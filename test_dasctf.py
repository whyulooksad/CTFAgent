#!/usr/bin/env python3
"""测试 DASCTF adapter 拉题 (用法: python3 test_dasctf.py <AccessKey>)"""
import sys
sys.path.insert(0, "master")
from adapters.dasctf import DasctfAdapter

key = sys.argv[1] if len(sys.argv) > 1 else ""
if not key:
    print("用法: python3 test_dasctf.py <你的完整AccessKey>")
    sys.exit(1)

a = DasctfAdapter("https://pro.dasctf.com", key)
chs = a.list_challenges()
print(f"拉题成功: {len(chs)} 道")
for c in chs:
    print(" ", c.id, c.title, c.type)
