#!/usr/bin/env python3
"""
adapters/manual.py -- 手动输入题目池 (面板「加题」)。

用于手动测试其他 CTF 平台的题目，与平台 adapter 并存，由 Master 统一调度。
手动题的语义与平台题不同:
  - web: 用户输入的 URL 就是靶机地址，无 start/stop 生命周期
  - crypto/misc: 附件是本地文件路径 (manual://<abs path>)，分发时拷贝进挑战目录
  - submit: 无判定平台，一律 correct —— solver 找到 flag 即闭环，
    flag 展示在面板上由用户自行拿去目标平台提交
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from .base import Challenge, PlatformAdapter, SubmitResult


class NoPlatformAdapter(PlatformAdapter):
    """未接入任何平台 (adapter=none): 只用手动题，等面板「平台接入」热切换。"""

    def list_challenges(self) -> list[Challenge]:
        return []

    def start_challenge(self, cid: str) -> str:
        return ""

    def stop_challenge(self, cid: str) -> None:
        pass

    def submit(self, cid: str, flag: str) -> SubmitResult:
        return SubmitResult("error", "平台未接入")


class ManualAdapter(PlatformAdapter):
    """手动题目池。线程安全 (面板线程写入，主循环读取)。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pool: dict[str, Challenge] = {}
        self._counter = 0

    def add(self, ch: Challenge) -> Challenge:
        with self._lock:
            self._counter += 1
            if not ch.id:
                ch.id = f"manual-{self._counter:03d}-{ch.type}"
            if not ch.title:
                ch.title = ch.id
            ch.source = "manual"
            self._pool[ch.id] = ch
        return ch

    # ─── PlatformAdapter ───

    def list_challenges(self) -> list[Challenge]:
        with self._lock:
            return list(self._pool.values())

    def start_challenge(self, cid: str) -> str:
        # 手动 web 题: 输入的 URL 即靶机，无需平台开实例
        with self._lock:
            ch = self._pool.get(cid)
        return ch.url if ch else ""

    def stop_challenge(self, cid: str) -> None:
        pass  # 手动题没有平台靶机生命周期

    def submit(self, cid: str, flag: str) -> SubmitResult:
        # 手动题无判定平台: 找到 flag 即闭环，正确性由用户在目标平台自行确认
        return SubmitResult("correct", "manual: flag 由用户自行到目标平台提交")

    def download_attachment(self, url: str, dest_dir: Path) -> Path:
        # manual://<abs path> -> 拷贝到挑战目录 (与平台附件统一处理)
        src = Path(url.replace("manual://", "", 1))
        if not src.is_file():
            raise FileNotFoundError(f"附件不存在: {src}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        return dest
