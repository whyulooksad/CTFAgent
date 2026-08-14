#!/usr/bin/env python3
"""
tests/test_master.py -- Master 端到端测试 (mock 平台 + Fake 后端，不依赖 codex/hermes)。

覆盖:
  1. 优先级排序单测 (3 道 mock 题期望序: easy-misc > mid-web > mid-crypto)
  2. e2e: 3 题正常解出并自动提交 / 错误 flag 低价值不重试 /
     超时高价值重试一次 / max_challenges 去重计数 / web 靶机释放

真实 codex 链路 (ProcessBackend + mock 题) 的验证请在 WSL 上手动跑:
  python3 master.py   (master_config.json: adapter=mock, backend=process)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from adapters.base import Challenge, SubmitResult
from adapters.mock import MOCK_FLAGS, MockAdapter
from challenge_state import FAILED, SUBMITTED_CORRECT, TIMEOUT, extract_flags
from master import Config, Master
from prioritizer import llm_order, rule_order
import prioritizer
from solver_pool import FakeBackend


def test_extract_flags() -> None:
    """真实冒烟跑出的三种噪音格式 + 基本格式。"""
    cases = {
        # 2026-08-14 冒烟实测: misc 题 codex 写法
        "- `flag{mock_easy_misc_welcome}`": "flag{mock_easy_misc_welcome}",
        # crypto 题 codex 写法
        "- flag{mock_xor_is_easy}": "flag{mock_xor_is_easy}",
        # web 题 codex 写法 (带来源注释)
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
    assert extract_flags("## Flags Found\n(无)\n") == []
    assert extract_flags("## Flags Found\n<!-- 进度笔记 -->\n") == []
    assert extract_flags("## Flags Found\nflag{a}\n\n## Next\nx") == ["flag{a}"]
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
    order = [c.id for c in rule_order(MockAdapter().list_challenges())]
    expected = ["mock-easy-misc", "mock-mid-web", "mock-mid-crypto"]
    assert order == expected, f"优先级排序错误: {order} != {expected}"
    print(f"[PASS] rule_order: {order}")


def test_e2e() -> None:
    state_file = SCRIPT_DIR / "tests" / "master_state_test.json"
    log_file = SCRIPT_DIR / "tests" / "master_test.log"
    for p in (state_file, log_file):
        p.unlink(missing_ok=True)

    cfg = Config(
        adapter="mock",
        backend="fake",
        max_solvers=2,
        max_challenges=5,
        solver_timeout=6,
        poll_interval=0.5,
        submit_min_interval=0.2,
        llm_priority=False,   # e2e 不真调 codex
        state_file=str(state_file),
        log_file=str(log_file),
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
    os.environ["CODEX_CMD"] = str(SCRIPT_DIR / "tests" / "fake_codex_llm.sh")

    # 1. fake codex 倒序输出合法 JSON -> 采用
    os.environ["FAKE_MODE"] = "ok"
    prioritizer._llm_cache.clear()
    got = [r.id for r in llm_order(rule)]
    assert got == ["t3", "t2", "t1"], f"有效重排未被采用: {got}"

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

    # 4. 同候选集命中缓存 (不再调用 codex，garbage 模式下仍返回倒序)
    os.environ["FAKE_MODE"] = "ok"
    prioritizer._llm_cache.clear()
    assert [r.id for r in llm_order(rule)] == ["t3", "t2", "t1"]
    os.environ["FAKE_MODE"] = "garbage"   # 缓存应生效，不受影响
    assert [r.id for r in llm_order(rule)] == ["t3", "t2", "t1"]

    del os.environ["CODEX_CMD"], os.environ["FAKE_MODE"]
    print("[PASS] llm_order")


def test_dashboard() -> None:
    """面板 API 冒烟: overview / pause / config / 404。"""
    import json as jsonlib
    import urllib.error
    import urllib.request
    from master_dashboard import start_dashboard

    # 绕过系统代理 (http_proxy 会把 127.0.0.1 也代理掉)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    cfg = Config(
        adapter="mock", backend="fake", llm_priority=False,
        state_file=str(SCRIPT_DIR / "tests" / "master_state_test.json"),
        log_file=str(SCRIPT_DIR / "tests" / "master_test.log"),
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

        # 404
        try:
            opener.open(f"{base}/api/nope")
            raise AssertionError("应返回 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()
    print("[PASS] dashboard")


if __name__ == "__main__":
    test_extract_flags()
    test_rule_order()
    test_llm_order()
    test_dashboard()
    test_e2e()
    print("ALL PASS")
