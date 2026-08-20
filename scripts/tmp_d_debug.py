#!/usr/bin/env python3
"""临时调试: D 场景卡死定位。"""
import faulthandler
import sys
from pathlib import Path

faulthandler.dump_traceback_later(20, exit=True)  # 20s 后转储所有线程栈

sys.path.insert(0, "/home/stw/ctf-agent/scripts")
import test_rotation as T

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
print("Master 构造完成，开始 run()...", flush=True)
m.run()
print("run() 结束", flush=True)
