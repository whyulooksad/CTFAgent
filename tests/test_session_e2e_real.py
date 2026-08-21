#!/usr/bin/env python3
"""
真实端到端: cc (claude) 跨轮次 session 恢复测试 (2026-08-21)

真的起 claude (宿主机 deepseek 配置), 验证:
  1. 第一轮: claude -p 建 session, stream-json 写 codex.log
  2. 模拟超时杀 (SIGINT) → run.sh cleanup 提取 session_id 写 .cc_session
  3. 第二轮: claude -p --resume <sid> 恢复, 验证上下文连续 (记得第一轮的事)
  4. hermes: 起 hermes chat 建 session (.hermes_session), -r 恢复

注意: 会真实调用 deepseek API (少量费用)。
用法: cd /home/stw/ctf-agent && python3 tests/test_session_e2e_real.py
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "solver"))

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


CC = "claude"
PROMPT1 = ("请用一句话回答: 你的任务代号是什么? 记住这句话: 秘密口令是 orange-banana-42。"
           "回答'已记住'即可, 不要执行任何命令。")
PROMPT2 = ("请回答: 1) 你的任务代号是什么? 2) 秘密口令是什么? 只回答这两个问题的答案。")


def run_claude(args, timeout=90):
    """跑 claude, 返回 (exit_code, stdout)。"""
    try:
        proc = subprocess.run([CC] + args, capture_output=True, text=True,
                              timeout=timeout)
        return proc.returncode, proc.stdout
    except subprocess.TimeoutExpired:
        return -1, ""


def test_cc_session_roundtrip():
    print("\n=== 真实 claude: 第一轮建 session → 提取 → 第二轮 resume ===")
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        # 第一轮: claude -p 建 session (stream-json 需 --verbose, 与 run.sh 一致)
        print("  [1] 第一轮 claude 建 session...")
        rc, out = run_claude(["-p", "--dangerously-skip-permissions", "--verbose",
                              "--output-format", "stream-json", PROMPT1])
        check("第一轮 claude 退出 0", rc == 0, f"rc={rc}")
        # 从 stream-json 输出提取 session_id (run.sh 用 codex.log, 这里直接 stdout)
        m = re.search(r'"session_id":"([0-9a-f-]{36})"', out)
        check("提取到 session_id", m is not None)
        if not m:
            return
        sid1 = m.group(1)
        print(f"  [2] 第一轮 session_id: {sid1}")
        # 模拟 run.sh cleanup 写 .cc_session
        (wd / ".cc_session").write_text(sid1)
        check(".cc_session 写入", (wd / ".cc_session").read_text() == sid1)

        # 第二轮: resume 原 session
        print("  [3] 第二轮 claude --resume 恢复...")
        rc2, out2 = run_claude(["-p", "--dangerously-skip-permissions", "--verbose",
                                "--output-format", "stream-json",
                                "--resume", sid1, PROMPT2])
        check("第二轮 resume 退出 0", rc2 == 0, f"rc={rc2}")
        # 验证 resume 成功: 输出里应包含第一轮的答案 (上下文连续)
        check("resume 后上下文连续 (记得秘密口令)",
              "orange-banana-42" in out2, out2[-200:])
        # 提取第二轮的 session_id (应相同)
        m2 = re.search(r'"session_id":"([0-9a-f-]{36})"', out2)
        check("resume 后 session_id 一致", m2 is not None and m2.group(1) == sid1,
              f"got {m2.group(1) if m2 else None}")


def test_hermes_session_roundtrip():
    print("\n=== 真实 hermes: 建 session → .hermes_session → -r 恢复 ===")
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        # 第一轮: hermes chat 建 session (非交互 -q, 用 --pass-session-id 拿 sid)
        print("  [1] 第一轮 hermes chat 建 session...")
        try:
            proc = subprocess.run(
                ["hermes", "chat", "-q",
                 "请回答: 你的监督代号是什么? 记住: 监督口令是 blue-apple-77",
                 "--pass-session-id", "--ignore-user-config"],
                capture_output=True, text=True, timeout=180)
            rc = proc.returncode
            out = proc.stdout + proc.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"  [skip] hermes 不可用: {e}")
            check("hermes 可用 (跳过)", True)
            return
        check("第一轮 hermes 退出 0", rc == 0, f"rc={rc} out={out[-150:]}")
        # 提取 session_id (hermes 输出 "Resume this session with: hermes --resume <sid>")
        m = re.search(r'--resume\s+(\S+)', out)
        if not m:
            m = re.search(r'[sS]ession[ _]?[iI]d:?\s*(\S+)', out)
        check("提取 hermes session_id", m is not None, out[-300:])
        if not m:
            return
        sid1 = m.group(1).strip().strip('",')
        print(f"  [2] 第一轮 hermes session_id: {sid1}")
        (wd / ".hermes_session").write_text(sid1)

        # 第二轮: hermes -r 恢复
        print("  [3] 第二轮 hermes -r 恢复...")
        try:
            proc2 = subprocess.run(
                ["hermes", "chat", "-q", "请回答: 监督口令是什么?",
                 "-r", sid1, "--ignore-user-config"],
                capture_output=True, text=True, timeout=180)
            out2 = proc2.stdout + proc2.stderr
        except subprocess.TimeoutExpired:
            out2 = ""
        # hermes 恢复后输出会话摘要, 断言: 恢复的是同一个 session id
        m2 = re.search(r'--resume\s+(\S+)', out2)
        check("hermes -r 恢复同一 session", m2 is not None and m2.group(1) == sid1,
              f"got {m2.group(1) if m2 else '?'} want {sid1}")
        check("hermes -r 有会话摘要输出", "Session:" in out2, out2[-200:])


if __name__ == "__main__":
    print("真实端到端 session 恢复测试 (会调用 deepseek API, 少量费用)")
    test_cc_session_roundtrip()
    test_hermes_session_roundtrip()
    print(f"\n===== 结果: {PASS} 通过 / {FAIL} 失败 =====")
    sys.exit(1 if FAIL else 0)
