#!/usr/bin/env python3
"""
真实 agent 仿真测试 - 跨轮次 session 恢复 (2026-08-21, 无解题版)

2 槽 × 4 题 × 短轮转, 真实起 ctf-agent (ProcessBackend + run.sh + 真 claude/hermes)。

**题全部无解** (靶机页面没有任何 flag): claude 找不到 flag → 必然第一轮超时轮转。
这正好测核心 (用户要求):
  第一轮 claude 建 session → 超时轮转 → run.sh cleanup 提取 session_id 写
  .cc_session → master 拿到 cc_session_id → 第二轮分发传 --resume-session →
  claude --resume 恢复**原会话** (codex.log session_id == 第一轮)。

⚠️ 真实调用 deepseek API (claude 解题 + hermes 监督), 会有费用。
用法: cd /home/stw/ctf-agent && python3 tests/test_sim_live.py
"""
from __future__ import annotations

import http.server
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "master"))
sys.path.insert(0, str(REPO / "tests"))

from adapters.base import Challenge, PlatformAdapter, SubmitResult  # noqa: E402
from master import Config, Master, setup_logging  # noqa: E402
from solver_pool import ProcessBackend  # noqa: E402

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


# ───────────────── 无解仿真平台: 4 道 web 题 (页面无 flag) ─────────────────
SIM_TITLES = {
    "sim-a": "仿真题A-登录页",
    "sim-b": "仿真题B-会员系统",
    "sim-c": "仿真题C-管理后台",
    "sim-d": "仿真题D-旧版页面",
}


def _sim_index(cid: str) -> str:
    # 无解题: 页面干净, 没有任何 flag (claude 找不到 → 必然超时轮转)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{SIM_TITLES[cid]}</title></head>
<body>
  <h1>{SIM_TITLES[cid]}</h1>
  <form method="post" action="/login">
    <input name="user" placeholder="username">
    <input type="password" name="pass" placeholder="password">
    <button type="submit">Login</button>
  </form>
  <p>&copy; 2026 SimCorp. All rights reserved.</p>
</body></html>
"""


def _make_sim_handler(docroot: Path):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(docroot), **kwargs)

        def log_message(self, fmt, *args):
            pass

    return Handler


class SimAdapter(PlatformAdapter):
    """仿真平台: 4 道无解 web 题 (页面无 flag, 只测 session 恢复)。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._servers: dict[str, tuple[http.server.ThreadingHTTPServer, Path]] = {}
        self._submits = 0  # 提交计数 (无解题不应有正确提交)

    def list_challenges(self) -> list[Challenge]:
        return [
            Challenge(id=cid, title=SIM_TITLES[cid], type="web",
                      score=100 + i * 50, solve_count=30 - i,
                      description=f"仿真题。登录页里藏着 flag，找找看。")
            for i, cid in enumerate(SIM_TITLES)
        ]

    def start_challenge(self, cid: str) -> str:
        if cid not in SIM_TITLES:
            return ""
        with self._lock:
            if cid in self._servers:
                srv = self._servers[cid][0]
                return f"http://127.0.0.1:{srv.server_address[1]}"
        docroot = Path(tempfile.mkdtemp(prefix=f"sim-{cid}-"))
        (docroot / "index.html").write_text(_sim_index(cid), encoding="utf-8")
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_sim_handler(docroot))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with self._lock:
            self._servers[cid] = (srv, docroot)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def stop_challenge(self, cid: str) -> None:
        with self._lock:
            item = self._servers.pop(cid, None)
        if item:
            srv, docroot = item
            srv.shutdown()
            srv.server_close()
            shutil.rmtree(docroot, ignore_errors=True)

    def submit(self, cid: str, flag: str) -> SubmitResult:
        self._submits += 1
        return SubmitResult("wrong", "无解题, 任何 flag 都错")

    def get_hint(self, cid: str) -> str:
        return "看看页面源码"

    def download_attachment(self, url: str, dest_dir: Path) -> Path:
        raise FileNotFoundError(f"no attachment for {url}")


# ───────────────── 主测试 ─────────────────
def main() -> int:
    global PASS, FAIL
    print("=" * 60)
    print("真实 agent 仿真: 跨轮次 session 恢复 (无解题)")
    print("2 槽 × 4 题, 第一轮必超时 → cleanup 提取 session → 第二轮 resume")
    print(f"⚠️  真实 deepseek API 调用 (claude 解题 + hermes 监督)")
    print("=" * 60)

    adapter = SimAdapter()
    backend = ProcessBackend(agent_cli="claude")

    cfg = Config(
        adapter="sim",
        backend="process",
        max_solvers=2,
        max_challenges=4,
        round_time_base=45,       # 第 1 圈每题 45s (claude 能产出 session, 但解不出)
        round_time_step=30,       # 第 2 圈 75s
        max_rounds=2,
        poll_interval=5,
        solver_timeout=120,
        agent_cli="claude",
        state_file=str(REPO / "master_state.json"),
        log_file=str(REPO / "master.log"),
        flags_file=str(REPO / "flags.jsonl"),
        dashboard_port=0,
        resident=False,
    )
    for p in (Path(cfg.state_file), Path(cfg.log_file), Path(cfg.flags_file)):
        p.unlink(missing_ok=True)

    log_path = Path(cfg.log_file)
    if not log_path.is_absolute():
        log_path = REPO / log_path
    setup_logging(log_path)

    m = Master(cfg, adapter=adapter, backend=backend)
    try:
        m.run()
    except KeyboardInterrupt:
        print("\n[测试] 被中断, 收尾...")
        m._stop.set()
    finally:
        m._shutdown()

    # ─── 验证 (核心: 真实 session 恢复) ───
    print("\n" + "=" * 60)
    print("验证结果")
    print("=" * 60)

    mlog = log_path.read_text(errors="replace") if log_path.exists() else ""
    state = json.loads(Path(cfg.state_file).read_text()) if Path(cfg.state_file).exists() else {}
    records = state.get("records", {})

    # 1. 4 题都尝试过
    tried = [r for r in records.values() if r.get("attempts", 0) >= 1]
    check("4 题都尝试", len(tried) >= 4, f"tried={[r['id'] for r in tried]}")

    # 2. 轮转发生 (无解题必超时)
    check("超时轮转", "本圈超时" in mlog, "无超时轮转")

    # 3. ★ 核心: 第二轮真实 resume 原会话 (不是 board.md 降级)
    #    判据: master 拿到 cc_session_id + run.sh 传 --resume-session
    resumed = [r for r in records.values() if r.get("cc_session_id")]
    check("master 记录 cc_session_id (第一轮 session 被提取)", len(resumed) >= 1,
          f"n={len(resumed)} cc={[r['id'] for r in resumed]}")

    # 第二轮分发时 run.sh 收到 --resume-session
    resume_log = 0
    for ml in (REPO / "master_logs").glob("sim-*.log"):
        if "恢复上一圈会话" in ml.read_text(errors="replace"):
            resume_log += 1
    check("run.sh 收到 --resume-session (恢复上一圈会话)", resume_log >= 1,
          f"n={resume_log}")

    # 4. 第二轮 codex.log session_id == .cc_session (真实 resume 到同一会话)
    resume_same = 0
    for d in (REPO / "challenges").glob("manual_web_*"):
        cc_file = d / ".cc_session"
        if not cc_file.exists():
            continue
        cc_sid = cc_file.read_text().strip()
        if not cc_sid:
            continue
        log_txt = (d / "codex.log").read_text(errors="replace") if (d / "codex.log").exists() else ""
        if cc_sid in log_txt:
            resume_same += 1
    check("第二轮 codex.log session_id == 第一轮 (原会话 resume)", resume_same >= 1,
          f"n={resume_same}")

    # 5. .hermes_session 写入 (hermes 恢复监督会话)
    wd_hm = sum(1 for d in (REPO / "challenges").glob("manual_web_*")
                if (d / ".hermes_session").exists() and (d / ".hermes_session").read_text().strip())
    check(".hermes_session 写入 (≥1)", wd_hm >= 1, f"n={wd_hm}")

    # 6. 无解题不应有 correct 提交
    check("无解题无正确提交", adapter._submits == 0 or not any(
        r.get("flags_correct", 0) >= 1 for r in records.values()),
        f"submits={adapter._submits}")

    print(f"\n===== 结果: {PASS} 通过 / {FAIL} 失败 =====")
    print(f"日志: {cfg.log_file}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
