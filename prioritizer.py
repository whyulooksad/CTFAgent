#!/usr/bin/env python3
"""
prioritizer.py -- 题目优先级排序 (master-agent-spec.md §4.3)。

规则层 (确定性，基础序):
    ease  = solve_count / max(solve_count)    解出人数归一化 (越多越容易)
    value = score / max(score)                分数归一化
    base  = 0.5 * ease + 0.5 * value          第一优先级: 分高 + 容易
    排序键 = (-base, -solve_count, -score)     次级: 容易优先，再次: 分高

LLM 层 (软修正): codex exec 单次低推理档调用，综合题目描述修正顺序。
    - 只调整顺序，不能增删题目
    - 输出必须是合法的 id 排列 (集合与输入完全一致)，否则回退输入 (规则层结果)
    - 按候选集签名缓存，同一批候选不会重复调用
    - 调用失败/超时(30s)/非法输出一律静默回退，不阻塞调度
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent

# 排序输入 duck-typing: 只要有 .score / .solve_count 属性
# (Challenge 和 ChallengeRecord 都满足)

CODEX_TIMEOUT = 30          # LLM 调用超时，超时即回退
DESC_SNIPPET_LEN = 60       # 提交给 LLM 的描述截断长度

# 候选集签名 -> 推荐顺序 (id 列表)。签名 = 排序后的 (id, score, solve_count)
_llm_cache: dict[str, list[str]] = {}


def rule_order(records: Sequence) -> list:
    """规则层排序。输入任意含 score/solve_count 的对象序列。"""
    items = [r for r in records]
    if not items:
        return []
    max_score = max((r.score for r in items), default=0) or 1
    max_solves = max((r.solve_count for r in items), default=0) or 1

    def key(r):
        ease = r.solve_count / max_solves
        value = r.score / max_score
        base = 0.5 * ease + 0.5 * value
        return (-base, -r.solve_count, -r.score)

    return sorted(items, key=key)


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
        ids = _call_codex(ordered)
        if not ids:
            return ordered  # 调用失败/输出非法 -> 回退
        # 严格校验: 只允许重排，不允许增删
        expected = {r.id for r in ordered}
        if len(ids) != len(ordered) or set(ids) != expected:
            return ordered
        _llm_cache[sig] = ids

    by_id = {r.id: r for r in ordered}
    return [by_id[i] for i in ids]


def _call_codex(records) -> Optional[list[str]]:
    """单次 codex exec 调用，返回推荐顺序的 id 列表；失败返回 None。"""
    codex_cmd = os.environ.get("CODEX_CMD", "codex")
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
            [codex_cmd, "exec", "-c", "model_reasoning_effort=low", prompt],
            capture_output=True,
            text=True,
            timeout=CODEX_TIMEOUT,
            cwd=str(SCRIPT_DIR),
            stdin=subprocess.DEVNULL,
        )
        out = res.stdout or ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    # 从输出中提取第一个 JSON 数组 (codex exec 输出带横幅等多余内容)
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
