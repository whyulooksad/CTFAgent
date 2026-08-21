"""测试: monitor.py 修复 (offset 防误读 + 真实时间注入) + flag 全量收集链路。

覆盖 2026-08-21 三项改动:
1. monitor.py: offset 归零防误读旧日志 + 注入真实当前时间/日志状态
2. flag 全量收集: extract_flags_all + master._collect_flag_candidates 写 jsonl
   (不直接提交, 交 Hermes 审查)
3. monitor.py: check_flag_candidates 检测 pending 候选触发 Hermes 审查

运行: python3 tests/test_flag_collection.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "solver")
sys.path.insert(0, "master")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


# ───────────────── 1. monitor.py: offset 防误读 ─────────────────
import monitor


def test_monitor_offset_reuse():
    """旧 bug: 第二轮 codex.log 被覆盖变小 → offset 归零 → 旧日志被当增量重读。
    新逻辑: 覆盖后若新内容为空 → 不触发; 有新内容 → 只报新增量。"""
    print("\n=== monitor.py: offset 防误读 ===")
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        log = wd / "codex.log"
        # 第一轮: 写日志, 读一次 (offset 推进)
        log.write_text('{"type":"assistant","message":{"role":"assistant"}}\n' * 5)
        st = monitor.MonitorState()
        inc1 = monitor.read_log_increment(log, st)
        check("首轮增量 5 行", inc1.count("\n") == 4, f"got {len(inc1)}")
        check("offset 推进", st.last_log_offset == log.stat().st_size)
        old_mtime = log.stat().st_mtime
        old_size = log.stat().st_size

        # 模拟第二轮: 覆盖写 (变小), 内容只有 init
        time.sleep(0.02)
        log.write_text('{"type":"system","subtype":"init"}\n')
        new_size = log.stat().st_size
        check("覆盖后变小", new_size < old_size, f"{new_size} vs {old_size}")
        st2 = monitor.MonitorState(last_log_offset=old_size,
                                   last_log_mtime=old_mtime,
                                   last_log_size=old_size)
        inc2 = monitor.read_log_increment(log, st2)
        # 新内容 1 行 → 应返回 1 行 (不是重读旧 5 行)
        check("覆盖后只报新增量(1行)", inc2.count("\n") == 0 and "init" in inc2,
              f"got: {inc2[:80]!r}")
        check("offset 重置为 0 后读到新内容", st2.last_log_offset == new_size)

        # 覆盖为空 → 不触发 (增量空)
        time.sleep(0.02)
        log.write_text("")
        st3 = monitor.MonitorState(last_log_offset=st2.last_log_offset,
                                   last_log_mtime=st2.last_log_mtime,
                                   last_log_size=st2.last_log_size)
        inc3 = monitor.read_log_increment(log, st3)
        check("覆盖为空 → 无增量", inc3 == "", f"got {inc3!r}")


def test_monitor_inject_time():
    """新逻辑: 输出注入 now_iso / log_mtime_iso / log_status。"""
    print("\n=== monitor.py: 真实时间注入 ===")
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "progress.md").write_text(
            "## Target\n- URL: http://x\n## Current Phase\nrecon\n"
            "## Next Steps\n1. x\n## Flags Found\n(无)\n")
        (wd / "board.md").write_text("# Board\n\n## Ideas\n\n## Memory\n\n")
        log = wd / "codex.log"
        log.write_text('{"type":"assistant","message":{"role":"assistant"}}\n')
        out = monitor.run_monitor(wd)
        check("触发 (有新日志)", out is not None)
        if out:
            check("注入 now_iso", "now_iso" in out and out["now_iso"])
            check("注入 log_status", "log_status" in out and "新日志" in out["log_status"])
            check("注入 log_mtime_iso", "log_mtime_iso" in out)
        # stale 场景: 日志旧 6 分钟
        old = time.time() - 400
        os.utime(log, (old, old))
        out2 = monitor.run_monitor(wd)
        check("stale 触发", out2 is not None and out2["is_stale"], f"{out2}")
        if out2:
            check("stale 秒数", out2["stale_seconds"] >= 380,
                  f"{out2['stale_seconds']}")
            check("log_status 标注停滞", "停滞" in out2["log_status"],
                  out2["log_status"])


# ───────────────── 2. flag 全量收集 ─────────────────
from challenge_state import extract_flags_all


def test_extract_flags_all():
    print("\n=== extract_flags_all 全量提取 ===")
    text = """
    正常段: flag{abc123}
    board: FLAG{board-flag} 和 ctf{ctf-flag}
    日志: {"message":"flag{in-log}"}
    SCTF{prefix-flag} 不应被截成 CTF 子串 (前缀边界, S 是字母)
    中文: flag{中文flag} 应该被提取
    """
    flags = extract_flags_all(text)
    check("提取 5 个", len(flags) == 5, f"got {flags}")
    check("含 flag{abc123}", "flag{abc123}" in flags)
    check("含 FLAG{board-flag}", "FLAG{board-flag}" in flags)
    check("含 ctf{ctf-flag}", "ctf{ctf-flag}" in flags)
    check("含 flag{in-log}", "flag{in-log}" in flags)
    check("含 flag{中文flag}", "flag{中文flag}" in flags)
    # 去重
    flags2 = extract_flags_all("flag{x} flag{x} FLAG{x}")
    check("去重保持大小写", flags2 == ["flag{x}", "FLAG{x}"], f"got {flags2}")


def test_collect_flag_candidates():
    """master._collect_flag_candidates + _append_flag_candidates:
    扫描 work_dir 捞 flag, 写 jsonl (带来源, status=pending, 不提交)。"""
    print("\n=== _collect_flag_candidates 全量扫描 + 写 jsonl ===")
    from master import Master
    from test_rotation import make_cfg
    from challenge_state import ChallengeRecord
    cfg = make_cfg("fc")
    m = Master(cfg, adapter=None, backend=None)
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "progress.md").write_text(
            "## Flags Found\nflag{progress-flag}\n")
        (wd / "board.md").write_text("hermes 记录: flag{board-flag}\n")
        (wd / "codex.log").write_text('{"message":"flag{in-log}"}\n')
        (wd / "codex_round1.log").write_text("flag{round-flag}\n")
        (wd / "branch_result_1.md").write_text("subagent 找到: flag{sub-flag}\n")
        (wd / "poc.py").write_text("# exploit\nprint('flag{py-flag}')\n")
        (wd / "output.bin").write_bytes(b"\x00\x01\x02flag{binary-flag}")
        rec = ChallengeRecord(id="t1", title="t", type="web")
        rec.work_dir = str(wd)
        cands = m._collect_flag_candidates(rec)
        check("收集到 6 个", len(cands) == 6, f"got {cands}")
        for f in ["flag{progress-flag}", "flag{board-flag}", "flag{in-log}",
                  "flag{round-flag}", "flag{sub-flag}", "flag{py-flag}"]:
            check(f"含 {f}", f in [c[0] for c in cands], f"got {cands}")
        check("不含二进制 flag", "flag{binary-flag}" not in [c[0] for c in cands])

        # _append_flag_candidates 写 jsonl
        n = m._append_flag_candidates(rec, cands)
        check("写 jsonl 6 条", n == 6, f"n={n}")
        jl = (wd / "flag_candidates.jsonl").read_text()
        check("jsonl 含来源", "board.md" in jl, jl[:200])
        check("jsonl status=pending", '"status": "pending"' in jl)
        # 重复调用不重复写
        n2 = m._append_flag_candidates(rec, m._collect_flag_candidates(rec))
        check("重复收集不重复写", n2 == 0, f"n2={n2}")


def test_monitor_flag_candidates_trigger():
    """monitor.check_flag_candidates: 新 pending 候选触发 Hermes, 已通知的不重复。"""
    print("\n=== monitor.py: flag 候选触发 ===")
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        jl = wd / "flag_candidates.jsonl"
        jl.write_text(json.dumps({"flag": "flag{test}",
                                  "source": "board.md",
                                  "collected_at": "2026-08-21T07:00:00",
                                  "status": "pending"}) + "\n")
        st = monitor.MonitorState()
        pend = monitor.check_flag_candidates(wd, st)
        check("首次触发返回 pending", len(pend) == 1 and pend[0]["flag"] == "flag{test}",
              f"{pend}")
        pend2 = monitor.check_flag_candidates(wd, st)
        check("二次不重复触发", pend2 == [], f"{pend2}")
        # 新增 1 条 → 再触发
        jl.write_text(jl.read_text() + json.dumps({"flag": "flag{new}",
                                                   "source": "codex.log",
                                                   "collected_at": "2026-08-21T07:01:00",
                                                   "status": "pending"}) + "\n")
        pend3 = monitor.check_flag_candidates(wd, st)
        check("新增候选再触发", len(pend3) == 2, f"{pend3}")
        # 处理完 (status 改 confirmed) → 不再触发
        lines = [json.loads(l) for l in jl.read_text().splitlines() if l.strip()]
        for r in lines:
            r["status"] = "confirmed"
        jl.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
        st2 = monitor.MonitorState()
        pend4 = monitor.check_flag_candidates(wd, st2)
        check("confirmed 后不触发", pend4 == [], f"{pend4}")


if __name__ == "__main__":
    test_monitor_offset_reuse()
    test_monitor_inject_time()
    test_extract_flags_all()
    test_collect_flag_candidates()
    test_monitor_flag_candidates_trigger()
    print(f"\n===== 结果: {PASS} 通过 / {FAIL} 失败 =====")
    sys.exit(1 if FAIL else 0)
