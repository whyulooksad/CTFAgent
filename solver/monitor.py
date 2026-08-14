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
MAX_LOG_LINES = 80  # 单次输出最大日志行数 (防 JSON 过大)
STATE_FILE = "monitor_state.json"

FLAG_PATTERN = re.compile(r"(?:flag|ctf)\{[^}]+\}", re.IGNORECASE)

# ───────────────────────── Data Models ─────────────────────────


@dataclass
class MonitorState:
    """跨轮次持久化的状态。"""
    last_log_offset: int = 0  # 上次读到的 codex.log 位置
    start_time: float = 0.0

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
    result = {"raw": text}

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


def read_dead_ends(path: Path) -> str:
    """读取 dead_ends.md 全文。"""
    if not path.exists():
        return ""
    return path.read_text().strip()


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
    dead_ends = read_dead_ends(work_dir / "dead_ends.md")
    log_path = work_dir / "codex.log"

    # 读日志增量
    log_increment = read_log_increment(log_path, state)

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

    if not has_new_log and not has_flag and not is_stale and not is_timeout:
        # 一切正常，无新日志 -> 静默
        return None

    # 截断日志增量，防 JSON 过大
    log_lines = log_increment.strip().split("\n") if log_increment.strip() else []
    if len(log_lines) > MAX_LOG_LINES:
        log_lines = log_lines[-MAX_LOG_LINES:]
        log_increment = "...(已截断前部分)...\n" + "\n".join(log_lines)

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
        "dead_ends": dead_ends if dead_ends else "(空)",
        "log_increment": log_increment.strip() if log_increment.strip() else "(无新日志)",
        "flag_found": flag,
        "is_stale": is_stale,
        "stale_seconds": stale_seconds,
        "is_timeout": is_timeout,
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
