#!/usr/bin/env python3
"""
prioritizer.py -- 题目优先级排序 (master-agent-spec.md §4.3)。

规则层 (确定性，基础序):
    ease  = solve_count / max(solve_count)    解出人数归一化 (越多越容易)
    value = score / max(score)                分数归一化
    base  = 0.5 * ease + 0.5 * value          第一优先级: 分高 + 容易
    排序键 = (-base, -solve_count, -score)     次级: 容易优先，再次: 分高

LLM 层 (软修正): Phase 3 用 codex exec 单次低推理档调用修正顺序，
解析失败/超时/非法输出一律回退规则层结果。
"""

from __future__ import annotations

from typing import Sequence

# 排序输入 duck-typing: 只要有 .score / .solve_count 属性
# (Challenge 和 ChallengeRecord 都满足)


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


def llm_order(ordered: list) -> list:
    """
    LLM 软修正 -- Phase 3 实现 (codex exec, model_reasoning_effort=low, 30s 超时)。

    约束:
      - 只调整顺序，不能增删题目
      - 输出必须是合法的 id 排列，否则回退输入 (规则层结果)
    当前直接返回输入。
    """
    # TODO(Phase 3): codex exec 单次调用，输入候选题 meta，输出推荐顺序
    return ordered
