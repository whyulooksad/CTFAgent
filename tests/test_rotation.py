#!/usr/bin/env python3
"""
轮转调度回归测试 (2026-08-20)

覆盖:
  A. 超时轮转: [fail] 题每圈超时 rotate, 圈数正常推进到 max_rounds, 不卡死
  B. 分发失败让路: 开靶机持续失败 3 次 → 本圈让路, 不阻塞轮转 (旧 bug 回归)
  C. _finalize 轮转语义: 失败/崩溃 → 本圈让路, 下圈再试, max_rounds 兜底

测试轮转参数: round_time_base=60, round_time_step=30, max_rounds=3
  (第 1 圈 60s / 第 2 圈 90s / 第 3 圈 120s — 线性公式 base+(round-1)*step)

用法: cd /home/stw/ctf-agent && python3 scripts/test_rotation.py
"""
from __future__ import annotations

import http.server
import logging
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "master"))

import master as M  # noqa: E402   (monkeypatch DISPATCH_COOLDOWN 用)
from adapters.base import Challenge, SubmitResult  # noqa: E402
from adapters.mock import MockAdapter  # noqa: E402
from master import Config, Master  # noqa: E402
from solver_pool import FakeBackend  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


class _RotHandler(http.server.BaseHTTPRequestHandler):
    """测试靶机: 任意 GET 返回 200 (靶机存活预检要能连上)。"""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"rot-test-ok")

    def log_message(self, *args):  # 静默
        pass


class TestAdapter(MockAdapter):
    """start_challenge 对指定 cid 永远抛异常 (模拟平台开靶机故障)。"""

    def __init__(self, fail_cids=(), multi_flag_cids=(), num_fail=1, include_normal=True,
                 pending_cids=(), full_cids=(), ok_web_cids=(), ok_tcp_cids=(),
                 ok_udp_cids=(), max_active=0):
        super().__init__()
        self.fail_cids = set(fail_cids)
        self.multi_flag_cids = set(multi_flag_cids)
        self.num_fail = num_fail
        self.include_normal = include_normal
        self.pending_cids = set(pending_cids)  # start 成功但容器未就绪 (URL 不可达)
        self.full_cids = set(full_cids)        # 平台 active 满 (max active 409)
        self.ok_web_cids = set(ok_web_cids)    # 正常 web 题 (start 成功 + 容器就绪)
        self.ok_tcp_cids = set(ok_tcp_cids)    # binary 题: 仅 TCP 端口开放 (HTTP 探测必失败)
        self.ok_udp_cids = set(ok_udp_cids)    # UDP 服务题: 仅 UDP 端口收包响应
        self.max_active = max_active           # 平台同时活跃实例上限 (0=不限制)
        self.start_calls: dict[str, int] = {}  # start_challenge 调用次数
        self._submit_count: dict[str, int] = {}  # 每题提交次数 (multi_flag 通关模拟)
        self.stop_calls: dict[str, int] = {}   # stop_challenge 调用次数
        self._started: dict[str, str] = {}
        self._servers: dict[str, http.server.ThreadingHTTPServer] = {}
        self._tcp_servers: dict[str, socket.socket] = {}
        self._udp_servers: dict[str, socket.socket] = {}
        self._next_port = 18000

    def list_challenges(self):
        chs = []
        if self.include_normal:
            chs += [
                Challenge(id="t1", title="正常题-1", type="crypto", score=300, solve_count=10,
                          description="测试正常题"),
                Challenge(id="t2", title="正常题-2", type="crypto", score=300, solve_count=10,
                          description="测试正常题"),
                Challenge(id="t3", title="正常题-3", type="crypto", score=300, solve_count=10,
                          description="测试正常题"),
                Challenge(id="f2", title="多flag题", type="crypto", score=400, solve_count=8,
                          flag_count=2, description="flag_count=2, 只产出 1 个 flag"),
            ]
        for i in range(1, self.num_fail + 1):
            chs.append(Challenge(id=f"fk{i}", title=f"卡死题-{i}[fail]", type="web",
                                 score=500, solve_count=5,
                                 description="永远不产出 flag, 测超时轮转"))
        # 场景 H 专用: pending (容器启动中) / full (平台满) 的 web 题
        for cid in sorted(self.pending_cids):
            chs.append(Challenge(id=cid, title=f"启动中题-{cid}", type="web",
                                 score=450, solve_count=8, description="容器未就绪"))
        for cid in sorted(self.full_cids):
            chs.append(Challenge(id=cid, title=f"平台满题-{cid}", type="web",
                                 score=500, solve_count=5, description="平台 active 满"))
        # 场景 J 专用: 正常可解的 web 题 (平台名额允许时 start 成功)
        for cid in sorted(self.ok_web_cids):
            chs.append(Challenge(id=cid, title=f"正常web题-{cid}", type="web",
                                 score=400, solve_count=7, description="正常 web"))
        # 场景 K 专用: binary 题 (仅 TCP 端口开放, HTTP 探测必然失败)
        for cid in sorted(self.ok_tcp_cids):
            chs.append(Challenge(id=cid, title=f"二进制题-{cid}", type="binary",
                                 score=400, solve_count=7, description="binary TCP 服务"))
        # 场景 K 专用: UDP 服务题 (仅 UDP 端口收包响应, HTTP/TCP 探测必然失败)
        for cid in sorted(self.ok_udp_cids):
            chs.append(Challenge(id=cid, title=f"UDP服务题-{cid}", type="binary",
                                 score=400, solve_count=7, description="UDP 服务"))
        return chs

    def start_challenge(self, cid: str) -> str:
        self.start_calls[cid] = self.start_calls.get(cid, 0) + 1
        if cid in self.fail_cids:
            raise RuntimeError("模拟平台开靶机失败 (409 资源不足)")
        if cid in self.full_cids:
            raise RuntimeError(
                "start 409 且容器不可用: HTTP 409: max active challenge instances reached (3)")
        # 场景 J: 模拟平台同时活跃实例上限 —— 已开实例数达到上限且本题未开过 → 409
        if self.max_active and cid not in self._started \
                and len(self._started) >= self.max_active:
            raise RuntimeError(
                "start 409 且容器不可用: HTTP 409: max active challenge instances reached "
                f"({self.max_active})")
        if cid in self.pending_cids:
            self._started[cid] = "http://127.0.0.1:1/"  # 不可达: 模拟容器启动中 (预检会失败)
            return self._started[cid]
        # binary 题: 仅监听 TCP 端口 (HTTP 探测失败, TCP connect 成功)
        if cid in self.ok_tcp_cids:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            threading.Thread(target=srv.accept, daemon=True).start()  # 接受首个连接即满足预检
            url = f"http://127.0.0.1:{srv.getsockname()[1]}/"
            self._tcp_servers[cid] = srv
            self._started[cid] = url
            return url
        # UDP 服务题: 仅 UDP 端口收包响应 (HTTP/TCP 探测失败, UDP recv 成功)
        if cid in self.ok_udp_cids:
            srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            srv.bind(("127.0.0.1", 0))
            port = srv.getsockname()[1]

            def _udp_echo(udp_srv=srv):
                try:
                    while True:
                        data, addr = udp_srv.recvfrom(512)
                        udp_srv.sendto(b"ok", addr)
                except OSError:
                    pass
            threading.Thread(target=_udp_echo, daemon=True).start()
            url = f"http://127.0.0.1:{port}/"
            self._udp_servers[cid] = srv
            self._started[cid] = url
            return url
        # 真实 HTTP 服务: 平台题重试时 _target_alive 预检必须能连上
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RotHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        self._servers[cid] = srv
        self._started[cid] = url
        return url

    def stop_challenge(self, cid: str) -> None:
        self.stop_calls[cid] = self.stop_calls.get(cid, 0) + 1
        srv = self._servers.pop(cid, None)
        if srv:
            srv.shutdown()
            srv.server_close()
        tsrv = self._tcp_servers.pop(cid, None)
        if tsrv:
            try:
                tsrv.close()
            except OSError:
                pass
        usrv = self._udp_servers.pop(cid, None)
        if usrv:
            try:
                usrv.close()
            except OSError:
                pass
        self._started.pop(cid, None)

    def submit(self, cid: str, flag: str) -> SubmitResult:
        if cid in self.multi_flag_cids:
            # 按提交次数递增: 第 1 次 1/2, 第 2 次 2/2 (通关) —— 测多 flag 完整闭环
            n = self._submit_count.get(cid, 0) + 1
            self._submit_count[cid] = n
            total = 2
            done = min(n, total)
            return SubmitResult("correct", f"测试平台: {done}/{total}", data={
                "total_flag_count": total, "correct_flag_count": done,
            })
        return SubmitResult("correct", "测试平台: 全接受")


def make_cfg(tag: str, **kw) -> Config:
    base = dict(
        adapter="mock",
        backend="fake",
        max_solvers=3,
        max_challenges=10,
        solver_timeout=600,
        # 单 flag 题圈超时 = base (系数 0.7 只作用于多 flag 题) → 60/30 即 60s/90s/120s
        round_time_base=60,
        round_time_step=30,
        max_rounds=3,
        poll_interval=1,
        llm_priority=False,
        submit_min_interval=0,
        max_submit_per_challenge=3,
        dashboard_port=0,
        resident=False,
        state_file=f"/tmp/rot_test_{tag}.json",
        log_file=f"/tmp/rot_test_{tag}.log",
        flags_file=f"/tmp/rot_test_{tag}_flags.jsonl",
    )
    base.update(kw)
    return Config(**base)


def setup_logging(cfg: Config) -> None:
    """每个场景独立日志文件 (force=True 替换上一场景的 handlers)。"""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.FileHandler(cfg.log_file, encoding="utf-8")],
        force=True,
    )


def run_master(cfg: Config, adapter, backend, timeout: float) -> None:
    setup_logging(cfg)
    m = Master(cfg, adapter=adapter, backend=backend)
    m.run()  # 阻塞直到调度完成或异常


def log_text(cfg: Config) -> str:
    return Path(cfg.log_file).read_text(encoding="utf-8", errors="replace")


def load_state(cfg: Config) -> dict:
    return __import__("json").loads(Path(cfg.state_file).read_text(encoding="utf-8"))


def cleanup(cfg: Config) -> None:
    for p in (cfg.state_file, cfg.log_file, cfg.flags_file):
        Path(p).unlink(missing_ok=True)
    for cid in ("t1", "t2", "t3", "f2", "d1", "h6f",
                "fk1", "fk2", "fk3", "fk4", "fk5", "fk6",
                "pk1", "pk2", "pk3"):
        shutil.rmtree(REPO / "challenges" / "fake" / cid, ignore_errors=True)


# ───────────────────────── 场景 A: 渐进式圈超时轮转 ─────────────────────────

def scenario_a() -> None:
    """6 道卡死题 3 槽 (无正常题): 验证实际 60s→90s→120s 渐进时间预算 +
    圈推进 + max_rounds 兜底。每圈 2 批, 每批都有候选 (改后最后一批也轮转)。"""
    print("\n=== 场景 A: 渐进式圈超时轮转 (实际 60s→90s→120s, 3 圈) ===")
    cfg = make_cfg("a", solver_timeout=45)  # 兜底 (正常不会走到)
    cleanup(cfg)
    adapter = TestAdapter(num_fail=6, include_normal=False)  # 6 道 fk 永不产出
    backend = FakeBackend(solve_delay=1.0)

    t0 = time.time()
    try:
        run_master(cfg, adapter, backend, timeout=700)
    finally:
        elapsed = time.time() - t0

    log = log_text(cfg)
    st = load_state(cfg)
    recs = {r["id"]: r for r in st["records"].values()}

    check("进入第 2 圈", "进入第 2 圈" in log, "圈数未推进")
    check("进入第 3 圈", "进入第 3 圈" in log, "圈数未推进")
    check("第 2 圈实际时间上限 90s", "单 flag 时间上限 90 秒" in log,
          f"实际: {[l for l in log.splitlines() if '进入第' in l]}")
    check("第 3 圈实际时间上限 120s", "单 flag 时间上限 120 秒" in log)
    check("第 1 圈超时为 60s", "本圈超时 (60s" in log,
          f"实际: {[l for l in log.splitlines() if '本圈超时' in l][:2]}")
    fails = sorted((r for r in recs.values() if r["id"].startswith("fk")),
                   key=lambda r: r["id"])
    check("6 道卡死题全部终态 timeout", all(r["status"] == "timeout" for r in fails),
          f"{[(r['id'], r['status']) for r in fails]}")
    # 批 1 (fk1-3) 每圈 rotate → ld=3; 批 2 (fk4-6) 末圈无候选走
    # solver_timeout 终态 (ld=2), 属预期 "最后一批连续做到底"
    check("批 1 完成 3 圈 (ld=3)", all(r.get("last_done_round") == 3 for r in fails[:3]),
          f"{[(r['id'], r.get('last_done_round')) for r in fails[:3]]}")
    check("批 2 终态且至少 2 圈 (ld>=2)",
          all(r.get("last_done_round", 0) >= 2 for r in fails[3:]),
          f"{[(r['id'], r.get('last_done_round')) for r in fails[3:]]}")
    check("总时长 < 660s", elapsed < 660, f"elapsed={elapsed:.0f}s")
    print(f"  (耗时 {elapsed:.0f}s)")
    cleanup(cfg)


# ───────────────────── 场景 B: 分发失败让路 (旧 bug 回归) ─────────────────────

def scenario_b() -> None:
    print("\n=== 场景 B: 开靶机持续失败 → 每次让路后按高优先级补试, 不阻塞轮转 ===")
    M.DISPATCH_COOLDOWN = 2  # 测试加速: 冷却 2s (生产 30s)
    cfg = make_cfg("b")
    cleanup(cfg)  # 清上次运行残留
    adapter = TestAdapter(fail_cids={"fk1"})  # num_fail=1 默认: fk1 存在且开靶机永远失败
    backend = FakeBackend(solve_delay=1.0)

    t0 = time.time()
    try:
        run_master(cfg, adapter, backend, timeout=180)
    finally:
        elapsed = time.time() - t0

    log = log_text(cfg)
    st = load_state(cfg)
    recs = {r["id"]: r for r in st["records"].values()}

    n_fail = log.count("分发 fk1 失败")
    check("fk1 每圈都被优先补试 (失败次数>=圈数)", n_fail >= 3, f"失败次数={n_fail}")
    check("进入第 2 圈", "进入第 2 圈" in log, "fk1 阻塞了轮转! (旧 bug)")
    check("进入第 3 圈", "进入第 3 圈" in log)
    check("正常题 t1 终态 correct", recs.get("t1", {}).get("status") == "submitted_correct",
          f"t1={recs.get('t1', {}).get('status')}")
    check("正常题 t2 终态 correct", recs.get("t2", {}).get("status") == "submitted_correct")
    check("fk1 未消耗重试配额 (attempts=0)", recs.get("fk1", {}).get("attempts") == 0,
          f"fk1 attempts={recs.get('fk1', {}).get('attempts')}")
    check("fk1 没进过 solver 不算做过 (ld=0)", recs.get("fk1", {}).get("last_done_round") == 0,
          f"fk1 ld={recs.get('fk1', {}).get('last_done_round')}")
    check("fk1 终态 timeout (max_rounds 兜底)", recs.get("fk1", {}).get("status") == "timeout",
          f"fk1={recs.get('fk1', {}).get('status')}")
    check("总时长 < 120s", elapsed < 120, f"elapsed={elapsed:.0f}s")
    print(f"  (耗时 {elapsed:.0f}s)")
    cleanup(cfg)


# ───────────────────── 场景 G: 基础设施失败最高优先补试 ─────────────────────

def scenario_g() -> None:
    """单槽验证: 空槽时基础设施失败的题 (没进过 solver) 排最前, 每次冷却
    过就被优先拉起补试, 平台恢复第一时间能打; 正常题不被打死。"""
    print("\n=== 场景 G: 失败题最高优先补试 (单槽) ===")
    M.DISPATCH_COOLDOWN = 2  # 测试加速
    cfg = make_cfg("g", max_solvers=1)
    cleanup(cfg)
    adapter = TestAdapter(fail_cids={"fk1"})  # fk1 开靶机永远失败
    backend = FakeBackend(solve_delay=1.0)

    t0 = time.time()
    try:
        run_master(cfg, adapter, backend, timeout=180)
    finally:
        elapsed = time.time() - t0

    log = log_text(cfg)
    st = load_state(cfg)
    recs = {r["id"]: r for r in st["records"].values()}

    n_fail = log.count("分发 fk1 失败")
    # 失败题在正常题解出后仍被反复拉起 → 空槽优先补试
    t1_done_at = next((i for i, l in enumerate(log.splitlines()) if "FLAG ACCEPTED: t1" in l), -1)
    fail_after_t1 = sum(1 for l in log.splitlines()[t1_done_at:] if "分发 fk1 失败" in l)
    check("fk1 冷却后反复优先补试 (失败次数>=5)", n_fail >= 5, f"失败次数={n_fail}")
    check("正常题解出后 fk1 仍被优先拉起 (补试>=2)", fail_after_t1 >= 2,
          f"t1 后补试次数={fail_after_t1}")
    check("正常题 t1/t2/t3 全部解出", all(
        recs.get(c, {}).get("status") == "submitted_correct" for c in ("t1", "t2", "t3")),
        f"{[(c, recs.get(c, {}).get('status')) for c in ('t1','t2','t3')]}")
    check("fk1 没进过 solver (ld=0, attempts=0)",
          recs.get("fk1", {}).get("last_done_round") == 0
          and recs.get("fk1", {}).get("attempts") == 0,
          f"fk1 ld={recs.get('fk1', {}).get('last_done_round')} attempts={recs.get('fk1', {}).get('attempts')}")
    check("不阻塞轮转 (进入第 3 圈)", "进入第 3 圈" in log)
    check("fk1 终态 timeout", recs.get("fk1", {}).get("status") == "timeout",
          f"fk1={recs.get('fk1', {}).get('status')}")
    check("总时长 < 180s", elapsed < 180, f"elapsed={elapsed:.0f}s")
    print(f"  (耗时 {elapsed:.0f}s)")
    cleanup(cfg)


# ───────────────────── 场景 C: _finalize 轮转语义 ─────────────────────

def scenario_c() -> None:
    print("\n=== 场景 C: _finalize (失败/崩溃) → 本圈让路, 下圈再试, max_rounds 兜底 ===")
    cfg = make_cfg("c")
    cleanup(cfg)  # 清上次运行残留
    setup_logging(cfg)
    m = Master(cfg, adapter=TestAdapter(), backend=FakeBackend(solve_delay=1.0))

    # 手动构造一条 RUNNING 记录, 模拟 master 重启恢复路径
    rec = m.state.sync_challenge(Challenge(id="c1", title="崩溃题", type="crypto",
                                           score=100, solve_count=500, description=""))
    m.state.set_status("c1", "running")
    rec.attempts = 1
    m.current_round = 1

    # 圈内失败 → 本圈让路, QUEUED, 下圈再试
    m._finalize("c1", "failed", "Master 重启，solver 进程丢失")
    rec = m.state.get("c1")
    check("失败后 QUEUED (不是 FAILED 终态)", rec.status == "queued", f"status={rec.status}")
    check("本圈让路 (last_done_round=1)", rec.last_done_round == 1, f"ld={rec.last_done_round}")

    # 下圈重新可分发
    m.current_round = 2
    rec.last_done_round = 1
    m._finalize("c1", "failed", "又崩了一次")
    rec = m.state.get("c1")
    check("第 2 圈再失败仍 QUEUED", rec.status == "queued", f"status={rec.status}")
    check("第 2 圈让路 (ld=2)", rec.last_done_round == 2, f"ld={rec.last_done_round}")

    # max_rounds 兜底: 最后一圈失败 → 终态
    m.current_round = 3
    rec.last_done_round = 2
    m._finalize("c1", "failed", "最后一圈仍失败")
    rec = m.state.get("c1")
    check("max_rounds 兜底终态 TIMEOUT", rec.status == "timeout", f"status={rec.status}")
    check("终态带原因 (达到最大轮数)", "达到最大轮数" in (rec.error or ""), f"error={rec.error}")

    cleanup(cfg)


# ───────────────────── 场景 D: master 重启恢复 ─────────────────────

def scenario_d() -> None:
    """重启恢复: RUNNING 题 → _recover → 轮转让路 → 下圈重新分发解出。"""
    print("\n=== 场景 D: master 重启恢复 (RUNNING → 让路 → 下圈再试) ===")
    import json as _json
    cfg = make_cfg("d")
    cleanup(cfg)
    # 预写 state: 一条 RUNNING 记录, 模拟上次运行中 master 崩溃
    Path(cfg.state_file).write_text(_json.dumps({
        "records": {
            "d1": {"id": "d1", "title": "恢复题", "type": "crypto", "score": 300,
                   "solve_count": 10, "description": "重启恢复测试",
                   "status": "running", "attempts": 1, "last_done_round": 0},
        },
        "saved_at": "test",
    }, ensure_ascii=False), encoding="utf-8")

    setup_logging(cfg)
    # num_fail=0: 只测 d1 恢复路径, 不带卡死题拖慢场景
    m = Master(cfg, adapter=TestAdapter(num_fail=0), backend=FakeBackend(solve_delay=1.0))
    m.run()

    log = log_text(cfg)
    st = load_state(cfg)
    recs = {r["id"]: r for r in st["records"].values()}
    d1 = recs.get("d1", {})

    check("恢复日志: RUNNING 标记失败", "恢复: d1 上次处于 running" in log, "无恢复日志")
    check("失败后本圈让路 (ld=1)", d1.get("last_done_round") == 1,
          f"ld={d1.get('last_done_round')}")
    check("下圈重新分发并解出 (correct)", d1.get("status") == "submitted_correct",
          f"status={d1.get('status')}")
    check("进入第 2 圈", "进入第 2 圈" in log, "恢复后未推进圈数")
    cleanup(cfg)


# ───────────────────── 场景 F: 多 flag 未通关回收 ─────────────────────

def scenario_f() -> None:
    """多 flag 题: 解出 1/2 → 回收重新分发 (不丢进度, 不卡死)。"""
    print("\n=== 场景 F: 多 flag 未通关回收 ===")
    cfg = make_cfg("f")
    cleanup(cfg)
    adapter = TestAdapter(multi_flag_cids={"f2"}, num_fail=0)
    backend = FakeBackend(solve_delay=1.0)

    t0 = time.time()
    try:
        run_master(cfg, adapter, backend, timeout=180)
    finally:
        elapsed = time.time() - t0

    log = log_text(cfg)
    st = load_state(cfg)
    recs = {r["id"]: r for r in st["records"].values()}
    f2 = recs.get("f2", {})

    check("多 flag 未通关日志 (1/2)", "未通关继续" in log, "无 '未通关继续'")
    check("回收后重新分发 (attempts>=3)", f2.get("attempts", 0) >= 3,
          f"attempts={f2.get('attempts')}")
    check("f2 最终由 max_rounds 兜底", f2.get("status") == "timeout",
          f"status={f2.get('status')}")
    check("其他题不受影响 (t1 correct)", recs.get("t1", {}).get("status") == "submitted_correct",
          f"t1={recs.get('t1', {}).get('status')}")
    check("总时长 < 60s", elapsed < 60, f"elapsed={elapsed:.0f}s")
    print(f"  (耗时 {elapsed:.0f}s)")
    cleanup(cfg)


# ───────────────────── 场景 E: 轮转断点 session 恢复 ─────────────────────

class SessionBackend(FakeBackend):
    """模拟 run.sh 的断点行为: 每次 start 后延迟写 .cc_session (模拟 claude
    会话 id), 并记录每次 start 收到的 cc_session_id, 用于断言下圈恢复原 session。
    """

    def __init__(self, solve_delay: float = 1.0):
        super().__init__(solve_delay=solve_delay)
        self.start_calls: list[dict] = []  # [{cid, session}]
        self._seq: dict[str, int] = {}

    def start(self, ch):
        handle = super().start(ch)
        self.start_calls.append({"cid": ch.id, "session": ch.cc_session_id})
        seq = self._seq.get(ch.id, 0) + 1
        self._seq[ch.id] = seq
        wd = handle.work_dir
        wd.mkdir(parents=True, exist_ok=True)

        def _write_session():
            time.sleep(0.5)  # solver 跑起来后才产生 session
            (wd / ".cc_session").write_text(f"sess-{ch.id}-{seq}", encoding="utf-8")

        threading.Thread(target=_write_session, daemon=True).start()
        return handle


def scenario_e() -> None:
    """断点续攻: 第 2/3 圈重新分发时, start 必须带上上一轮写入的 session
    (cc_session_id 非空且等于上一轮的 .cc_session), 而不是从 0 开始。"""
    print("\n=== 场景 E: 轮转断点 session 恢复 (下圈恢复原 session) ===")
    # 小时间参数只用于快速触发轮转 (单 flag 20s/30s/40s; 时间预算本身由 A 验证)
    cfg = make_cfg("e", max_solvers=2, round_time_base=20, round_time_step=10,
                   max_rounds=3, solver_timeout=30)
    cleanup(cfg)
    adapter = TestAdapter(num_fail=3, include_normal=False)  # 只有 fk1/fk2/fk3
    backend = SessionBackend(solve_delay=1.0)

    t0 = time.time()
    try:
        run_master(cfg, adapter, backend, timeout=240)
    finally:
        elapsed = time.time() - t0

    log = log_text(cfg)
    st = load_state(cfg)
    recs = {r["id"]: r for r in st["records"].values()}

    check("进入第 3 圈", "进入第 3 圈" in log, "轮转未跑满 3 圈")
    for cid in ("fk1", "fk2", "fk3"):
        calls = [c for c in backend.start_calls if c["cid"] == cid]
        s0 = calls[0]["session"] if calls else "NO-CALL"
        s1 = calls[1]["session"] if len(calls) > 1 else "NO-CALL"
        s2 = calls[2]["session"] if len(calls) > 2 else "NO-CALL"
        check(f"{cid} 第 1 轮无 session (新开)", s0 is None, f"s0={s0}")
        check(f"{cid} 第 2 轮恢复第 1 轮 session", s1 == f"sess-{cid}-1",
              f"s1={s1} (期望 sess-{cid}-1)")
        check(f"{cid} 第 3 轮恢复第 2 轮 session", s2 == f"sess-{cid}-2",
              f"s2={s2} (期望 sess-{cid}-2)")
        check(f"{cid} 未从 0 开始 (session 非空)", s1 is not None and s2 is not None)
        check(f"{cid} 3 圈各分发 1 次", len(calls) == 3, f"分发次数={len(calls)}")
    check("总时长 < 200s", elapsed < 200, f"elapsed={elapsed:.0f}s")
    print(f"  (耗时 {elapsed:.0f}s)")
    cleanup(cfg)


# ───────────────────── 场景 H: 熔断 + URL 复用 + 提交上限 ─────────────────────

def scenario_h() -> None:
    """三个新修复的集成验证:
    H1. 409 熔断: "max active" 的题不阻塞/不饿死正常题, 也不连发 409
    H2. URL 复用: 容器未就绪 (预检失败) 的题不再重复 POST start
    H3. 多 flag 提交上限: 6 flag 题额度 = 18, 不会交 2 个就锁死
    """
    print("\n=== 场景 H: 熔断 + URL 复用 + 多flag提交上限 ===")
    M.DISPATCH_COOLDOWN = 2  # 测试加速
    M.PLATFORM_FULL_COOLDOWN = 5  # 测试加速 (409 让路长冷却只影响生产)
    cfg = make_cfg("h", max_solvers=2, max_rounds=3, solver_timeout=30)
    cleanup(cfg)
    adapter = TestAdapter(
        pending_cids={"pk1"},   # 容器启动中: 拿到 URL 但不可达
        full_cids={"fk1"},      # 平台 active 满
        num_fail=0,
    )
    backend = FakeBackend(solve_delay=1.0)

    t0 = time.time()
    try:
        run_master(cfg, adapter, backend, timeout=240)
    finally:
        elapsed = time.time() - t0

    log = log_text(cfg)
    st = load_state(cfg)
    recs = {r["id"]: r for r in st["records"].values()}

    # H1: 熔断 —— fk1 (max active) 失败后不饿死正常题
    check("平台满熔断日志出现", "平台实例已满，本轮熔断" in log, "无熔断日志")
    check("正常题 t1 不被 409 题饿死 (correct)",
          recs.get("t1", {}).get("status") == "submitted_correct",
          f"t1={recs.get('t1', {}).get('status')}")
    check("正常题 t2 不被 409 题饿死 (correct)",
          recs.get("t2", {}).get("status") == "submitted_correct")
    check("fk1 让路不阻塞圈推进", "进入第 2 圈" in log)
    check("fk1 终态 timeout (max_rounds 兜底)",
          recs.get("fk1", {}).get("status") == "timeout",
          f"fk1={recs.get('fk1', {}).get('status')}")

    # H2: URL 复用 —— pk1 预检失败后不再重复 POST start
    pk_calls = adapter.start_calls.get("pk1", 0)
    check("pk1 start_challenge 只调 1 次 (复用 URL)", pk_calls == 1,
          f"pk1 start 次数={pk_calls}")
    check("pk1 有复用等待日志 (靶机未就绪)", "靶机未就绪" in log)

    # H3: 多 flag 提交上限放大 (单测)
    m = Master(cfg, adapter=TestAdapter(num_fail=0), backend=FakeBackend())
    rec6 = m.state.sync_challenge(
        Challenge(id="h6f", title="六flag", type="crypto", flag_count=6,
                  score=500, solve_count=5, description=""))
    ok_6 = True
    for i in range(6 * 3):  # 18 次内都应允许 (0..17 → submit_count=18)
        if not m.state.can_submit("h6f"):
            ok_6 = False
            break
        m.state.record_submit("h6f", f"flag{i}", "submitting")
    check("6 flag 题前 18 次提交都允许", ok_6, "额度没放大!")
    check("6 flag 题第 19 次被拒", not m.state.can_submit("h6f"), "第 19 次应超限")

    check("总时长 < 240s", elapsed < 240, f"elapsed={elapsed:.0f}s")
    print(f"  (耗时 {elapsed:.0f}s)")
    cleanup(cfg)


# ───────────────────── 场景 I: 多 flag 通关闭环 + 判死释放 ─────────────────────

class MultiFlagBackend(FakeBackend):
    """模拟多 flag solver: 每轮写出 cid_flags 里的全部 flag (第 2 轮起只新 flag 被提交)。"""

    def __init__(self, cid_flags: dict, solve_delay: float = 0.5):
        super().__init__(solve_delay=solve_delay)
        self.cid_flags = cid_flags

    def _simulate(self, ch, handle):
        flags = self.cid_flags.get(ch.id)
        if flags is None:
            return super()._simulate(ch, handle)  # 无多 flag 配置 → 默认行为
        handle.work_dir.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(flags):
            time.sleep(self.solve_delay * (i + 1))
            with open(handle.work_dir / "progress.md", "w", encoding="utf-8") as fh:
                fh.write("## Flags Found\n" + "\n".join(flags[:i + 1]) + "\n")
        # 写完所有 flag 后线程结束 (solver 退出)


def scenario_i() -> None:
    """偶发补充 1: 多 flag 题完整闭环 —— 解出 flag1 → 1/2 回收 → 重跑
    写出 flag1+flag2 → flag2 新提交 → 2/2 通关终态, 靶机释放。
    偶发补充 2: pending 题 16 次预检失败 → 判死 + stop_challenge 释放实例。"""
    print("\n=== 场景 I: 多 flag 通关闭环 + pending 判死释放 ===")
    M.DISPATCH_COOLDOWN = 2  # 测试加速
    cfg = make_cfg("i", max_solvers=3, max_rounds=3, solver_timeout=30)
    cleanup(cfg)
    adapter = TestAdapter(
        multi_flag_cids={"f2"},
        pending_cids={"pk1"},   # 永远容器未就绪 → boot_fails 判死
        num_fail=0,
    )
    backend = MultiFlagBackend({"f2": ["flag{f2_alpha}", "flag{f2_beta}"]})

    t0 = time.time()
    try:
        run_master(cfg, adapter, backend, timeout=240)
    finally:
        elapsed = time.time() - t0

    log = log_text(cfg)
    st = load_state(cfg)
    recs = {r["id"]: r for r in st["records"].values()}

    # I1: 多 flag 通关 (f2 是 crypto 题无靶机, 不查 stop)
    f2 = recs.get("f2", {})
    check("多 flag 先未通关 (1/2)", "未通关继续" in log, "无 1/2 回收日志")
    check("多 flag 最终通关 (2/2)", "通关 2/2" in log, "无 2/2 通关日志")
    check("f2 终态 submitted_correct", f2.get("status") == "submitted_correct",
          f"f2={f2.get('status')}")
    check("f2 两个 flag 都已提交", len(f2.get("flags_submitted", [])) >= 2,
          f"submitted={f2.get('flags_submitted')}")

    # I2: pending 题终态时释放平台实例 (max_rounds 兜底 + stop)
    pk = recs.get("pk1", {})
    check("pk1 终态 (timeout 兜底)", pk.get("status") == "timeout",
          f"pk1={pk.get('status')} error={pk.get('error', '')[:60]}")
    check("pk1 终态时释放平台实例 (stop 被调)", adapter.stop_calls.get("pk1", 0) >= 1,
          f"stop_calls={adapter.stop_calls.get('pk1', 0)}")
    check("pk1 从未重复 POST (start 只 1 次)", adapter.start_calls.get("pk1", 0) == 1,
          f"start_calls={adapter.start_calls.get('pk1', 0)}")
    check("其他题不受影响 (t1 correct)", recs.get("t1", {}).get("status") == "submitted_correct",
          f"t1={recs.get('t1', {}).get('status')}")
    check("总时长 < 240s", elapsed < 240, f"elapsed={elapsed:.0f}s")
    print(f"  (耗时 {elapsed:.0f}s)")
    cleanup(cfg)


# ───────────────── 场景 J: 平台 active 名额感知 (409 风暴根治) ─────────────────

def scenario_j() -> None:
    """用户日志实锤 (2026-08-20): 第 2 圈 3 个槽一口气 start 3 个实例
    → 平台 active 满 (3) → 其余排队题全 409 饿死, 尝试数卡死。
    修复: _fill_slots 名额感知 (已开实例 >= platform_max_active 不再 start),
    预检失败题复用 URL 不重复 POST, 409 让路 5 分钟长冷却。
    验证: ① 平台满时不再尝试新题 (无 409 刷屏) ② 预检失败题 start 只 1 次
    ③ 名额释放后排队题能进 (不饿死) ④ 全部终态。"""
    print("\n=== 场景 J: 平台 active 名额感知 (409 风暴根治) ===")
    M.DISPATCH_COOLDOWN = 2  # 测试加速
    M.PLATFORM_FULL_COOLDOWN = 5
    cfg = make_cfg("j", max_solvers=3, max_rounds=2, solver_timeout=30,
                   platform_max_active=3)
    cleanup(cfg)
    adapter = TestAdapter(
        pending_cids={"pk1", "pk2", "pk3"},  # 容器永不就绪 → 占满 3 名额后判死释放
        ok_web_cids={"w1", "w2"},            # 正常 web 题: 名额释放后应能 start 并解出
        num_fail=0,
        include_normal=False,
        max_active=3,                        # 模拟平台 active 上限 (409 语义)
    )
    backend = FakeBackend(solve_delay=0.5)

    t0 = time.time()
    try:
        run_master(cfg, adapter, backend, timeout=240)
    finally:
        elapsed = time.time() - t0

    log = log_text(cfg)
    st = load_state(cfg)
    recs = {r["id"]: r for r in st["records"].values()}

    # J1: 平台满时 master 不再尝试 start 新题 (名额感知日志出现)
    #     注: MAX_BOOT_FAILS=12 判死较快, pk 可能先释放名额让 w 直接进,
    #     此时名额未满日志不出现, 但"w 不被 409 饿死"仍然成立
    n409 = sum(1 for l in log.splitlines() if "start 409" in l)
    check("平台名额感知 (已满日志 或 无 409 刷屏)",
          "平台 active 实例已满" in log or n409 == 0, f"无名额感知且疑似 409 (n={n409})")
    # J2: 没有 409 刷屏 (w1/w2 在平台满时根本没被尝试 start)
    check("无 409 刷屏 (w1/w2 没被反复 start)", n409 == 0, f"409 行数={n409}")
    # J3: 预检失败题 (pk) 复用 URL 不重复 POST start
    for cid in ("pk1", "pk2", "pk3"):
        check(f"{cid} start 只 1 次 (复用 URL)", adapter.start_calls.get(cid, 0) == 1,
              f"start_calls={adapter.start_calls.get(cid, 0)}")
    # J4: 名额释放后排队题能进 (w1/w2 解出, 不饿死)
    check("w1 名额释放后解出", recs.get("w1", {}).get("status") == "submitted_correct",
          f"w1={recs.get('w1', {}).get('status')}")
    check("w2 名额释放后解出", recs.get("w2", {}).get("status") == "submitted_correct",
          f"w2={recs.get('w2', {}).get('status')}")
    # J5: pk 终态 (boot 判死 或 max_rounds 兜底, 两者都释放实例) + 实例释放
    for cid in ("pk1", "pk2", "pk3"):
        status = recs.get(cid, {}).get("status")
        ok_term = status in ("failed", "timeout")
        check(f"{cid} 终态 (判死/兜底)", ok_term,
              f"{cid}={status} err={recs.get(cid, {}).get('error', '')[:40]}")
        check(f"{cid} 实例已释放 (stop 被调)", adapter.stop_calls.get(cid, 0) >= 1,
              f"stop_calls={adapter.stop_calls.get(cid, 0)}")
    check("总时长 < 240s", elapsed < 240, f"elapsed={elapsed:.0f}s")
    print(f"  (耗时 {elapsed:.0f}s)")
    cleanup(cfg)


# ───────────── 场景 K: binary 题 TCP 靶机预检 (f1-04 修复回归) ─────────────

def scenario_k() -> None:
    """用户日志实锤 (2026-08-20): f1-04 (binary) 永远"靶机未就绪 (7/16)"——
    容器其实 2-3 分钟就起来了, 但 _target_alive 只做 HTTP GET 探测,
    binary 靶机是 TCP 服务 (nc/自定义协议), HTTP 必然失败 → 误判未就绪。
    修复: HTTP 探测失败后退化 TCP connect, 端口开放即算存活。
    验证: binary 题 (仅 TCP 端口, 无 HTTP 响应) 能通过预检进入 solver。"""
    print("\n=== 场景 K: binary 题 TCP 靶机预检 (f1-04 修复回归) ===")
    M.DISPATCH_COOLDOWN = 2
    M.PLATFORM_FULL_COOLDOWN = 5
    M.MAX_BOOT_FAILS = 6  # 测试加速: 6x2s=12s 判死
    cfg = make_cfg("k", max_solvers=3, max_rounds=2, solver_timeout=30,
                   platform_max_active=3)
    cleanup(cfg)
    adapter = TestAdapter(
        ok_tcp_cids={"k1", "k2"},   # binary: 仅 TCP 端口开放, HTTP 探测必失败
        ok_udp_cids={"ku1"},        # UDP 服务: 仅 UDP 端口收包响应
        ok_web_cids={"kw1"},        # web: HTTP 正常 (对照)
        num_fail=0,
        include_normal=False,
    )
    backend = FakeBackend(solve_delay=0.5)

    t0 = time.time()
    try:
        run_master(cfg, adapter, backend, timeout=240)
    finally:
        elapsed = time.time() - t0

    log = log_text(cfg)
    st = load_state(cfg)
    recs = {r["id"]: r for r in st["records"].values()}

    # K1: binary 题通过预检进 solver 并解出 (不再被 HTTP 探测误判卡死)
    for cid in ("k1", "k2"):
        check(f"{cid} (binary TCP) 预检通过并解出",
              recs.get(cid, {}).get("status") == "submitted_correct",
              f"{cid}={recs.get(cid, {}).get('status')} err={recs.get(cid, {}).get('error', '')[:50]}")
        check(f"{cid} start 只 1 次", adapter.start_calls.get(cid, 0) == 1,
              f"start_calls={adapter.start_calls.get(cid, 0)}")
    # K2: UDP 服务题通过预检 (HTTP/TCP 都不通, 靠 UDP 探测)
    check("ku1 (UDP 服务) 预检通过并解出",
          recs.get("ku1", {}).get("status") == "submitted_correct",
          f"ku1={recs.get('ku1', {}).get('status')} err={recs.get('ku1', {}).get('error', '')[:50]}")
    # K3: 无"靶机未就绪"误判 (binary/udp 题不该再走 boot 循环)
    n_boot = sum(1 for l in log.splitlines()
                 if "靶机未就绪" in l and ("k1" in l or "k2" in l or "ku1" in l))
    check("非 HTTP 题无 boot 误判循环", n_boot == 0, f"boot 行数={n_boot}")
    # K4: 对照 web 题正常
    check("web 对照题 kw1 解出", recs.get("kw1", {}).get("status") == "submitted_correct",
          f"kw1={recs.get('kw1', {}).get('status')}")
    check("总时长 < 240s", elapsed < 240, f"elapsed={elapsed:.0f}s")
    print(f"  (耗时 {elapsed:.0f}s)")
    cleanup(cfg)


if __name__ == "__main__":
    scenario_a()
    scenario_b()
    scenario_c()
    scenario_d()
    scenario_e()
    scenario_f()
    scenario_g()
    scenario_h()
    scenario_i()
    scenario_j()
    scenario_k()
    print(f"\n===== 结果: {PASS} 通过 / {FAIL} 失败 =====")
    sys.exit(1 if FAIL else 0)
