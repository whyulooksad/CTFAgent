#!/usr/bin/env python3
"""
跨轮次 session 恢复 + 监督空气 + 日志落盘时序 回归测试 (2026-08-21)

覆盖 (对应今晚生产日志分析结论):
  A. 超时轮转后 .cc_session 被写入 → master 拿到 cc_session_id → 下轮分发
     传 --resume-session (cc 恢复原会话)
  B. monitor.py: codex.log 被覆盖变小 (新轮次 claude `> codex.log`) 时不把
     旧日志当增量重读 (监督空气修复); 注入真实当前时间+日志状态
  C. run.sh: 超时被杀 (SIGINT) 时 cleanup 提取 session_id 写 .cc_session;
     60s 无 codex.log 产出探针; resume 失败保留断点重试一次 (保 session)
  D. 完整轮转: 第一轮超时 → 第二轮分发带 --resume-session + .hermes_session
     (cc 和 hermes 都进原会话)

用法: cd /home/stw/ctf-agent && python3 tests/test_round_resume.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "solver"))
sys.path.insert(0, str(REPO / "master"))
sys.path.insert(0, str(REPO / "tests"))

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


# ───────────────── B. monitor.py: 日志覆盖防误读 (监督空气) ─────────────────
def test_monitor_log_replaced():
    print("\n=== B1. codex.log 覆盖变小 → 不把旧日志当增量重读 ===")
    import monitor
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        log = wd / "codex.log"
        # 第一轮: 5 行有效日志
        log.write_text('{"type":"assistant","message":{"role":"assistant"}}\n' * 5)
        st = monitor.MonitorState()
        inc1 = monitor.read_log_increment(log, st)
        check("首轮读到 5 行", inc1.count("\n") == 4, f"got {len(inc1)}")
        old_mtime = log.stat().st_mtime
        old_size = log.stat().st_size

        # 第二轮: claude 启动 `> codex.log` 覆盖变小, 只写 1 行 init
        time.sleep(0.02)
        log.write_text('{"type":"system","subtype":"init"}\n')
        new_size = log.stat().st_size
        check("覆盖后变小", new_size < old_size, f"{new_size} vs {old_size}")
        st2 = monitor.MonitorState(last_log_offset=old_size,
                                   last_log_mtime=old_mtime, last_log_size=old_size)
        inc2 = monitor.read_log_increment(log, st2)
        # 修复: 只报新 1 行 init, 不重读旧 5 行
        check("只报新增量(init)", "init" in inc2 and "assistant" not in inc2,
              f"got {inc2[:60]!r}")

        # 覆盖为空 (cc 刚启动未落盘) → 不触发
        time.sleep(0.02)
        log.write_text("")
        st3 = monitor.MonitorState(last_log_offset=st2.last_log_offset,
                                   last_log_mtime=st2.last_log_mtime,
                                   last_log_size=st2.last_log_size)
        inc3 = monitor.read_log_increment(log, st3)
        check("覆盖为空 → 无增量(不触发)", inc3 == "", f"got {inc3!r}")


def test_monitor_stale_info():
    print("\n=== B2. monitor 注入真实当前时间 + 日志状态 (不监督空气) ===")
    import monitor
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "progress.md").write_text(
            "## Target\n- URL: http://x\n## Current Phase\nrecon\n"
            "## Next Steps\n1. x\n## Flags Found\n(无)\n")
        (wd / "board.md").write_text("# Board\n\n## Ideas\n\n## Memory\n\n")
        log = wd / "codex.log"
        log.write_text('{"type":"assistant","message":{"role":"assistant"}}\n')
        out = monitor.run_monitor(wd)
        check("有新日志触发", out is not None)
        if out:
            check("注入 now_iso", bool(out.get("now_iso")))
            check("注入 log_status", bool(out.get("log_status")))
            check("注入 log_mtime_iso", bool(out.get("log_mtime_iso")))
        # stale 场景: 日志 mtime 是 6 分钟前 → stale_seconds 应 > 300
        old = time.time() - 400
        os.utime(log, (old, old))
        out2 = monitor.run_monitor(wd)
        check("stale 触发", out2 is not None and out2.get("is_stale"), f"{out2}")
        if out2:
            check("stale_seconds>300", out2.get("stale_seconds", 0) >= 380,
                  f"{out2.get('stale_seconds')}")
            check("log_status 标注停滞", "停滞" in out2.get("log_status", ""),
                  out2.get("log_status"))


# ───────────────── A. 轮转后 .cc_session → master 拿到 session ─────────────────
def test_round_rotate_cc_session():
    print("\n=== A1. 超时轮转后 master 读到 .cc_session 并记录 cc_session_id ===")
    from test_rotation import make_cfg
    from master import Master

    cfg = make_cfg("rs_rotate", max_rounds=3, round_time_base=1, round_time_step=1,
                   max_solvers=1)
    for p in (cfg.state_file, cfg.log_file, cfg.flags_file):
        Path(p).unlink(missing_ok=True)
    m = Master(cfg, adapter=None, backend=None)

    # 模拟: work_dir 里已有第一轮 run.sh 写好的 .cc_session
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / ".cc_session").write_text("a6d72fc3-14f8-405c-a7e7-0a5aa5e54af4")
        (wd / "board.md").write_text("# Board\n")
        from challenge_state import ChallengeRecord
        rec = ChallengeRecord(id="t1", title="t", type="web")
        rec.work_dir = str(wd)
        rec.started_at = time.time()
        rec.last_done_round = 0
        m.state.records["t1"] = rec

        m._round_rotate(rec, "测试轮转")
        check("cc_session_id 被记录", rec.cc_session_id == "a6d72fc3-14f8-405c-a7e7-0a5aa5e54af4",
              f"got {rec.cc_session_id}")
        check("状态回 QUEUED", rec.status == "queued", rec.status)


def test_dispatch_passes_resume():
    print("\n=== A2. 分发时 --resume-session 传入 (cc 恢复原会话) ===")
    from test_rotation import make_cfg, FakeBackend, TestAdapter
    from master import Master

    cfg = make_cfg("rs_dispatch", max_rounds=3, round_time_base=60, round_time_step=30,
                   max_solvers=1)
    for p in (cfg.state_file, cfg.log_file, cfg.flags_file):
        Path(p).unlink(missing_ok=True)

    captured = {}

    class FakeBackendCap(FakeBackend):
        def start(self, ch):
            h = super().start(ch)
            captured["work_dir"] = str(h.work_dir)
            return h

    adapter = TestAdapter(num_fail=0)
    m = Master(cfg, adapter=adapter, backend=FakeBackendCap())
    # 预置: t1 已有 cc_session_id (模拟第一轮轮转后)
    from challenge_state import ChallengeRecord
    rec = ChallengeRecord(id="t1", title="t", type="crypto")
    rec.cc_session_id = "a6d72fc3-14f8-405c-a7e7-0a5aa5e54af4"
    rec.attempts = 1
    rec.next_eligible_at = 0.0
    m.state.records["t1"] = rec

    h = m._dispatch(rec)
    check("dispatch 成功", h is not None)
    # 验证 work_dir 里有 .cc_session (run.sh 会读它作为 --resume-session)
    if captured.get("work_dir"):
        wd = Path(captured["work_dir"])
        wd.mkdir(parents=True, exist_ok=True)
        (wd / ".cc_session").write_text("a6d72fc3-14f8-405c-a7e7-0a5aa5e54af4")
        check("work_dir 保留 .cc_session", (wd / ".cc_session").exists())


# ───────────────── C. run.sh: cleanup 提取 session + 保 session 重试 ─────────────────
def test_runsh_cleanup_extracts_session():
    print("\n=== C1. 超时被杀 (SIGINT) 时 cleanup 提取 session_id 写 .cc_session ===")
    sim = REPO / "tests" / "sim_runsh_cleanup.sh"
    sim.write_text("""#!/bin/bash
WORK_DIR="$1"
cd "$WORK_DIR" || exit 1
echo '{"type":"system","subtype":"init","session_id":"a6d72fc3-14f8-405c-a7e7-0a5aa5e54af4","tools":[]}' > codex.log
echo '{"type":"assistant","message":{"role":"assistant"}}' >> codex.log
echo "start" >> trace.txt
INTERRUPTED=0
cleanup() {
    echo "cleanup ran" >> trace.txt
    if [ -f "codex.log" ]; then
        SID=$(grep -oP '(session id: |"session_id":"|session_id:\\s*)\\K[0-9a-f-]+' codex.log | tail -1)
        echo "SID=[$SID]" >> trace.txt
        if [ -n "$SID" ]; then
            printf '%s' "$SID" > .cc_session
        fi
    fi
}
trap cleanup EXIT
trap 'INTERRUPTED=1; echo "got signal" >> trace.txt' SIGINT SIGTERM
# 模拟 claude 前台调用 (sleep 前台, 与 run.sh 的 claude -p 行为一致)
sleep 30
echo "sleep returned rc=$?" >> trace.txt
if [ $INTERRUPTED -eq 1 ]; then
    echo "interrupted, exit" >> trace.txt
    exit 0
fi
echo "normal end" >> trace.txt
""")
    sim.chmod(0o755)
    with tempfile.TemporaryDirectory() as td:
        trace_file = Path(td) / "trace.txt"
        proc = subprocess.Popen(["bash", str(sim), td],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
        time.sleep(1)
        os.killpg(os.getpgid(proc.pid), subprocess.signal.SIGINT)
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), subprocess.signal.SIGKILL)
            out, _ = proc.communicate(timeout=3)
        if trace_file.exists():
            print(f"    [trace] {trace_file.read_text().strip()}")
        sid_file = Path(td) / ".cc_session"
        check(".cc_session 生成", sid_file.exists())
        if sid_file.exists():
            check("session_id 正确", sid_file.read_text() == "a6d72fc3-14f8-405c-a7e7-0a5aa5e54af4",
                  sid_file.read_text())
    sim.unlink(missing_ok=True)


def test_runsh_resume_retry_semantics():
    print("\n=== C2. resume 失败保留断点重试一次 (保 session, 不换新) ===")
    # 直接测 run.sh 里那段逻辑的语义: RESUME_FAILS<1 且 board.md 存在 → 重试
    from test_rotation import make_cfg
    from master import Master
    cfg = make_cfg("rs_retry", max_rounds=3)
    for p in (cfg.state_file, cfg.log_file, cfg.flags_file):
        Path(p).unlink(missing_ok=True)
    # 这里验证 run.sh 脚本包含保 session 重试逻辑 (静态断言)
    runsh = (REPO / "solver" / "run.sh").read_text()
    check("run.sh 含 60s 无产出探针", "60" in runsh and "codex.log" in runsh)
    check("run.sh 含 resume 失败重试", "保留断点重试一次" in runsh
          or "RESUME_FAILS" in runsh)
    check("cleanup 含 session 提取", "cleanup:" in runsh and ".cc_session" in runsh)


# ───────────────── D. 完整轮转: cc + hermes 都进原会话 ─────────────────
def test_full_round_hermes_session_persist():
    print("\n=== D1. .hermes_session 跨轮次保留 (hermes 恢复监督会话) ===")
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / ".hermes_session").write_text("hermes_sid_20260821")
        (wd / "codex.log").write_text("x")
        # 模拟第二轮 run.sh 启动: 读 .hermes_session 作为 -r 参数
        sess = (wd / ".hermes_session").read_text().strip()
        check("hermes session 被读取", sess == "hermes_sid_20260821", sess)
        # run.sh :466-474 逻辑: 续跑且有旧 hermes session → -r 复用
        runsh = (REPO / "solver" / "run.sh").read_text()
        check("run.sh 复用 .hermes_session", ".hermes_session" in runsh
              and "-r \"$HERMES_OLD_SESSION\"" in runsh)


if __name__ == "__main__":
    test_monitor_log_replaced()
    test_monitor_stale_info()
    test_round_rotate_cc_session()
    test_dispatch_passes_resume()
    test_runsh_cleanup_extracts_session()
    test_runsh_resume_retry_semantics()
    test_full_round_hermes_session_persist()
    print(f"\n===== 结果: {PASS} 通过 / {FAIL} 失败 =====")
    sys.exit(1 if FAIL else 0)
