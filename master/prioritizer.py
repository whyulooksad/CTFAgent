#!/usr/bin/env python3
"""
prioritizer.py -- 题目优先级排序 (规则层 v2 + LLM 软修正)。

规则层 v2 (2026-08-18 真机复盘后重构，效用分模型):
    utility = 期望得分 / 期望耗时 × 方差惩罚 × 系列修正     (每槽位小时的期望得分)

    期望得分 = P(解出|难度) × score × (1 + 0.5×(flag_count-1))
        - P/T 先验: easy 0.85/15min, medium 0.45/30min, hard 0.20/50min
        - score 用平台动态分值 (解出人数越多分越低、底 80%，天然编码热度)
        - 多 flag 按"每 flag 一份分"乐观计 (平台部分计分)，剩余 flag 条件成功率折半
    期望耗时 = T(难度) × (1 + 0.4×(flag_count-1))
    方差惩罚 = 1 / (1 + 0.15×(flag_count-1))
        - 长任务占槽的风险折扣: 多 flag 夹在同难度单 flag 与下一档之间，
          不会像旧 ÷4 规则那样沉底，也绝不会跳到 easy 前面
    系列修正 = 0.5 + 拉普拉斯系列成功率   (系列尝试≥2 才启用, 修正系数∈[0.5,1.5])
        - 同前缀题号 (a-/b-/e1-...) 的历史成败自动教会调度器:
          连续失败的系列整体降权，高产系列保持

LLM 层 (软修正, 默认关): claude -p 单次无工具调用，综合题目描述修正顺序。
    - 2026-08-18 真机实测发现问题后默认关闭: deepseek 对 63 题的重排把多 flag
      排到队首 + 每次分发多 30-60s 延迟；配置 llm_priority: true 可重新启用
    - 只调整顺序，不能增删题目；非法输出/失败/超时(30s)一律静默回退规则层
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent          # master/ (claude -p 的工作目录)

# 排序输入 duck-typing: 只要有 .score / .difficulty / .flag_count 属性
# (Challenge 和 ChallengeRecord 都满足)

LLM_TIMEOUT = 30            # LLM 调用超时，超时即回退
DESC_SNIPPET_LEN = 60       # 提交给 LLM 的描述截断长度

# ── 规则层 v2 参数 (效用分模型) ──
SOLVE_PRIOR = {"easy": 0.85, "medium": 0.45, "hard": 0.20}    # P(解出) 先验
TIME_MIN = {"easy": 15.0, "medium": 30.0, "hard": 50.0}       # E(耗时) 分钟先验
DEFAULT_DIFFICULTY = "medium"                                 # 未知难度按 medium
FLAG_POINTS_STEP = 0.5     # 多 flag: 每多 1 个 flag 的期望得分加成 (条件成功率折半)
FLAG_TIME_FACTOR = 0.4     # 多 flag: 每多 1 个 flag 的耗时放大
FLAG_RISK_PENALTY = 0.15   # 多 flag: 占槽方差惩罚系数
SERIES_MIN_ATTEMPTS = 2    # 系列历史修正启用门槛 (尝试次数)

# 候选集签名 -> 推荐顺序 (id 列表)。签名 = 排序后的 (id, score, solve_count)
_llm_cache: dict[str, list[str]] = {}


def _series_stats(records) -> dict:
    """题号前缀 (a-/b-/e1-...) -> (尝试次数, 有产出数)。产出=通关或拿到>=1个 flag。"""
    stats: dict[str, tuple[int, int]] = {}
    for r in records:
        pfx = str(r.id).split("-")[0]
        a, s = stats.get(pfx, (0, 0))
        a += int(getattr(r, "attempts", 0) or 0)
        if str(getattr(r, "status", "")) == "submitted_correct" or \
                int(getattr(r, "flags_correct", 0) or 0) >= 1:
            s += 1
        stats[pfx] = (a, s)
    return {k: v for k, v in stats.items() if v[0] > 0}


def rule_order(records: Sequence, all_records: Optional[Sequence] = None) -> list:
    """
    规则层 v2 排序 (效用分模型，见模块 docstring)。

    all_records: 全量记录 (含终态/running)，用于系列历史修正——
    只传 queued 候选时无法知道"a 系已经连败 3 题"这类信息。
    """
    items = [r for r in records]
    if not items:
        return []
    stats = _series_stats(all_records if all_records is not None else items)

    def utility(r) -> float:
        diff = (getattr(r, "difficulty", "") or "").strip().lower()
        if diff not in SOLVE_PRIOR:
            diff = DEFAULT_DIFFICULTY
        p = SOLVE_PRIOR[diff]
        t = TIME_MIN[diff]
        fc = max(1, int(getattr(r, "flag_count", 1) or 1))

        # 系列历史修正: 修正系数 ∈ [0.5, 1.5]，乘在解出概率上
        pfx = str(r.id).split("-")[0]
        a, s = stats.get(pfx, (0, 0))
        if a >= SERIES_MIN_ATTEMPTS:
            srate = (s + 1) / (a + 2)          # 拉普拉斯平滑系列成功率
            p *= (0.5 + srate)

        expected_points = p * max(0, r.score) * (1 + FLAG_POINTS_STEP * (fc - 1))
        expected_hours = t * (1 + FLAG_TIME_FACTOR * (fc - 1)) / 60.0
        risk = 1.0 / (1.0 + FLAG_RISK_PENALTY * (fc - 1))
        return expected_points / expected_hours * risk

    return sorted(items, key=lambda r: (-utility(r), -r.score, -r.solve_count))


# ───────────────────────── LLM 软修正 ─────────────────────────


def _signature(records) -> str:
    parts = sorted(f"{r.id}:{r.score}:{r.solve_count}" for r in records)
    return "|".join(parts)


def llm_order(ordered: list) -> list:
    """
    LLM 软修正: 输入规则层排好的候选列表，返回 LLM 建议的顺序。
    任何异常情况都原样返回输入 (回退规则层)。
    """
    if len(ordered) < 2:
        return ordered

    sig = _signature(ordered)
    ids = _llm_cache.get(sig)
    if ids is None:
        ids = _call_llm(ordered)
        if not ids:
            return ordered  # 调用失败/输出非法 -> 回退
        # 严格校验: 只允许重排，不允许增删
        expected = {r.id for r in ordered}
        if len(ids) != len(ordered) or set(ids) != expected:
            return ordered
        _llm_cache[sig] = ids

    by_id = {r.id: r for r in ordered}
    return [by_id[i] for i in ids]


def _call_llm(records) -> Optional[list[str]]:
    """单次 claude -p 调用 (无工具、不落 session)，返回推荐顺序的 id 列表；失败返回 None。"""
    claude_cmd = os.environ.get("CLAUDE_CMD", "claude")
    lines = "\n".join(
        f"- id={r.id} | 类型={getattr(r, 'type', '?')} | 分数={r.score} | "
        f"已解人数={r.solve_count} | {getattr(r, 'title', '')} | "
        f"{(getattr(r, 'description', '') or '')[:DESC_SNIPPET_LEN]}"
        for r in records
    )
    prompt = (
        "你是 CTF 比赛的调度助手。以下是待做题目的候选列表:\n\n"
        f"{lines}\n\n"
        "比赛采用动态计分(解出人数越多，题目分值越低)。请综合预期解题难度和分值收益，"
        "给出最优先做的顺序(性价比最高、最容易拿分的排前面)。\n"
        "只输出一个 JSON 数组，元素是题目 id 字符串，按推荐顺序排列，"
        "必须包含且只包含上面列出的所有 id，不要输出其他任何内容。"
    )

    try:
        res = subprocess.run(
            [claude_cmd, "-p", prompt, "--tools", "", "--no-session-persistence"],
            capture_output=True,
            text=True,
            timeout=LLM_TIMEOUT,
            cwd=str(SCRIPT_DIR),
            stdin=subprocess.DEVNULL,
        )
        out = res.stdout or ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    # 从输出中提取第一个 JSON 数组 (模型偶尔加说明文字，防御性提取)
    m = re.search(r"\[[^\[\]]*\]", out, re.DOTALL)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(arr, list) or not all(isinstance(x, str) for x in arr):
        return None
    return arr
