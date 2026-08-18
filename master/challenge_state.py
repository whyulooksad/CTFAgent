#!/usr/bin/env python3
"""
challenge_state.py -- Master 的题目状态机与持久化。

状态机 (master-agent-spec.md §4.2):

    discovered → queued → dispatched → running ─┬→ flag_found → submitted_correct ✓
                                                ├→ submitted_wrong (solver 继续跑)
                                                ├→ timeout ──┐
                                                ├→ failed ───┴→ (可选重试一次) → queued
                                                └→ manual_stop ✓

- submitted_correct / manual_stop / 不再重试的 timeout / failed 为终态
- 重试规则: 最多重试 1 次，仅限高价值题 (分值高 / 多 flag 有部分进度)，见 retry_eligible()
- 状态由 Master 主循环 + Submitter 线程两个线程读写，MasterState 加锁保护
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# ─── 状态常量 ───

QUEUED = "queued"
DISPATCHED = "dispatched"
RUNNING = "running"
FLAG_FOUND = "flag_found"
SUBMITTED_CORRECT = "submitted_correct"
SUBMITTED_WRONG = "submitted_wrong"     # 信息性记录 (rec.last_submit_status)，不作为主状态
TIMEOUT = "timeout"
FAILED = "failed"
MANUAL_STOP = "manual_stop"

# 可能被 Master 重启恢复逻辑中断的状态
ACTIVE_STATES = {DISPATCHED, RUNNING, FLAG_FOUND}

# 终态 (不再参与调度)
TERMINAL_STATES = {SUBMITTED_CORRECT, MANUAL_STOP, TIMEOUT, FAILED}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ─── flag 解析 (与 run.sh 的 awk 逻辑一致) ───

_FLAG_HEADER = re.compile(r"^##\s*Flags Found\b")
# 与 monitor.py 的 FLAG_PATTERN 同源，外加前缀边界: 避免把 "SCTF{...}"
# 截成 "CTF{...}" 这类子串误匹配；非 flag{}/ctf{} 前缀走原始行回退
_FLAG_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:flag|ctf)\{[^}]+\}", re.IGNORECASE)

# 回退行的"像 flag"判据参数。实测 (2026-08-15 ezssti) 模型会把进度笔记写进
# Flags Found 段 (如 "- 2026-08-15: 已按要求完整读取...准备继续侦察")，
# 整句当 flag 提交造成假闭环 + solver 被误杀，回退必须严格过滤
_MAX_FLAG_LEN = 128
_CJK_CHAR = re.compile(r"[一-鿿　-〿＀-￯]")   # 汉字/中文标点/全角
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")                        # 日志式笔记前缀


def _looks_like_flag(s: str) -> bool:
    """
    回退行 (正则未命中 token 时) 是否像 flag。

    flag 是无空白的短 token；带空格的自然语言句子、日期前缀的进度笔记、
    含中文的行都判为笔记噪音。(中文 flag 的平台极少，且那种 flag{中文} 形式
    会被上面的 _FLAG_TOKEN 正则命中，不走回退)
    """
    if not s or len(s) > _MAX_FLAG_LEN:
        return False
    if any(ch.isspace() for ch in s):
        return False
    if _DATE_PREFIX.match(s):
        return False
    if _CJK_CHAR.search(s):
        return False
    return True


def extract_flags(progress_text: str) -> list[str]:
    """
    解析 progress.md 的 Flags Found 段，提取 flag token。

    等价于 run.sh:
      awk '/^## *Flags Found/{f=1;next} /^##/{f=0} f' progress.md
        | grep -v '^(无)' | grep -v '^<!--' | grep -v '^$'

    实测模型写该段时常带噪音 (列表符/反引号/来源注释)，如:
      - `flag{x}`
      - flag{x}（来源：首页 HTML 注释）
    整行当 flag 提交会被平台判错。因此行内匹配 flag token；
    匹配不到时回退为清理后的原始行 (保留"不猜 flag 格式"原则，
    兼容非 flag{}/ctf{} 前缀的平台)。
    """
    flags: list[str] = []
    in_section = False
    for line in progress_text.splitlines():
        if line.startswith("##"):
            in_section = bool(_FLAG_HEADER.match(line))
            continue
        if not in_section:
            continue
        s = line.strip()
        if not s or s == "(无)" or s.startswith("<!--"):
            continue
        matched = _FLAG_TOKEN.search(s)
        if matched:
            for m in _FLAG_TOKEN.finditer(s):
                if m.group(0) not in flags:
                    flags.append(m.group(0))
        else:
            cleaned = s.strip("-*• `\"'").strip()
            if cleaned and _looks_like_flag(cleaned) and cleaned not in flags:
                flags.append(cleaned)
    return flags


# ─── 重试判定 ───


def retry_eligible(
    rec: "ChallengeRecord",
    records: list["ChallengeRecord"],
    value_threshold: float = 0.6,
    rarity_threshold: float = 0.7,
) -> bool:
    """
    高价值题重试判定 v2 (2026-08-18 规则层重构):
      ① 分值高: score >= value_threshold × 本轮最高分
         (平台动态分值已编码"解出人数越多分越低、底 80%"，无需独立热度公式)
      ② 多 flag 有部分进度: flag_count>1 且 flags_correct>=1
         (已证明啃得动、分数已部分落袋，kill 后续攻性价比高)
    旧 rarity 公式废弃: solve_count 曾被 tsec 适配器造假污染 (easy=300/hard=100)，
    hard 题 rarity≈0.67 恰好踩中阈值变成"几乎必重试"。rarity_threshold 参数仅保留
    调用兼容，不再参与判定。
    """
    max_score = max((r.score for r in records), default=0)
    if max_score and rec.score >= value_threshold * max_score:
        return True
    if int(getattr(rec, "flag_count", 1) or 1) > 1 and \
            int(getattr(rec, "flags_correct", 0) or 0) >= 1:
        return True
    return False


# ─── 题目记录 ───


@dataclass
class ChallengeRecord:
    """单道题在 Master 侧的全生命周期记录 (可 JSON 序列化)。"""

    # 题目元数据 (sync 时更新)
    id: str
    title: str
    type: str                          # web | crypto | misc
    score: int = 0
    solve_count: int = 0
    difficulty: str = ""               # easy | medium | hard | "" 未知 (规则层排序与分层超时用)
    description: str = ""
    attachment_url: Optional[str] = None
    source: str = "platform"           # platform (adapter 拉取) | manual (面板手动加入)
    flag_count: int = 1                # 该题 flag 总数 (平台多 flag 题)
    flags_correct: int = 0             # 已正确提交的 flag 数
    boot_fails: int = 0                # 平台靶机就绪预检连续失败次数 (容器启动窗口)

    # 调度状态
    status: str = QUEUED
    attempts: int = 0                  # 已成功分发的次数 (基础设施失败不计)
    next_eligible_at: float = 0.0      # dispatch 失败后的冷却截止时间戳

    # 运行实例
    url: Optional[str] = None          # web 题靶机 URL (每次 start 可能变化)
    attachment_path: Optional[str] = None
    work_dir: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    # flag / 提交
    flags_seen: list[str] = field(default_factory=list)      # progress.md 里出现过的
    flags_submitted: list[str] = field(default_factory=list) # 已实际提交过的
    results_received: list[str] = field(default_factory=list)  # 已收到提交结果的
    submit_count: int = 0             # 实际提交次数 (受单题上限约束)
    last_submit_status: str = ""      # 最近一次提交结果 correct | wrong | error | skipped
    flag: Optional[str] = None        # 最终判定的正确 flag

    # 其他
    error: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChallengeRecord":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        rec = cls(**known)
        # 旧状态文件兼容
        rec.source = d.get("source", "platform")
        return rec


# ─── Master 全局状态 (线程安全 + 持久化) ───


class MasterState:
    """
    Master 的全部题目状态。

    写入方: Master 主循环 (调度/监控) + Submitter 线程 (提交结果)，加锁保护。
    持久化: master_state.json，原子写 (tmp + replace)，Master 崩溃后可恢复。
    """

    def __init__(self, path: Path, max_submit_per_challenge: int = 3):
        self.path = path
        self.max_submit_per_challenge = max_submit_per_challenge
        self._lock = threading.RLock()
        self.records: dict[str, ChallengeRecord] = {}

    # ─── 题目元数据同步 ───

    def sync_challenge(self, meta) -> ChallengeRecord:
        """
        同步平台题目元数据 (Challenge)。新建记录直接进 QUEUED；
        已有记录只更新元数据，绝不改 status (终态不复活)。
        """
        with self._lock:
            rec = self.records.get(meta.id)
            if rec is None:
                rec = ChallengeRecord(
                    id=meta.id,
                    title=meta.title,
                    type=meta.type,
                    score=meta.score,
                    solve_count=meta.solve_count,
                    difficulty=getattr(meta, "difficulty", "") or "",
                    description=meta.description,
                    attachment_url=meta.attachment_url,
                    source=getattr(meta, "source", "platform"),
                    flag_count=int(getattr(meta, "flag_count", 1) or 1),
                    status=QUEUED,
                )
                self.records[meta.id] = rec
            else:
                rec.title = meta.title
                rec.score = meta.score
                rec.solve_count = meta.solve_count
                if getattr(meta, "difficulty", ""):
                    rec.difficulty = meta.difficulty
                if meta.description:
                    # 保留 master 注入的多 flag 进度提示 (sync 每轮都会跑，不能覆盖)
                    tail = ""
                    if "[多 flag 进度]" in (rec.description or ""):
                        tail = "\n\n[多 flag 进度]" + \
                            rec.description.split("[多 flag 进度]", 1)[1]
                    rec.description = meta.description + tail
                if meta.attachment_url:
                    rec.attachment_url = meta.attachment_url
                if getattr(meta, "source", "platform") == "manual":
                    rec.source = "manual"
                if getattr(meta, "flag_count", 1) > 1:
                    rec.flag_count = meta.flag_count
            rec.updated_at = _now()
            return rec

    # ─── 查询 ───

    def get(self, cid: str) -> Optional[ChallengeRecord]:
        with self._lock:
            return self.records.get(cid)

    def all_records(self) -> list[ChallengeRecord]:
        with self._lock:
            return list(self.records.values())

    def distinct_attempted(self) -> int:
        """已尝试过的题目数 (按去重计，重试不重复计数)。"""
        with self._lock:
            return sum(1 for r in self.records.values() if r.attempts >= 1)

    def pending_submits(self, cid: str) -> int:
        """已看到但还没收到提交结果的 flag 数 (用于 solver 死亡后延迟判定)。"""
        with self._lock:
            rec = self.records.get(cid)
            if rec is None:
                return 0
            received = set(rec.results_received)
            return sum(1 for f in rec.flags_seen if f not in received)

    # ─── 状态迁移 ───

    def set_status(self, cid: str, status: str, error: str = "") -> None:
        with self._lock:
            rec = self.records.get(cid)
            if rec is None:
                return
            rec.status = status
            if error:
                rec.error = error
            rec.updated_at = _now()

    def mark_flag_seen(self, cid: str, flag: str) -> bool:
        """记录 flag 已出现。返回 True 表示首次出现。"""
        with self._lock:
            rec = self.records.get(cid)
            if rec is None:
                return False
            if flag in rec.flags_seen:
                return False
            rec.flags_seen.append(flag)
            rec.updated_at = _now()
            return True

    def can_submit(self, cid: str) -> bool:
        with self._lock:
            rec = self.records.get(cid)
            if rec is None:
                return False
            # flag 感知上限: 多 flag 题至少允许 全部 flag + 2 次试错余量
            # (单 flag 题即配置值 3，行为不变; 固定上限 3 会卡死 4-6 flag 题的最后
            #  一个 flag——解出来了也提交不了，通关判定永远到不了，solver 空转到
            #  超时; 实测 2026-08-18 b-01 3/4)
            fc = int(getattr(rec, "flag_count", 1) or 1)
            cap = max(self.max_submit_per_challenge, fc + 2)
            return rec.submit_count < cap

    def record_submit(self, cid: str, flag: str, status: str) -> None:
        """Submitter 线程在真正发起提交前调用 (占用一次提交配额)。"""
        with self._lock:
            rec = self.records.get(cid)
            if rec is None:
                return
            rec.submit_count += 1
            rec.flags_submitted.append(flag)
            rec.last_submit_status = status
            rec.updated_at = _now()

    def record_submit_result(self, cid: str, flag: str, status: str, message: str = "") -> None:
        """记录提交结果 (correct/wrong/error/skipped)。"""
        with self._lock:
            rec = self.records.get(cid)
            if rec is None:
                return
            if flag not in rec.results_received:
                rec.results_received.append(flag)
            rec.last_submit_status = status
            if status != "correct" and message:
                rec.error = message
            rec.updated_at = _now()

    def mark_correct(self, cid: str, flag: str, all_flags_done: bool = True) -> None:
        """记录一个正确 flag。all_flags_done=False (多 flag 未通关) 不终态。"""
        with self._lock:
            rec = self.records.get(cid)
            if rec is None:
                return
            rec.flag = flag
            rec.flags_correct += 1
            if all_flags_done:
                rec.status = SUBMITTED_CORRECT
                rec.finished_at = time.time()
            rec.updated_at = _now()

    # ─── 持久化 ───

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "records": {cid: r.to_dict() for cid, r in self.records.items()},
                "saved_at": _now(),
            }

    def save(self) -> None:
        data = self.snapshot()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # atomic

    def load(self) -> bool:
        if not self.path.exists():
            return False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            records = {
                cid: ChallengeRecord.from_dict(d)
                for cid, d in data.get("records", {}).items()
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
        with self._lock:
            self.records = records
        return bool(records)
