#!/usr/bin/env python3
"""临时调试: B→D 顺序跑, 复现 D 卡住并定位日志问题。"""
import faulthandler
import sys
import time
from pathlib import Path

faulthandler.dump_traceback_later(25, exit=True)

sys.path.insert(0, "/home/stw/ctf-agent/scripts")
import test_rotation as T

# 1. 先跑 B (模拟全量顺序)
print("=== 跑 B ===", flush=True)
T.scenario_b()
print("B 完成", flush=True)

# 2. 再跑 D
print("=== 跑 D ===", flush=True)
cfg = T.make_cfg("d")
T.cleanup(cfg)
Path(cfg.state_file).write_text(
    '{"records": {"d1": {"id":"d1","title":"恢复题","type":"crypto","score":300,'
    '"solve_count":10,"description":"重启恢复测试","status":"running",'
    '"attempts":1,"last_done_round":0}}, "saved_at":"test"}',
    encoding="utf-8",
)
T.setup_logging(cfg)
m = T.Master(cfg, adapter=T.TestAdapter(num_fail=0), backend=T.FakeBackend(solve_delay=1.0))
t0 = time.time()
m.run()
print(f"D run() 结束, 耗时 {time.time()-t0:.1f}s", flush=True)
print("D 日志文件行数:", len(Path(cfg.log_file).read_text().splitlines()), flush=True)
