#!/usr/bin/env python3
"""快场景组入口 (B~I 串行, A 已单独验证过): 从 tests/ 加载 test_rotation。"""
import sys

sys.path.insert(0, "/home/stw/ctf-agent/tests")
import test_rotation as T

T.scenario_b()
T.scenario_c()
T.scenario_d()
T.scenario_e()
T.scenario_f()
T.scenario_g()
T.scenario_h()
T.scenario_i()
T.scenario_j()
T.scenario_k()
print(f"快场景组 done, PASS={T.PASS} FAIL={T.FAIL}")
sys.exit(1 if T.FAIL else 0)
