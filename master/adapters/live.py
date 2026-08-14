#!/usr/bin/env python3
"""
adapters/live.py -- 真实赛方平台适配器 (Phase 4，测试日按官方 API 文档填充)。

预计接口形态 (参考第二届腾讯智能渗透测试黑客松):
  GET  /challenges       题目列表
  POST /start_challenge  开靶机
  POST /stop_challenge   释放靶机
  POST /submit           提交 flag
  GET  /hint             获取提示

填充时只需实现 base.PlatformAdapter 的方法，字段映射到 Challenge/SubmitResult。
Master 核心逻辑零改动，配置 adapter=live 即接入。
"""

from __future__ import annotations

from pathlib import Path

from .base import Challenge, PlatformAdapter, SubmitResult


class LiveAdapter(PlatformAdapter):
    """真实平台实现骨架。"""

    def __init__(self) -> None:
        raise NotImplementedError(
            "live adapter 待测试日按赛方 API 文档填充 (master-agent-spec.md Phase 4)"
        )

    def list_challenges(self) -> list[Challenge]:
        raise NotImplementedError

    def start_challenge(self, cid: str) -> str:
        raise NotImplementedError

    def stop_challenge(self, cid: str) -> None:
        raise NotImplementedError

    def submit(self, cid: str, flag: str) -> SubmitResult:
        raise NotImplementedError
