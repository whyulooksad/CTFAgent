#!/usr/bin/env python3
"""
flag 收集设计边界测试 (2026-08-21)

不是测"能不能收集", 而是测"会不会引入新 bug":
  A. 重复收集防护: 同一 flag 跨轮次/多文件出现, jsonl 不重复写
  B. 已 seen flag 过滤: progress.md 已提交的 flag 不会再次进候选 (防重复提交)
  C. 噪音 rejected 后不再触发: Hermes 标记 rejected 的 flag 后续轮不打扰
  D. 多 flag 题: 部分已提交后, 剩余 flag 候选不误伤已提交的
  E. jsonl 损坏容错: 文件损坏/半行时 master 不崩, 正常收集
  F. 收集-审查-补写-提交 全链路: 不直接提交, 审查后命令补写, 补写后正常提交

用法: cd /home/stw/ctf-agent && python3 tests/test_flag_collection_edges.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "master"))
sys.path.insert(0, str(REPO / "solver"))
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


# ───────────────── A. 重复收集防护 ─────────────────
def test_no_dup_candidates():
    print("\n=== A. 重复收集防护 ===")
    from master import Master
    from test_rotation import make_cfg
    from challenge_state import ChallengeRecord

    cfg = make_cfg("fc_edge_dup", max_rounds=3)
    for p in (cfg.state_file, cfg.log_file, cfg.flags_file):
        Path(p).unlink(missing_ok=True)
    m = Master(cfg, adapter=None, backend=None)

    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "board.md").write_text("flag{dup-flag} 出现在 board\n")
        rec = ChallengeRecord(id="t1", title="t", type="web")
        rec.work_dir = str(wd)

        # 第一轮收集
        cands1 = m._collect_flag_candidates(rec)
        n1 = m._append_flag_candidates(rec, cands1)
        check("首轮收集 1 个", n1 == 1, f"n1={n1} cands={cands1}")

        # 第二轮: 同一 flag 又出现在 codex.log (多文件), 不应重复写
        (wd / "codex.log").write_text("agent 说 flag{dup-flag} 在这里\n")
        cands2 = m._collect_flag_candidates(rec)
        check("第二轮扫描到已存在的 flag", any(f == "flag{dup-flag}" for f, _ in cands2))
        n2 = m._append_flag_candidates(rec, cands2)
        check("不重复写 jsonl", n2 == 0, f"n2={n2}")

        # jsonl 只有 1 条
        lines = (wd / "flag_candidates.jsonl").read_text().strip().splitlines()
        check("jsonl 仅 1 条", len(lines) == 1, f"lines={len(lines)}")


# ───────────────── B. 已 seen flag 过滤 ─────────────────
def test_seen_filter():
    print("\n=== B. 已 seen flag 不进候选 ===")
    from master import Master
    from test_rotation import make_cfg
    from challenge_state import ChallengeRecord

    cfg = make_cfg("fc_edge_seen", max_rounds=3)
    for p in (cfg.state_file, cfg.log_file, cfg.flags_file):
        Path(p).unlink(missing_ok=True)
    m = Master(cfg, adapter=None, backend=None)

    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "board.md").write_text("flag{already-submitted} 已提交过\nflag{new-one} 新候选\n")
        rec = ChallengeRecord(id="t1", title="t", type="web")
        rec.work_dir = str(wd)
        rec.flags_seen = {"flag{already-submitted}"}  # 已 seen

        cands = m._collect_flag_candidates(rec)
        check("收集到 2 个原始候选", len(cands) == 2, f"cands={cands}")
        # 主循环过滤 (与 master.py:613 一致)
        filtered = [(f, s) for f, s in cands if f not in rec.flags_seen]
        check("过滤后只剩新 flag", len(filtered) == 1 and filtered[0][0] == "flag{new-one}",
              f"filtered={filtered}")


# ───────────────── C. rejected 不再触发 ─────────────────
def test_rejected_no_trigger():
    print("\n=== C. Hermes rejected 后不再打扰 ===")
    import monitor
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "progress.md").write_text(
            "## Target\n- URL: http://x\n## Current Phase\nrecon\n"
            "## Next Steps\n1. x\n## Flags Found\n(无)\n")
        (wd / "board.md").write_text("# Board\n\n## Ideas\n\n## Memory\n\n")
        (wd / "codex.log").write_text("x")
        # 写候选: 1 条 pending + 1 条 rejected (Hermes 已审查)
        (wd / "flag_candidates.jsonl").write_text(
            '{"flag":"flag{pending-one}","source":"codex.log","status":"pending"}\n'
            '{"flag":"flag{noise}","source":"AGENTS.md","status":"rejected"}\n')
        # 首次: pending 触发
        out1 = monitor.run_monitor(wd)
        check("pending 候选触发", out1 is not None and out1.get("flag_candidates"),
              f"{out1.get('flag_candidates') if out1 else None}")
        if out1:
            check("只带 pending 候选", "flag{pending-one}" in str(out1.get("flag_candidates")),
                  str(out1.get("flag_candidates")))
        # 模拟 Hermes 把 pending 也标 rejected
        (wd / "flag_candidates.jsonl").write_text(
            '{"flag":"flag{pending-one}","source":"codex.log","status":"rejected"}\n'
            '{"flag":"flag{noise}","source":"AGENTS.md","status":"rejected"}\n')
        # 再次 monitor: 无 pending → 不触发
        out2 = monitor.run_monitor(wd)
        check("全 rejected 后不触发", out2 is None or not out2.get("flag_candidates"),
              f"{out2}")


# ───────────────── D. 多 flag 题 ─────────────────
def test_multi_flag_partial():
    print("\n=== D. 多 flag 题部分已提交 ===")
    from master import Master
    from test_rotation import make_cfg
    from challenge_state import ChallengeRecord

    cfg = make_cfg("fc_edge_mf", max_rounds=3)
    for p in (cfg.state_file, cfg.log_file, cfg.flags_file):
        Path(p).unlink(missing_ok=True)
    m = Master(cfg, adapter=None, backend=None)

    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        (wd / "board.md").write_text(
            "flag{first-done} 已提交\nflag{second-found} 新找到\n")
        rec = ChallengeRecord(id="f2", title="多flag", type="web", flag_count=2)
        rec.work_dir = str(wd)
        rec.flags_seen = {"flag{first-done}"}  # 已提交 1 个

        cands = m._collect_flag_candidates(rec)
        filtered = [(f, s) for f, s in cands if f not in rec.flags_seen]
        check("过滤已提交, 剩新 flag", len(filtered) == 1 and filtered[0][0] == "flag{second-found}",
              f"filtered={filtered}")


# ───────────────── E. jsonl 损坏容错 ─────────────────
def test_jsonl_corrupt():
    print("\n=== E. jsonl 损坏容错 ===")
    from master import Master
    from test_rotation import make_cfg
    from challenge_state import ChallengeRecord

    cfg = make_cfg("fc_edge_corrupt", max_rounds=3)
    for p in (cfg.state_file, cfg.log_file, cfg.flags_file):
        Path(p).unlink(missing_ok=True)
    m = Master(cfg, adapter=None, backend=None)

    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        # jsonl 半行损坏 (写入被中断)
        (wd / "flag_candidates.jsonl").write_text(
            '{"flag":"flag{ok-one}","source":"x","status":"pending"}\n'
            '{"flag":"flag{corrupt}","source":"x","statu')  # 截断损坏
        (wd / "board.md").write_text("flag{new-after-corrupt}\n")
        rec = ChallengeRecord(id="t1", title="t", type="web")
        rec.work_dir = str(wd)

        # 不应崩, 正常收集新 flag
        cands = m._collect_flag_candidates(rec)
        check("损坏 jsonl 不崩, 收集到新 flag",
              any(f == "flag{new-after-corrupt}" for f, _ in cands),
              f"cands={cands}")
        n = m._append_flag_candidates(rec, cands)
        # 追加新候选 (损坏行跳过, 不影响) —— 容错正确: 损坏行保留 + 新行追加
        check("追加成功", n >= 1, f"n={n}")
        content = (wd / "flag_candidates.jsonl").read_text()
        check("新 flag 已追加", "flag{new-after-corrupt}" in content, content[-100:])
        check("原损坏行仍在(未被破坏)", "flag{corrupt}" in content, content[:100])


# ───────────────── F. 全链路: 收集→审查→补写→提交 ─────────────────
def test_full_chain_no_direct_submit():
    print("\n=== F. 全链路: 不直接提交, 审查后补写才提交 ===")
    from master import Master
    from test_rotation import make_cfg, FakeBackend, TestAdapter
    import threading

    cfg = make_cfg("fc_edge_chain", max_rounds=3, round_time_base=2,
                   round_time_step=1, max_solvers=1)
    for p in (cfg.state_file, cfg.log_file, cfg.flags_file):
        Path(p).unlink(missing_ok=True)
    adapter = TestAdapter(num_fail=0)
    backend = FakeBackend(solve_delay=0.2)

    orig_sim = backend._simulate

    def real_sim(ch, handle):
        ev = handle.opaque["stop_event"]
        wd = Path(handle.work_dir)
        wd.mkdir(parents=True, exist_ok=True)
        # agent 把 flag 写 board (漏写 progress.md)
        (wd / "progress.md").write_text(
            "## Target\n- URL: http://x\n## Current Phase\nrecon\n"
            "## Next Steps\n1. x\n## Flags Found\n(无)\n")
        (wd / "board.md").write_text(
            "# Board\n\n## Memory\n\n| M1 | fact | 找到 flag: flag{chain-board} |\n")
        ev.wait(5)  # 存活 5s 让 master 收集, 然后结束 (不卡线程)

    backend._simulate = real_sim
    from master import Master as MM
    m = MM(cfg, adapter=adapter, backend=backend)
    t = threading.Thread(target=m.run, daemon=True)
    t.start()
    t.join(15)
    m._stop.set()
    t.join(5)
    backend._simulate = orig_sim

    # 1. 用 jsonl 判断: 收集到候选 (pending, 未提交)
    fake_dir = REPO / "challenges" / "fake"
    jsonls = list(fake_dir.rglob("flag_candidates.jsonl")) if fake_dir.exists() else []
    check("收集到候选 (jsonl)", len(jsonls) >= 1, f"jsonls={len(jsonls)}")
    pending_flags = set()
    for jp in jsonls:
        for ln in jp.read_text(errors="replace").splitlines():
            try:
                d = json.loads(ln)
                if d.get("status") == "pending":
                    pending_flags.add(d.get("flag", ""))
            except Exception:
                continue
    check("候选含 flag{chain-board}", "flag{chain-board}" in pending_flags,
          f"pending={pending_flags}")
    # 2. 未直接提交: progress.md Flags Found 仍是 (无) (cc 没补写前 master 不会提交)
    not_submitted = True
    for jp in jsonls:
        prog = jp.parent / "progress.md"
        if prog.exists() and "flag{chain-board}" in prog.read_text():
            not_submitted = False
    check("未直接提交 (progress 无候选 flag)", not_submitted)

    # 2. 模拟 Hermes 审查: 写 dead_ends 命令补写 + 模拟 cc 补写 progress.md
    for d in fake_dir.iterdir() if fake_dir.exists() else []:
        if (d / "flag_candidates.jsonl").exists():
            (d / "dead_ends.md").write_text(
                "【flag 收集】发现真实 flag{chain-board}（来源: board.md），"
                "你漏写进 progress.md，请立即补写到 Flags Found 段。\n")
            # 模拟 cc 补写 progress.md
            prog = d / "progress.md"
            if prog.exists():
                txt = prog.read_text()
                prog.write_text(txt.replace("## Flags Found\n(无)",
                                            "## Flags Found\nflag{chain-board}\n"))

    # 3. 补写后: _read_flags 能读到 → master 会正常提交 (闭环)
    from master import SolverHandle
    read_ok = False
    for d in fake_dir.iterdir() if fake_dir.exists() else []:
        if (d / "flag_candidates.jsonl").exists():
            h = SolverHandle(cid=d.name, type="web", work_dir=d, started_at=0.0)
            flags = m._read_flags(h)
            if "flag{chain-board}" in flags:
                read_ok = True
    check("补写后 _read_flags 读到 flag", read_ok)

    # 清理 fake work_dir
    import shutil
    if fake_dir.exists():
        shutil.rmtree(fake_dir, ignore_errors=True)


if __name__ == "__main__":
    test_no_dup_candidates()
    test_seen_filter()
    test_rejected_no_trigger()
    test_multi_flag_partial()
    test_jsonl_corrupt()
    test_full_chain_no_direct_submit()
    print(f"\n===== 结果: {PASS} 通过 / {FAIL} 失败 =====")
    sys.exit(1 if FAIL else 0)
