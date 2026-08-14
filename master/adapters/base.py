#!/usr/bin/env python3
"""
adapters/base.py -- 赛方平台适配器抽象接口。

Master 只依赖这里的接口与数据结构，不依赖任何具体平台实现。
换平台 = 新增一个 Adapter 实现 + 配置项 `adapter` 指向它 (master-agent-spec.md §4.1)。

真实赛方 API 测试日才公布，参考第二届腾讯智能渗透测试黑客松的模式:
  GET  /challenges      -> 题目列表 (含分数/解出人数/附件)
  POST /start_challenge -> 打开某道题的靶机实例
  POST /stop_challenge  -> 释放靶机
  POST /submit          -> 提交 flag
  GET  /hint            -> 获取提示
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Challenge:
    """与平台无关的题目数据结构。"""

    id: str                       # 平台题目 ID (唯一)
    title: str
    type: str                     # web | crypto | misc
    score: int = 0                # 当前动态分值 (解出越多分越低)
    solve_count: int = 0          # 已解出人数 (越多越容易)
    description: str = ""         # 题目描述 (作为 solver hint / LLM 判难度)
    url: Optional[str] = None     # web 题靶机 URL (start_challenge 之后才有)
    attachment_url: Optional[str] = None
    attachment_path: Optional[Path] = None   # Master 下载后的本地路径
    source: str = "platform"      # platform (adapter 拉取) | manual (面板手动加入)


@dataclass
class SubmitResult:
    """flag 提交结果。"""

    status: str                   # correct | wrong | error
    message: str = ""


class PlatformAdapter(abc.ABC):
    """赛方平台 API 抽象。所有方法应可重入、失败抛异常。"""

    @abc.abstractmethod
    def list_challenges(self) -> list[Challenge]:
        """拉取题目列表 (每次返回最新分数/解出人数，动态计分)。"""

    @abc.abstractmethod
    def start_challenge(self, cid: str) -> str:
        """打开靶机实例，返回实例 URL。非 web 题 / 无需开靶机时返回 ""。"""

    @abc.abstractmethod
    def stop_challenge(self, cid: str) -> None:
        """释放靶机实例。"""

    @abc.abstractmethod
    def submit(self, cid: str, flag: str) -> SubmitResult:
        """提交 flag 判定。"""

    def get_hint(self, cid: str) -> str:
        """获取该题提示 (预留，Master 暂不主动调用)。"""
        return ""

    def download_attachment(self, url: str, dest_dir: Path) -> Path:
        """下载附件到 dest_dir，返回本地路径。默认实现留给子类。"""
        raise NotImplementedError(f"attachment download not supported: {url}")
