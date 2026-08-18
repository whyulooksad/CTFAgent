#!/usr/bin/env python3
"""
monitor.py -- Hermes 的眼睛。

持续 tail codex.log，每次把最新日志增量 + progress.md 状态输出给 Hermes agent。
Hermes agent 看到这些信息后，自己判断该不该给建议(guidance.md)、该不该搜(CTF WP/CVE)、该不该拦(dead_ends.md)。

有新日志 -> 输出 JSON (触发 Hermes agent 介入)
无新日志但日志停滞 >5min -> 输出 stale 信号 (Codex 可能卡住)
无新日志且正常 -> 静默 (不触发 agent, 省 token)

Usage:
  python3 monitor.py --work-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

# ───────────────────────── Config ─────────────────────────

STALE_LOG_SECONDS = 300  # 日志无更新超过 5 分钟 -> stale 信号
TIMEOUT_SECONDS = 7200  # 整体超时 2 小时
STATE_FILE = "monitor_state.json"

FLAG_PATTERN = re.compile(r"(?:flag|ctf)\{[^}]+\}", re.IGNORECASE)

# board.md 容量上限 (与 hermes_monitor.md 一致): 超限触发 Hermes 全量整理
BOARD_MEMORY_LIMIT = 25
BOARD_IDEA_LIMIT = 15

# ───────────────────────── Data Models ─────────────────────────


@dataclass
class MonitorState:
    """跨轮次持久化的状态。"""
    last_log_offset: int = 0  # 上次读到的 codex.log 位置
    start_time: float = 0.0
    board_over_notified: bool = False  # board 超限已通知过 Hermes (防每 10s 空转触发)
    last_branch_mtime: float = 0.0  # 上次检查的 branch_result_*.md 最晚 mtime (新增结果触发)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MonitorState":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


# ───────────────────────── Parsers ─────────────────────────


def parse_progress(path: Path) -> dict:
    """解析 progress.md，提取结构化字段。"""
    if not path.exists():
        return {}
    text = path.read_text()
    result = {}

    phase_match = re.search(r"##\s*Current Phase\s*\n(.+)", text)
    result["phase"] = phase_match.group(1).strip() if phase_match else ""

    ns_match = re.search(r"##\s*Next Steps\s*\n(.*?)(?:\n##|\Z)", text, re.DOTALL)
    result["next_steps"] = ns_match.group(1).strip() if ns_match else ""

    flag_match = re.search(r"##\s*Flags Found\s*\n(.*?)(?:\n##|\Z)", text, re.DOTALL)
    result["flags"] = flag_match.group(1).strip() if flag_match else ""

    url_match = re.search(r"URL:\s*(.+)", text)
    result["url"] = url_match.group(1).strip() if url_match else ""

    time_match = re.search(r"Start Time:\s*(.+)", text)
    result["start_time"] = time_match.group(1).strip() if time_match else ""

    return result


def parse_board(path: Path) -> dict:
    """解析 board.md，统计 Memory/Idea 条数 (容量整理触发依据)。"""
    if not path.exists():
        return {"memory_count": 0, "idea_count": 0}
    text = path.read_text(encoding="utf-8", errors="replace")
    memory_count = len(re.findall(r"^\|\s*M\d+", text, re.M))
    idea_count = len(re.findall(r"^\|\s*I\d+", text, re.M))
    return {"memory_count": memory_count, "idea_count": idea_count}


def read_log_increment(path: Path, state: MonitorState) -> str:
    """读取 codex.log 的增量内容 (从上次位置到现在)。"""
    if not path.exists():
        return ""
    size = path.stat().st_size
    # 日志被截断或重置 (新轮次 codex.log 被覆盖)
    if size < state.last_log_offset:
        state.last_log_offset = 0
    offset = state.last_log_offset
    increment = ""
    if size > offset:
        with open(path, "r", errors="replace") as f:
            f.seek(offset)
            increment = f.read()
        state.last_log_offset = size
    return increment


def check_flag_found(progress: dict, log_increment: str) -> Optional[str]:
    """快速检测 flag 是否出现 (在 Flags Found 段或日志增量里)。"""
    for source in [progress.get("flags", ""), log_increment]:
        match = FLAG_PATTERN.search(source)
        if match:
            return match.group(0)
    return None


def check_branch_results(work_dir: Path, state: MonitorState) -> bool:
    """检测是否有新增/更新的 branch_result_*.md (subagent 完成试探)。

    branch_result 里可能有 subagent 找到的 flag——主进程 codex.log 无新日志时
    monitor 不会触发，Hermes 就发现不了 branch_result 的 flag (b-02 断链场景)。
    这里把 branch_result 变化也作为触发信号。
    """
    files = sorted(work_dir.glob("branch_result_*.md"))
    if not files:
        state.last_branch_mtime = 0.0
        return False
    latest = max(f.stat().st_mtime for f in files)
    changed = latest > state.last_branch_mtime
    state.last_branch_mtime = latest
    return changed


# ───────────────────────── State Persistence ─────────────────────────


def load_state(work_dir: Path) -> MonitorState:
    path = work_dir / STATE_FILE
    if path.exists():
        try:
            return MonitorState.from_dict(json.loads(path.read_text()))
        except (json.JSONDecodeError, TypeError):
            pass
    return MonitorState()


def save_state(work_dir: Path, state: MonitorState) -> None:
    path = work_dir / STATE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    tmp.replace(path)


# ───────────────────────── Main ─────────────────────────


def run_monitor(work_dir: Path) -> Optional[dict]:
    """
    执行一次监控，返回输出给 Hermes agent 的 JSON dict。
    返回 None 表示无需输出 (静默)。
    """
    state = load_state(work_dir)

    if state.start_time == 0.0:
        state.start_time = time.time()

    progress = parse_progress(work_dir / "progress.md")
    log_path = work_dir / "codex.log"

    # board.md 容量统计 (超限时触发 Hermes 全量整理)
    board = parse_board(work_dir / "board.md")
    board_over_limit = (
        board["memory_count"] > BOARD_MEMORY_LIMIT
        or board["idea_count"] > BOARD_IDEA_LIMIT
    )
    # 超限只通知一次 (Hermes 整理前不反复触发 hermes chat 空转); 回落后重置可再通知
    notify_board_over = board_over_limit and not state.board_over_notified
    state.board_over_notified = bool(board_over_limit)

    # 读日志增量
    log_increment = read_log_increment(log_path, state)

    # 人工指导检测（human_guidance.md 非空 -> 触发 Hermes 处理）
    hg_path = work_dir / "human_guidance.md"
    has_human_guidance = hg_path.exists() and bool(hg_path.read_text(encoding="utf-8").strip())

    # branch_result 变化检测 (subagent 完成 -> 可能带 flag，需 Hermes 审核)
    branch_changed = check_branch_results(work_dir, state)

    # 快速检测 flag
    flag = check_flag_found(progress, log_increment)

    # 超时检测
    elapsed = time.time() - state.start_time
    is_timeout = elapsed > TIMEOUT_SECONDS

    # 日志停滞检测
    is_stale = False
    stale_seconds = 0
    if log_path.exists() and not log_increment:
        mtime = log_path.stat().st_mtime
        stale_seconds = int(time.time() - mtime)
        if stale_seconds > STALE_LOG_SECONDS:
            is_stale = True

    # 持久化状态
    save_state(work_dir, state)

    # 决定是否输出
    has_new_log = bool(log_increment.strip())
    has_flag = flag is not None

    if (
        not has_new_log
        and not has_flag
        and not is_stale
        and not is_timeout
        and not has_human_guidance
        and not notify_board_over
        and not branch_changed
    ):
        # 一切正常，无新日志 -> 静默
        return None

    # 日志增量只作为闹钟信号，不传内容（Hermes 自行 tail 读新鲜数据）
    log_line_count = len(log_increment.strip().split("\n")) if log_increment.strip() else 0

    output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "work_dir": str(work_dir),
        "elapsed_minutes": int(elapsed / 60),
        "progress": {
            "phase": progress.get("phase", ""),
            "next_steps": progress.get("next_steps", ""),
            "flags": progress.get("flags", ""),
            "url": progress.get("url", ""),
        },
        "log_increment_lines": log_line_count,
        "log_increment_hint": "(有新日志，请自行 tail codex.log 读取)" if log_line_count > 0 else "(无新日志)",
        "human_guidance": "有新的待处理人工指导，请读 human_guidance.md" if has_human_guidance else None,
        "flag_found": flag,
        "branch_results_changed": branch_changed,
        "is_stale": is_stale,
        "stale_seconds": stale_seconds,
        "is_timeout": is_timeout,
        "board": {
            "memory_count": board["memory_count"],
            "idea_count": board["idea_count"],
            "over_limit": board_over_limit,
        },
    }

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes CTF monitor (eyes)")
    parser.add_argument("--work-dir", required=True, help="challenge work directory")
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    if not work_dir.exists():
        print(f"work-dir not found: {work_dir}", file=sys.stderr)
        return 1

    output = run_monitor(work_dir)
    if output:
        print(json.dumps(output, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
