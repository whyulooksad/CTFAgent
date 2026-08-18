#!/usr/bin/env python3
"""
tests/test_master.py -- Master 端到端测试 (mock 平台 + Fake 后端，不依赖 claude/hermes)。

覆盖:
  1. 优先级排序单测 (3 道 mock 题期望序: easy-misc > mid-web > mid-crypto)
  2. e2e: 3 题正常解出并自动提交 / 错误 flag 低价值不重试 /
     超时高价值重试一次 / max_challenges 去重计数 / web 靶机释放

真实 claude 链路 (ProcessBackend + mock 题) 的验证请在 WSL 上手动跑:
  python3 master.py   (master_config.json: adapter=mock, backend=process)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent   # 仓库根
sys.path.insert(0, str(SCRIPT_DIR / "master"))

from adapters.base import Challenge, SubmitResult
from adapters.manual import NoPlatformAdapter
from adapters.mock import MOCK_FLAGS, MockAdapter
from challenge_state import FAILED, QUEUED, SUBMITTED_CORRECT, TIMEOUT, extract_flags
from master import Config, Master
from prioritizer import llm_order, rule_order
import prioritizer
from solver_pool import FakeBackend


def test_extract_flags() -> None:
    """真实冒烟跑出的三种噪音格式 + 基本格式。"""
    cases = {
        # 2026-08-14 冒烟实测: misc 题解题 Agent 写法
        "- `flag{mock_easy_misc_welcome}`": "flag{mock_easy_misc_welcome}",
        # crypto 题解题 Agent 写法
        "- flag{mock_xor_is_easy}": "flag{mock_xor_is_easy}",
        # web 题解题 Agent 写法 (带来源注释)
        "- flag{mock_web_hidden}（来源：首页 HTML 注释）": "flag{mock_web_hidden}",
        # 干净格式
        "flag{abc_123}": "flag{abc_123}",
        "CTF{xyz}": "CTF{xyz}",
        # 非 flag{} 前缀: 回退清理后的原始行 (不猜格式)
        "- `SCTF{exotic_format}`": "SCTF{exotic_format}",
    }
    for raw, expected in cases.items():
        got = extract_flags(f"## Flags Found\n{raw}\n")
        assert got == [expected], f"{raw!r} -> {got} != [{expected!r}]"

    # 2026-08-15 ezssti 事故实测: recon 阶段的进度笔记被误当 flag (假闭环+误杀 solver)
    noise_lines = [
        "- 2026-08-15: 已按要求完整读取浏览器技能说明、Web 攻击流程、board.md 与 progress.md，准备基于现有状态继续侦察。",
        "2026-08-15: recon complete, continuing.",
        "已读取全部文件，准备开始侦察。",
        "- Progress: scanning target, 30% done.",
        "- 正在分析附件，稍后更新。",
    ]
    for noise in noise_lines:
        got = extract_flags(f"## Flags Found\n(无)\n\n{noise}\n")
        assert got == [], f"进度笔记被误判为 flag: {noise!r} -> {got}"

    assert extract_flags("## Flags Found\n(无)\n") == []
    assert extract_flags("## Flags Found\n<!-- 进度笔记 -->\n") == []
    assert extract_flags("## Flags Found\nflag{a}\n\n## Next\nx") == ["flag{a}"]
    # 笔记与真 flag 混排: 只取 flag
    assert extract_flags("## Flags Found\n- 2026-08-15: recon done\n- flag{real_one}\n") == ["flag{real_one}"]
    print("[PASS] extract_flags")


class TestAdapter(MockAdapter):
    """扩展 2 道测异常路径的题 (title 标记驱动 FakeBackend 行为)。"""

    def list_challenges(self) -> list[Challenge]:
        return super().list_challenges() + [
            Challenge(
                id="t-wrong",
                title="[wrong] 假flag题",
                type="misc",
                score=50,
                solve_count=100,
                description="会产出一个错误 flag，测错误提交路径",
            ),
            Challenge(
                id="t-fail-hard",
                title="[fail] 高分难题",
                type="misc",
                score=500,
                solve_count=2,
                description="永远解不出来，测超时与高价值重试",
            ),
        ]

    def submit(self, cid: str, flag: str) -> SubmitResult:
        if cid == "t-wrong":
            return SubmitResult("wrong", "test: 恒定错误")
        return super().submit(cid, flag)


def test_rule_order() -> None:
    """规则层 v2 (效用分): mock 三题 期望序 mid-web(400分medium) > easy-misc(100分easy) > mid-crypto(300分medium)。"""
    order = [c.id for c in rule_order(MockAdapter().list_challenges())]
    expected = ["mock-mid-web", "mock-easy-misc", "mock-mid-crypto"]
    assert order == expected, f"优先级排序错误: {order} != {expected}"
    print(f"[PASS] rule_order: {order}")


def test_rule_layer_v2() -> None:
    """
    规则层 v2 专项 (2026-08-18 真机复盘后重构):
      ① 多 flag 夹层: easy单 > medium单 > medium多flag > hard单
         (既不像旧 ÷4 沉底，也绝不会跳到 easy 前面 —— b 系跳队事故的反向保障)
      ② 系列学习: a 系历史 0/3 连败 -> 同条件新题排在无历史系之后
      ③ 分层超时: easy≈22min / hard6flag=75min 封顶 / 未知难度=全局配置
      ④ 重试 v2: 高分重试、多 flag 部分进度重试、低分单 flag 不重试
    """
    import tempfile
    from prioritizer import SOLVE_PRIOR, TIME_MIN

    # ① 多 flag 夹层 (同分 200)
    def ch(cid, diff, fc=1, score=200):
        return Challenge(id=cid, title=cid, type="web", score=score,
                         difficulty=diff, flag_count=fc)
    cands = [ch("m-1", "medium", 4), ch("e-1", "easy"), ch("h-1", "hard"), ch("m-2", "medium")]
    order = [c.id for c in rule_order(cands)]
    assert order == ["e-1", "m-2", "m-1", "h-1"], order
    print("[PASS] rule_layer_v2 ①多flag夹层:", order)

    # ② 系列学习: 全量记录里 a 系 0/3 连败
    class Rec:  # duck-typing 模拟历史记录
        def __init__(self, cid, attempts, solved):
            self.id, self.attempts = cid, attempts
            self.status = "submitted_correct" if solved else "failed"
            self.flags_correct = 1 if solved else 0
    history = [Rec("a-01", 1, False), Rec("a-02", 1, False), Rec("a-03", 1, False)]
    fresh = [ch("a-04", "medium"), ch("c-01", "medium")]
    order = [c.id for c in rule_order(fresh, all_records=fresh + history)]
    assert order == ["c-01", "a-04"], order   # a 系被降权到 c 系之后
    print("[PASS] rule_layer_v2 ②系列学习:", order)

    # ③ 分层超时
    from master import Master, Config as MConfig
    cfgx = MConfig(adapter="mock", backend="fake", solver_timeout=3600,
                   state_file=str(SCRIPT_DIR / "tests" / "master_state_rl.json"),
                   dashboard_port=0)
    mx = Master(cfgx, adapter=MockAdapter(), backend=FakeBackend())
    assert mx._solver_timeout_for(ch("x", "easy")) == int(15 * 1.5 * 60)      # 22.5min
    assert mx._solver_timeout_for(ch("x", "hard", 6)) == 75 * 60              # 封顶
    assert mx._solver_timeout_for(ch("x", "medium", 4)) == 75 * 60            # 99->75
    assert mx._solver_timeout_for(ch("x", "")) == 3600                        # 未知=全局
    print("[PASS] rule_layer_v2 ③分层超时")

    # ④ 重试 v2
    from challenge_state import retry_eligible, ChallengeRecord
    r_hi = ChallengeRecord(id="hi", title="t", type="web", score=500)
    r_lo = ChallengeRecord(id="lo", title="t", type="web", score=50)
    r_multi = ChallengeRecord(id="mf", title="t", type="web", score=100,
                              flag_count=4, flags_correct=1)
    r_multi0 = ChallengeRecord(id="mf0", title="t", type="web", score=100, flag_count=4)
    pool = [r_hi, r_lo, r_multi, r_multi0]
    assert retry_eligible(r_hi, pool, 0.6) is True            # 高分
    assert retry_eligible(r_lo, pool, 0.6) is False           # 低分单 flag
    assert retry_eligible(r_multi, pool, 0.6) is True         # 多 flag 部分进度
    assert retry_eligible(r_multi0, pool, 0.6) is False       # 多 flag 零进度
    print("[PASS] rule_layer_v2 ④重试规则")

    # ⑤ 提交上限 flag 感知: 6 flag 题允许 8 次提交 (全部 flag + 2 试错); 单 flag 仍 3 次
    from challenge_state import MasterState
    st_path = SCRIPT_DIR / "tests" / "master_state_cap.json"
    st_path.unlink(missing_ok=True)
    ms = MasterState(st_path, max_submit_per_challenge=3)
    rec6 = ms.sync_challenge(ChallengeRecord(id="b-x", title="t", type="web", flag_count=6))
    assert rec6.flag_count == 6                   # 新记录首同步就要带上 flag_count
    for i in range(8):
        rec6.submit_count = i
        assert ms.can_submit("b-x") is True, f"第 {i+1} 次提交应被允许 (flag×6)"
    rec6.submit_count = 8
    assert ms.can_submit("b-x") is False          # 6 flag + 2 试错用尽
    rec1 = ms.sync_challenge(ChallengeRecord(id="s-x", title="t", type="web"))  # 单 flag 行为不变
    assert rec1.flag_count == 1
    rec1.submit_count = 3
    assert ms.can_submit("s-x") is False
    rec1.submit_count = 2
    assert ms.can_submit("s-x") is True
    st_path.unlink(missing_ok=True)
    print("[PASS] rule_layer_v2 ⑤提交上限flag感知")
    st_path.unlink(missing_ok=True)
    print("[PASS] rule_layer_v2 ⑤提交上限flag感知")


def test_multiflag_no_loop() -> None:
    """
    回归: 多 flag 题死循环事故 (2026-08-16 b 系列)。

    事故链: flag correct 未通关 -> recycle 清 flags_seen -> solver 重跑解出同一
    flag -> 再提交 -> 平台 duplicate -> 被当 correct -> 又 recycle -> 死循环，
    3 槽位被占满，新题永远进不来。

    修复断言: ① duplicate 不计分不回收 ② flags_seen 不清空(同 flag 不再提交)
    ③ recycle 后 hint 含已得 flag ④ 死循环切断后能腾出槽位分发新题。
    """
    import threading

    class MultiFlagAdapter(MockAdapter):
        """一道 3 flag 的题: solver 陆续解出三个 flag (模拟同一会话持续攻坚)。"""

        def list_challenges(self) -> list[Challenge]:
            return super().list_challenges() + [
                Challenge(id="t-multi", title="多flag题", type="misc", score=500,
                          solve_count=10, flag_count=3,
                          description="3 个 flag"),
            ]

        def submit(self, cid: str, flag: str) -> SubmitResult:
            if cid == "t-multi" and flag in ("flag{first}", "flag{second}", "flag{third}"):
                if flag in self._submitted_ok:
                    return SubmitResult("correct", "duplicate: 已计过分",
                                        data={"duplicate": True,
                                              "correct_flag_count": len(self._submitted_ok),
                                              "total_flag_count": 3})
                self._submitted_ok.append(flag)
                return SubmitResult("correct", f"+100 ({flag})",
                                    data={"correct_flag_count": len(self._submitted_ok),
                                          "total_flag_count": 3})
            return super().submit(cid, flag)

        _submitted_ok: list = []

    # FakeBackend 变体: t-multi 的 solver 不退出，每 0.6s 依次解出一个 flag
    class RepeatBackend(FakeBackend):
        def _simulate(self, ch, handle):
            if ch.id == "t-multi":
                ev = handle.opaque["stop_event"]
                handle.work_dir.mkdir(parents=True, exist_ok=True)
                for i, flag in enumerate(("flag{first}", "flag{second}", "flag{third}")):
                    if ev.wait(0.6):
                        return
                    prev = []
                    f = handle.work_dir / "progress.md"
                    if f.exists():
                        prev = [l for l in f.read_text().splitlines()
                                if l.startswith("flag{")]
                    f.write_text("## Flags Found\n" + "\n".join(prev + [flag]) + "\n",
                                 encoding="utf-8")
                return
            super()._simulate(ch, handle)

    state_file = SCRIPT_DIR / "tests" / "master_state_mf.json"
    log_file = SCRIPT_DIR / "tests" / "master_mf.log"
    flags_file = SCRIPT_DIR / "tests" / "master_flags_mf.jsonl"
    for p in (state_file, log_file, flags_file):
        p.unlink(missing_ok=True)

    cfg = Config(
        adapter="mock", backend="fake", llm_priority=False,
        max_solvers=1, max_challenges=10,   # 单槽: 死循环会永远饿死其他题
        poll_interval=0.3, solver_timeout=60,
        submit_min_interval=0.2, max_submit_per_challenge=3,
        state_file=str(state_file), log_file=str(log_file), flags_file=str(flags_file),
    )
    # 直连构造 Master 不经过 CLI 的 logging 配置，手动挂 FileHandler
    import logging
    root = logging.getLogger("master")
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(fh)
    m = Master(cfg, adapter=MultiFlagAdapter(), backend=RepeatBackend())
    t = threading.Thread(target=m.run, daemon=True)
    t.start()
    time.sleep(10)
    m._stop.set()
    t.join(timeout=6)

    rec = m.state.get("t-multi")
    log_text = log_file.read_text(encoding="utf-8")
    # ① 多 flag 全通关: 3 个 flag 各提交一次，状态终态
    assert rec.status == SUBMITTED_CORRECT, \
        f"应通关: {rec.status}, correct={rec.flags_correct}"
    assert rec.flags_correct == 3, rec.flags_correct
    assert sorted(rec.flags_submitted) == ["flag{first}", "flag{second}", "flag{third}"]
    # ② 无重复提交 (flags_seen 保留)
    for f in ("flag{first}", "flag{second}", "flag{third}"):
        assert rec.flags_submitted.count(f) == 1
    # ③ solver 未通关期间不被回收 (日志有"继续攻剩余")
    assert "solver 存活，继续攻剩余 flag" in log_text
    # ④ duplicate 防御分支 (状态恢复等场景): 不计分、不回收
    m.submitter._results.put({"cid": "t-multi", "flag": "flag{first}",
                              "status": "correct", "message": "dup",
                              "data": {"duplicate": True}})
    m._drain_results()
    log_text = log_file.read_text(encoding="utf-8")
    assert "重复 flag 已计过分" in log_text, "duplicate 未被识别"
    rec = m.state.get("t-multi")
    assert rec.flags_correct == 3, "duplicate 计了分"
    assert rec.status == SUBMITTED_CORRECT, "duplicate 触发了回收"
    print("[PASS] multiflag-no-loop")


def test_platform_boot_wait() -> None:
    """
    回归: 平台靶机预检不再误杀 (2026-08-16 下午事故)。

    事故链: 平台 start 返回地址时容器仍在启动 -> 5s 预检失败被误判"不可达"
    -> 终态 FAILED + close 刚开的容器 -> close 超时泄漏平台槽位 -> 腾讯侧
    3 容器、master 侧无 solver 错位 + 后续 start 撞 409 max-active。

    修复断言: 平台题预检失败 -> 冷却重试(不终态/不释放)；就绪后正常分发；
    连续 8 次不就绪才判死。手动题仍立即终态。
    """
    state_file = SCRIPT_DIR / "tests" / "master_state_boot.json"
    log_file = SCRIPT_DIR / "tests" / "master_boot.log"
    for p in (state_file, log_file):
        p.unlink(missing_ok=True)

    cfg = Config(
        adapter="mock", backend="fake", llm_priority=False,
        max_solvers=3, max_challenges=10, poll_interval=0.3, solver_timeout=60,
        state_file=str(state_file), log_file=str(log_file),
        flags_file=str(SCRIPT_DIR / "tests" / "master_flags_boot.jsonl"),
    )
    m = Master(cfg, adapter=MockAdapter(),
               backend=FakeBackend(flag_lookup=MOCK_FLAGS.get, solve_delay=0.2))

    web = m.state.sync_challenge(
        [c for c in MockAdapter().list_challenges() if c.type == "web"][0])
    m.state.set_status(web.id, QUEUED)
    web.attempts = 1   # 模拟重试分发 (预检只在 attempts>=1 时介入，与事故场景一致)

    # 模拟容器启动窗口: 前 2 次预检不可达，第 3 次就绪
    seq = {"n": 0}
    orig_alive = Master._target_alive
    def flaky_alive(url):
        seq["n"] += 1
        return seq["n"] > 2
    Master._target_alive = staticmethod(flaky_alive)
    try:
        m._dispatch(web)   # 第1次: 未就绪 -> 冷却 (QUEUED，不终态)
        assert web.status == QUEUED and web.boot_fails == 1, (web.status, web.boot_fails)
        assert m.running == {}, f"冷却期不应有 solver: {list(m.running)}"
        m._dispatch(web)   # 第2次: 仍未就绪 -> 冷却
        assert web.boot_fails == 2 and web.status == QUEUED
        m._dispatch(web)   # 第3次: 就绪 -> 正常分发
        assert web.id in m.running, f"就绪后应分发: {web.status} boot_fails={web.boot_fails}"
        h = m.running[web.id]
        time.sleep(1.0)    # FakeBackend 秒解
        # 就绪即清计数
        assert web.boot_fails == 0
        m.submitter.start()   # 直连构造 Master 需手动起提交线程
        m.submitter.submit(web.id, MOCK_FLAGS[web.id])
        time.sleep(1.5)
        m._drain_results()
        assert m.state.get(web.id).status == SUBMITTED_CORRECT
        m.submitter.stop()
    finally:
        Master._target_alive = orig_alive

    # 手动题语义不变: 不可达立即终态
    man = m._build_manual_challenge({"type": "web", "url": "http://127.0.0.1:9", "title": "x"})
    m.manual_adapter.add(man)
    rec2 = m.state.sync_challenge(man)
    Master._target_alive = staticmethod(lambda url: False)
    try:
        m._dispatch(rec2)
        assert rec2.status == FAILED, f"手动题应立即终态: {rec2.status}"
    finally:
        Master._target_alive = orig_alive
    print("[PASS] platform-boot-wait")


def test_e2e() -> None:
    state_file = SCRIPT_DIR / "tests" / "master_state_test.json"
    log_file = SCRIPT_DIR / "tests" / "master_test.log"
    flags_file = SCRIPT_DIR / "tests" / "master_flags_test.jsonl"
    for p in (state_file, log_file, flags_file):
        p.unlink(missing_ok=True)

    cfg = Config(
        adapter="mock",
        backend="fake",
        max_solvers=2,
        max_challenges=5,
        solver_timeout=6,
        poll_interval=0.5,
        submit_min_interval=0.2,
        llm_priority=False,   # e2e 不真调 claude
        state_file=str(state_file),
        log_file=str(log_file),
        flags_file=str(flags_file),
    )
    adapter = TestAdapter()
    backend = FakeBackend(flag_lookup=MOCK_FLAGS.get, solve_delay=0.5)
    m = Master(cfg, adapter=adapter, backend=backend)

    t0 = time.time()
    m.run()
    elapsed = time.time() - t0
    assert elapsed < 120, f"e2e 耗时异常: {elapsed:.1f}s"

    recs = {r.id: r for r in m.state.all_records()}
    assert set(recs) == {
        "mock-easy-misc", "mock-mid-crypto", "mock-mid-web", "t-wrong", "t-fail-hard",
    }, f"题目集合不符: {set(recs)}"

    # 3 道 mock 题全部解出且 flag 正确
    for cid, expected_flag in MOCK_FLAGS.items():
        r = recs[cid]
        assert r.status == SUBMITTED_CORRECT, f"{cid}: {r.status} != submitted_correct"
        assert r.flag == expected_flag, f"{cid}: {r.flag} != {expected_flag}"

    # 错误 flag: 低价值题 (value=0.1, rarity=0.5) 不重试
    r = recs["t-wrong"]
    assert r.status == FAILED and r.attempts == 1, \
        f"t-wrong: {r.status} attempts={r.attempts}"
    assert r.last_submit_status == "wrong", r.last_submit_status
    # 假 flag 处理: progress.md 已当场清除 + human_guidance.md 有 [master] 通知
    wd = Path(recs["t-wrong"].work_dir)
    prog = (wd / "progress.md").read_text(encoding="utf-8")
    assert "flag{wrong_t-wrong}" not in prog, "假 flag 未从 progress.md 清除"
    hg = (wd / "human_guidance.md").read_text(encoding="utf-8")
    assert "[master" in hg and "flag{wrong_t-wrong}" in hg, "hermes 未收到假 flag 通知"
    assert "dead_ends" in hg

    # 高价值难题 (value=1.0): 超时重试一次后终态
    r = recs["t-fail-hard"]
    assert r.status == TIMEOUT and r.attempts == 2, \
        f"t-fail-hard: {r.status} attempts={r.attempts}"

    # 题目数上限按去重计
    assert m.state.distinct_attempted() == 5

    # web 靶机已释放
    assert not adapter._servers, f"靶机泄漏: {adapter._servers}"

    assert not m.running
    print(f"[PASS] e2e ({elapsed:.1f}s)")


def test_llm_order() -> None:
    """LLM 软修正三条路径: 有效重排 / 非法输出回退 / 调用失败回退。"""
    import os
    recs = [
        Challenge(id=f"t{i}", title=f"题{i}", type="misc", score=100 * i, solve_count=10 * i)
        for i in (1, 2, 3)
    ]
    rule = rule_order(recs)
    rule_ids = [r.id for r in rule]
    # fake claude 把 prompt 里的 id 顺序倒过来输出 (与输入顺序不同的合法排列)
    reversed_ids = list(reversed(rule_ids))
    os.environ["CLAUDE_CMD"] = str(SCRIPT_DIR / "tests" / "fake_claude_llm.sh")

    # 1. fake claude 倒序输出合法 JSON -> 采用
    os.environ["FAKE_MODE"] = "ok"
    prioritizer._llm_cache.clear()
    got = [r.id for r in llm_order(rule)]
    assert got == reversed_ids, f"有效重排未被采用: {got} != {reversed_ids}"

    # 2. 输出非 JSON -> 回退规则序
    os.environ["FAKE_MODE"] = "garbage"
    prioritizer._llm_cache.clear()
    got = [r.id for r in llm_order(rule)]
    assert got == rule_ids, f"非法输出未回退: {got}"

    # 3. 调用失败 -> 回退规则序
    os.environ["FAKE_MODE"] = "fail"
    prioritizer._llm_cache.clear()
    got = [r.id for r in llm_order(rule)]
    assert got == rule_ids, f"调用失败未回退: {got}"

    # 4. 同候选集命中缓存 (不再调用 claude，garbage 模式下仍返回重排结果)
    os.environ["FAKE_MODE"] = "ok"
    prioritizer._llm_cache.clear()
    assert [r.id for r in llm_order(rule)] == reversed_ids
    os.environ["FAKE_MODE"] = "garbage"   # 缓存应生效，不受影响
    assert [r.id for r in llm_order(rule)] == reversed_ids

    del os.environ["CLAUDE_CMD"], os.environ["FAKE_MODE"]
    print("[PASS] llm_order")


def test_dashboard() -> None:
    """面板 API 冒烟: overview / pause / config / 404。"""
    import json as jsonlib
    import urllib.error
    import urllib.request
    import llm_config
    from master_dashboard import start_dashboard

    # 绕过系统代理 (http_proxy 会把 127.0.0.1 也代理掉)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    cfg = Config(
        adapter="mock", backend="fake", llm_priority=False,
        state_file=str(SCRIPT_DIR / "tests" / "master_state_test.json"),
        log_file=str(SCRIPT_DIR / "tests" / "master_test.log"),
        flags_file=str(SCRIPT_DIR / "tests" / "master_flags_test.jsonl"),
    )
    m = Master(cfg, adapter=MockAdapter(),
               backend=FakeBackend(flag_lookup=MOCK_FLAGS.get, solve_delay=999))
    server, port = start_dashboard(m, 0)
    base = f"http://127.0.0.1:{port}"
    try:
        # overview (空状态)
        with opener.open(f"{base}/api/overview") as r:
            d = jsonlib.loads(r.read())
        assert d["running"] == 0 and d["challenges"] == []

        # 手工塞一条记录再查
        m.state.sync_challenge(m.adapter.list_challenges()[0])
        with opener.open(f"{base}/api/overview") as r:
            d = jsonlib.loads(r.read())
        assert len(d["challenges"]) == 1 and d["challenges"][0]["id"] == "mock-easy-misc"
        # llm 状态含 hermes 热切换字段 (key 掩码)
        assert "hermes_provider" in d["llm"] and "hermes_configured" in d["llm"]
        st = llm_config.status({"platform": "p", "base_url": "https://x", "api_key": "sk-abcdefgh1234",
                                "model": "m", "hermes_provider": "deepseek",
                                "hermes_base_url": "https://api.deepseek.com",
                                "hermes_api_key": "", "hermes_model": ""})
        assert st["hermes_configured"] and st["hermes_api_key"] == "sk-***1234", st

        # pause / resume
        req = urllib.request.Request(f"{base}/api/pause", data=b"{}", method="POST")
        assert jsonlib.loads(opener.open(req).read())["paused"] is True
        assert m.paused
        req = urllib.request.Request(f"{base}/api/resume", data=b"{}", method="POST")
        assert jsonlib.loads(opener.open(req).read())["paused"] is False

        # config
        body = jsonlib.dumps({"max_solvers": 3, "max_challenges": 7}).encode()
        req = urllib.request.Request(f"{base}/api/config", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        d = jsonlib.loads(opener.open(req).read())
        assert d["max_solvers"] == 3 and d["max_challenges"] == 7
        assert m.cfg.max_solvers == 3

        # flags 接口 (空)
        with opener.open(f"{base}/api/flags") as r:
            d = jsonlib.loads(r.read())
        assert d == {"flags": []}

        # 待命启动 (standby_start): 启动后不调度，面板「接入」赛方题目平台才开始
        cfg2 = Config(
            adapter="mock", backend="fake", llm_priority=False, dashboard_port=0,
            standby_start=True, resident=False,
            state_file=str(SCRIPT_DIR / "tests" / "master_state_test.json"),
            log_file=str(SCRIPT_DIR / "tests" / "master_test.log"),
            flags_file=str(SCRIPT_DIR / "tests" / "master_flags_test.jsonl"),
        )
        m2 = Master(cfg2, adapter=NoPlatformAdapter(), backend=FakeBackend())

        # 假赛方题目平台 (patch LiveAdapter，不真发请求)
        import adapters.live as live_mod
        class FakePlatformAdapter:
            def __init__(self, base_url, token=""):
                self.base_url, self.token = base_url, token
            def list_challenges(self):
                return [Challenge(id="p-1", title="平台题", type="misc",
                                  score=100, solve_count=1)]
        real_live = live_mod.LiveAdapter
        live_mod.LiveAdapter = FakePlatformAdapter
        server2 = None
        try:
            from master_dashboard import start_dashboard as _sd
            server2, port2 = _sd(m2, 0)
            base2 = f"http://127.0.0.1:{port2}"
            with opener.open(f"{base2}/api/overview") as r:
                d = jsonlib.loads(r.read())
            assert d["standby"] is True
            assert m2._should_exit() is False, "待命中不应退出"

            body = jsonlib.dumps({"platform": "TestPlat", "base_url": "https://x/api",
                                  "api_key": "sk-test12345678"}).encode()
            req = urllib.request.Request(f"{base2}/api/connect-platform", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            d = jsonlib.loads(opener.open(req).read())
            assert d["api_key"] == "sk-***5678", d   # 掩码，绝不回传明文
            assert d["challenges"] == 1
            assert m2.standby is False, "接入赛方平台后应解除待命"
            assert m2.platform_connected and m2.state.get("p-1") is not None

            # 状态文件按 (平台, api_key) 作用域: master_state_<平台>_<sha256(key)[:8]>.json
            import hashlib
            sfx = "TestPlat_" + hashlib.sha256(b"sk-test12345678").hexdigest()[:8]
            assert m2.state.path.name == f"master_state_{sfx}.json", m2.state.path
            assert m2.flags_file.name == f"flags_{sfx}.jsonl"
            # 同 key 重连: 作用域不变 (幂等)
            opener.open(urllib.request.Request(
                f"{base2}/api/connect-platform", data=body, method="POST",
                headers={"Content-Type": "application/json"}))
            assert m2.state.path.name == f"master_state_{sfx}.json"
            # 换 key: 切到全新作用域 (测试目录内)，旧进度不带入
            m2.state.set_status("p-1", "submitted_correct")
            m2.state.get("p-1").attempts = 1
            body2 = jsonlib.dumps({"platform": "TestPlat", "base_url": "https://x/api",
                                   "api_key": "sk-other99999"}).encode()
            opener.open(urllib.request.Request(
                f"{base2}/api/connect-platform", data=body2, method="POST",
                headers={"Content-Type": "application/json"}))
            sfx2 = "TestPlat_" + hashlib.sha256(b"sk-other99999").hexdigest()[:8]
            assert m2.state.path.name == f"master_state_{sfx2}.json"
            r = m2.state.get("p-1")  # 平台重列的题进新作用域，但是全新进度
            assert r is not None and r.attempts == 0 and r.status == "queued", (r,)
            # submitter 线程的 state 引用同步切换
            assert m2.submitter.state is m2.state
            # 清理作用域测试产物
            for p in m2.state.path.parent.glob(f"master_state_{sfx}*.json"):
                p.unlink(missing_ok=True)
            for p in m2.state.path.parent.glob(f"flags_{sfx}*.jsonl"):
                p.unlink(missing_ok=True)

            # 非法输入: 空 base_url / 空 api_key
            for bad in ({"base_url": "", "api_key": "sk-x"},
                        {"base_url": "https://x", "api_key": ""}):
                req = urllib.request.Request(
                    f"{base2}/api/connect-platform",
                    data=jsonlib.dumps(bad).encode(), method="POST",
                    headers={"Content-Type": "application/json"})
                try:
                    opener.open(req)
                    raise AssertionError(f"非法输入应 400: {bad}")
                except urllib.error.HTTPError as e:
                    assert e.code == 400
        finally:
            live_mod.LiveAdapter = real_live
            if server2:
                server2.shutdown()

        # 404
        try:
            opener.open(f"{base}/api/nope")
            raise AssertionError("应返回 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()
    print("[PASS] dashboard")


def test_manual_and_resident() -> None:
    """手动加题 + 上限豁免 + 常驻不退出 + 手动模式 flag 仅展示不提交 + flags.jsonl 落盘。"""
    import threading
    state_file = SCRIPT_DIR / "tests" / "master_state_manual.json"
    for p in (state_file, SCRIPT_DIR / "tests" / "master_test.log",
              SCRIPT_DIR / "tests" / "master_flags_test.jsonl"):
        p.unlink(missing_ok=True)

    cfg = Config(
        adapter="none", backend="fake", llm_priority=False,
        resident=True, dashboard_port=0,
        max_solvers=2, max_challenges=1,   # 上限 1，但手动题不受限
        poll_interval=0.3, solver_timeout=5,
        submit_min_interval=0.2,
        state_file=str(state_file),
        log_file=str(SCRIPT_DIR / "tests" / "master_test.log"),
        flags_file=str(SCRIPT_DIR / "tests" / "master_flags_test.jsonl"),
    )
    m = Master(cfg, backend=FakeBackend(flag_lookup=lambda cid: f"flag{{manual_{cid}}}",
                                        solve_delay=0.3))
    assert m.platform_connected is False  # adapter=none

    # 手动 web 题的靶机: 起一个真实本地 http server (分发前有存活预检)
    import http.server
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                          lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, **kw))
    import threading as _th
    _th.Thread(target=srv.serve_forever, daemon=True).start()
    target_url = f"http://127.0.0.1:{srv.server_address[1]}"

    # 手动加题: 合法 web + 合法 crypto 附件 + 死靶机 web + 两个非法条目
    added = m.add_manual_challenges([
        {"type": "web", "url": target_url, "title": "手动web", "description": "手动测试"},
        {"type": "crypto", "attachment": str(SCRIPT_DIR / "tests" / "mock_challenges" / "mid_crypto.zip"),
         "title": "手动crypto"},
        {"type": "web", "url": "http://127.0.0.1:9", "title": "死靶机"},  # 探活必失败
        {"type": "crypto", "attachment": "/nonexistent.zip"},
        {"type": "pwn"},
    ])
    assert len(added) == 3, f"应只入队 3 道: {added}"
    assert all(r.source == "manual" and r.status == QUEUED
               for r in m.state.all_records())

    # 常驻运行: 手动题全部被分发 (上限 1 不拦)，Fake 后端秒解
    t = threading.Thread(target=m.run, daemon=True)
    t.start()
    time.sleep(4)
    m._stop.set()
    t.join(timeout=6)
    recs = {r.id: r for r in m.state.all_records()}
    live = [r for r in recs.values() if "死靶机" not in r.id]
    for r in live:
        assert r.attempts == 1, (r.id, r.attempts, r.status)
    solved = [r for r in live if r.status == SUBMITTED_CORRECT]
    assert len(solved) == 2, f"手动题应全部闭环: {[(r.id, r.status) for r in recs.values()]}"
    # 手动模式不提交，flag 直接展示闭环
    assert all(r.last_submit_status == "manual_display" for r in solved), \
        [r.last_submit_status for r in solved]
    # 手动 web 题的 URL 即靶机
    web_rec = next(r for r in recs.values() if r.type == "web" and "手动web" in r.id)
    assert web_rec.url == target_url

    # 死靶机: 分发前探活失败 -> 立即 FAILED，不启动 solver，不重试 (bug 修复验证)
    dead_rec = next(r for r in recs.values() if "死靶机" in r.id)
    assert dead_rec.status == FAILED and dead_rec.attempts == 0, \
        (dead_rec.status, dead_rec.attempts)
    # flags.jsonl 落盘 (auto_submitted=False)
    flags = [json.loads(l) for l in
             Path(m.flags_file).read_text(encoding="utf-8").splitlines() if l.strip()]
    mine = [f for f in flags if any(r.id == f["cid"] for r in recs.values())]
    assert len(mine) == 2 and all(not f["auto_submitted"] for f in mine), mine
    # 常驻模式永不满退
    assert m._should_exit() is False
    print("[PASS] manual+resident")


def test_wrong_flag_and_terminate() -> None:
    """
    假 flag 处理 + 双死门 (引擎切换 claude code 后的新机制)。

    断言:
      ① wrong 提交结果 -> progress.md 的 Flags Found 段当场清掉假 flag
         (段空恢复 (无)) + human_guidance.md 落 [master] 通知 (hermes 消费写 dead_ends)
      ② 真 flag 不受影响，保留
      ③ _terminate: STOP 文件落盘; 未死透返回 False 不回收槽位; 死透才回收
    """
    import threading
    state_file = SCRIPT_DIR / "tests" / "master_state_wf.json"
    state_file.unlink(missing_ok=True)

    cfg = Config(
        adapter="mock", backend="fake", llm_priority=False, dashboard_port=0,
        state_file=str(state_file),
        log_file=str(SCRIPT_DIR / "tests" / "master_test.log"),
        flags_file=str(SCRIPT_DIR / "tests" / "master_flags_wf.jsonl"),
    )
    m = Master(cfg, adapter=MockAdapter(), backend=FakeBackend())
    wd = SCRIPT_DIR / "challenges" / "fake" / "t-wrongflag"
    wd.mkdir(parents=True, exist_ok=True)

    # ① 混排: 真 flag 保留 / 假 flag 清除
    (wd / "progress.md").write_text(
        "## Current Phase\nexploit\n\n## Flags Found\nflag{fake_one}\nflag{real_two}\n",
        encoding="utf-8")
    (wd / "human_guidance.md").write_text("", encoding="utf-8")
    rec = m.state.sync_challenge(Challenge(
        id="t-wrongflag", title="假flag", type="misc", score=10, solve_count=5))
    rec.work_dir = str(wd)
    m._on_wrong_flag(rec, "flag{fake_one}", "平台判错: not the flag")
    prog = (wd / "progress.md").read_text(encoding="utf-8")
    assert "flag{fake_one}" not in prog and "flag{real_two}" in prog
    hg = (wd / "human_guidance.md").read_text(encoding="utf-8")
    assert "[master" in hg and "flag{fake_one}" in hg and "dead_ends" in hg

    # ② 全删 -> 段空恢复 (无)
    m._on_wrong_flag(rec, "flag{real_two}", "也错了")
    prog = (wd / "progress.md").read_text(encoding="utf-8")
    assert "flag{real_two}" not in prog and "(无)" in prog

    # ③ 双死门: 先造一个"停了还没死透"的后端，再正常路径
    from solver_pool import SolverHandle

    class SlowDieBackend(FakeBackend):
        """第一次 stop 后 is_alive 仍返回 True (模拟 docker daemon 滞后)，第二次才死透。"""
        def __init__(self):
            super().__init__()
            self.stop_calls: dict = {}

        def stop(self, handle):
            self.stop_calls[handle.cid] = self.stop_calls.get(handle.cid, 0) + 1

        def is_alive(self, handle):
            n = self.stop_calls.get(handle.cid, 0)
            if n == 0:
                return True          # 还没收到 stop
            return n < 2             # 第 1 次停止后仍"存活" (滞后)，第 2 次起死透

    m2 = Master(cfg, adapter=MockAdapter(), backend=SlowDieBackend())
    wdx = SCRIPT_DIR / "challenges" / "fake" / "t-die"
    wdx.mkdir(parents=True, exist_ok=True)
    h = SolverHandle(cid="t-die", type="misc", work_dir=wdx, started_at=time.time())
    h.opaque["stop_event"] = threading.Event()
    h.opaque["thread"] = None
    m2.running["t-die"] = h
    ok = m2._terminate("t-die", "测试")
    assert ok is False, "未死透不应返回 True"
    assert "t-die" in m2.running, "未死透不应回收槽位"
    assert (wdx / "STOP").exists(), "STOP 文件未落盘"
    # 模拟 daemon 追上: 再查一次死了
    ok = m2._terminate("t-die", "测试")
    assert ok is True and "t-die" not in m2.running
    print("[PASS] wrong-flag + terminate")


if __name__ == "__main__":
    test_extract_flags()
    test_rule_order()
    test_rule_layer_v2()
    test_llm_order()
    test_dashboard()
    test_manual_and_resident()
    test_platform_boot_wait()
    test_multiflag_no_loop()
    test_wrong_flag_and_terminate()
    test_e2e()
    print("ALL PASS")
