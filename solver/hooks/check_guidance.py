#!/usr/bin/env python3
"""
PostToolUse hook: 检查 guidance.md / dead_ends.md，有新内容则注入给 Codex。

机制（借鉴 CHYing）：
- Hermes 随时往 guidance.md / dead_ends.md 追加内容
- 每次 Codex 工具调用后，本 hook 检查这两个文件
- 有内容 -> 通过 additionalContext 注入给模型 -> 清空文件（读后清空）
- 无内容 -> 静默退出，不占上下文

stdin 收到 PostToolUse 的 JSON，含 cwd 字段指向工作目录。
"""

import json
import os
import sys
from pathlib import Path


def main() -> None:
    # subagent 不消费全局指导（guidance/dead_ends 只给主进程）。
    # branch.py spawn 时注入 CODEX_ROLE=subagent，hook 是 codex 子进程会继承。
    if os.environ.get("CODEX_ROLE") == "subagent":
        return

    try:
        data = json.load(sys.stdin)
    except Exception:
        # 解析失败直接静默退出
        return

    cwd = data.get("cwd", "")
    if not cwd:
        return

    work_dir = Path(cwd)

    parts = []

    # ── guidance.md（软建议：帮找新路）──
    guidance_path = work_dir / "guidance.md"
    try:
        guidance = guidance_path.read_text(encoding="utf-8").strip()
        if guidance:
            parts.append("## 📨 监督者指导 (guidance)\n\n" + guidance)
            guidance_path.write_text("", encoding="utf-8")  # 读后清空
    except Exception:
        pass

    # ── dead_ends.md（硬约束：禁止重试）──
    dead_ends_path = work_dir / "dead_ends.md"
    try:
        dead_ends = dead_ends_path.read_text(encoding="utf-8").strip()
        if dead_ends:
            parts.append("## 🚫 监督者死命令 (dead_ends - 必须遵守)\n\n" + dead_ends)
            dead_ends_path.write_text("", encoding="utf-8")  # 读后清空
    except Exception:
        pass

    if not parts:
        # 无新内容，静默退出
        return

    additional_context = "\n\n".join(parts)

    # 输出 JSON，additionalContext 会注入给模型
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": additional_context,
        }
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
