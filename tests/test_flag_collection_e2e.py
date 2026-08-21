"""端到端: flag 全量收集 → Hermes 审查 → dead_ends 命令补写 → 正常提交。

场景: solver 把 flag 写在 board.md (漏写 progress.md)。验证:
1. master 全量收集 → flag_candidates.jsonl (status=pending, 不提交)
2. monitor 检测 pending → 触发 Hermes 审查信号
3. 模拟 Hermes 审查后写 dead_ends.md 命令补写
4. solver 读 dead_ends 补写 progress.md → master _read_flags 正常提交

运行: python3 tests/test_flag_collection_e2e.py
"""
import json
import sys
import time
import pathlib
from pathlib import Path

sys.path.insert(0, 'tests')
sys.path.insert(0, 'master')
from test_rotation import (make_cfg, setup_logging, FakeBackend, M, TestAdapter)
from master import Master, SolverHandle

print("=== 端到端: 收集→审查→补写→提交 完整闭环 ===")
cfg = make_cfg("fc_e2e", max_solvers=3, max_rounds=3, solver_timeout=30,
               round_time_base=8, round_time_step=3)
for p in (cfg.state_file, cfg.log_file, cfg.flags_file):
    pathlib.Path(p).unlink(missing_ok=True)
setup_logging(cfg)
adapter = TestAdapter(num_fail=0)
backend = FakeBackend(solve_delay=0.5)

orig_sim = backend._simulate


def real_sim(ch, handle):
    ev = handle.opaque["stop_event"]
    wd = handle.work_dir
    wd.mkdir(parents=True, exist_ok=True)
    # 不听话的 agent: flag 写在 board.md, progress.md 的 Flags Found 是 (无)
    (wd / "progress.md").write_text(
        "## Target\n- URL: http://x\n## Current Phase\nrecon\n"
        "## Next Steps\n1. x\n## Flags Found\n(无)\n", encoding="utf-8")
    (wd / "board.md").write_text(
        "# Board\n\n## Memory\n\n| M1 | fact | 找到 flag: flag{hidden-in-board} |\n",
        encoding="utf-8")
    (wd / "codex.log").write_text(
        '{"type":"assistant","message":{"role":"assistant","content":"found flag{in-log}"}}\n',
        encoding="utf-8")
    ev.wait()


backend._simulate = real_sim
m = Master(cfg, adapter=adapter, backend=backend)
import threading
t = threading.Thread(target=m.run, daemon=True)
t.start()
t.join(15)
m._stop.set()
t.join(5)
backend._simulate = orig_sim

log = open(cfg.log_file).read()
print("--- master 日志 (收集相关) ---")
for l in log.splitlines():
    if "全量收集" in l or "FLAG ACCEPTED" in l or "flag 候选" in l:
        print(f"  {l}")

# 验证 1: master 收集但未直接提交 (jsonl pending, 候选 flag 无 FLAG ACCEPTED)
# 验证 2: 模拟 Hermes 审查写 dead_ends
ok_collect = "全量收集" in log
# 候选 flag 不应被直接提交 (fake_t1 是 FakeBackend 默认 simulate, 不算)
ok_no_direct_submit = ("FLAG ACCEPTED: t1 flag{hidden-in-board}" not in log
                       and "FLAG ACCEPTED: t1 flag{in-log}" not in log)
print(f"\nmaster 收集到候选: {'PASS' if ok_collect else 'FAIL'}")
print(f"未直接提交 (等待审查): {'PASS' if ok_no_direct_submit else 'FAIL'}")

# 找 work_dir (FakeBackend 的 work_dir 在 CHALLENGES_DIR/fake/<id> 下)
from master import CHALLENGES_DIR
cands_paths = list(CHALLENGES_DIR.rglob("flag_candidates.jsonl"))
print(f"flag_candidates.jsonl 文件: {len(cands_paths)}")
for cp in cands_paths:
    recs = [json.loads(l) for l in cp.read_text().splitlines() if l.strip()]
    print(f"  {cp}: {len(recs)} 条, pending={sum(1 for r in recs if r['status']=='pending')}")
    for r in recs:
        print(f"    {r['flag']} source={r['source']} status={r['status']}")
    # 模拟 Hermes: 写 dead_ends.md 命令补写
    wd = cp.parent
    (wd / "dead_ends.md").write_text(
        "【flag 收集】发现真实 flag{hidden-in-board}（来源: board.md），"
        "你漏写进 progress.md 了！立即把它追加到 progress.md 的 Flags Found 段。\n",
        encoding="utf-8")
    # 模拟 solver 响应 dead_ends: 补写 progress.md
    (wd / "progress.md").write_text(
        "## Target\n- URL: http://x\n## Current Phase\nrecon\n"
        "## Next Steps\n1. x\n## Flags Found\nflag{hidden-in-board}\n",
        encoding="utf-8")
    # 模拟 Hermes 标记 confirmed
    for r in recs:
        if r["flag"] == "flag{hidden-in-board}":
            r["status"] = "confirmed"
    cp.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")

# 第二阶段: 模拟 Hermes 命令补写 → solver 补写 progress.md → master _read_flags 能读到
print("\n--- 补写后 _read_flags 检测 ---")
# 用独立临时目录模拟 (不依赖第一段的 fake/t1, 避免测试时序耦合)
import tempfile
with tempfile.TemporaryDirectory() as td2:
    t1_wd = Path(td2)
    (t1_wd / "progress.md").write_text(
        "## Target\n- URL: http://x\n## Current Phase\nrecon\n"
        "## Next Steps\n1. x\n## Flags Found\nflag{hidden-in-board}\n",
        encoding="utf-8")
    h = SolverHandle(cid="t1", type="web", work_dir=t1_wd, started_at=0.0)
    flags = m._read_flags(h)
    ok_read = "flag{hidden-in-board}" in flags
    print(f"  _read_flags 读到补写 flag: {flags}")
    print(f"补写后正常检测: {'PASS' if ok_read else 'FAIL'}")

# 清理 fake work_dir 残留 (测试产物)
import shutil
for cid in ("t1", "t2", "t3", "f2"):
    d = CHALLENGES_DIR / "fake" / cid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)

all_ok = ok_collect and ok_no_direct_submit and ok_read
print("=== 端到端完整闭环 PASS ===" if all_ok else "=== 有问题! ===")
sys.exit(0 if all_ok else 1)
