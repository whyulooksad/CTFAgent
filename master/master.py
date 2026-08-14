#!/usr/bin/env python3
"""
master.py -- CTF 多题并行调度主进程 (master-agent-spec.md)。

职责:
  1. 通过 Adapter 拉题 / 开靶机 / 下载附件
  2. 按优先级分发题目到空闲 Solver 槽位 (进程/Docker/Fake 后端)
  3. 监控运行中的 Solver: 读 work_dir/progress.md 检测 flag / 超时 / 死亡
  4. flag 经 Submitter 自动提交 (频控)，correct 后回收槽位
  5. 失败题按高价值规则重试一次；达到 max_challenges 或队列耗尽后收尾

Master 不解题、不监督解题 (那是 Solver 容器内 Codex/Hermes 的事)。

Usage:
  python3 master.py [--config master_config.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

import prioritizer
from adapters.base import Challenge
from adapters.mock import MockAdapter
from challenge_state import (
    ACTIVE_STATES,
    FAILED,
    FLAG_FOUND,
    MANUAL_STOP,
    MasterState,
    QUEUED,
    RUNNING,
    SUBMITTED_CORRECT,
    TIMEOUT,
    extract_flags,
    retry_eligible,
)
from solver_pool import DockerBackend, FakeBackend, ProcessBackend, SolverBackend, SolverHandle
from submitter import Submitter

SCRIPT_DIR = Path(__file__).resolve().parent          # master/
REPO_DIR = SCRIPT_DIR.parent                          # 仓库根
CHALLENGES_DIR = REPO_DIR / "challenges"

# dispatch 基础设施失败 (开靶机/下载附件) 的冷却时间
DISPATCH_COOLDOWN = 30.0
# 失败重试重新入队前的冷却
RETRY_COOLDOWN = 5.0


# ───────────────────────── Config ─────────────────────────


@dataclass
class Config:
    adapter: str = "mock"                    # mock | live
    backend: str = "process"                 # process | docker | fake(测试)
    max_solvers: int = 5                     # 并发 Solver 槽位数 (手动可配)
    max_challenges: int = 20                 # 尝试题目数上限 (去重计)
    solver_timeout: int = 3600               # 单个 solver 整体超时 (秒，默认 1h)
    max_retries_per_challenge: int = 1       # 失败重试次数上限
    retry_value_threshold: float = 0.6       # 高价值判定: 分数归一化阈值
    retry_rarity_threshold: float = 0.7      # 高价值判定: 解出稀有度阈值
    poll_interval: int = 15                  # 主循环间隔 (秒)
    llm_priority: bool = True                # LLM 软修正开关 (Phase 3 生效)
    llm_priority_effort: str = "low"
    submit_min_interval: int = 10            # 提交频控: 最小间隔 (秒)
    max_submit_per_challenge: int = 3        # 单题提交上限
    dashboard_port: int = 8081               # Phase 3
    docker_image: str = "ctf-solver:latest"  # Phase 2
    state_file: str = "master_state.json"
    log_file: str = "master.log"

    @classmethod
    def load(cls, path: Path) -> "Config":
        data: dict = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def make_adapter(name: str):
    if name == "mock":
        return MockAdapter()
    if name == "live":
        from adapters.live import LiveAdapter  # 测试日填充 (Phase 4)
        return LiveAdapter()
    raise ValueError(f"unknown adapter: {name}")


def make_backend(name: str, cfg: Optional[Config] = None) -> SolverBackend:
    if name == "process":
        return ProcessBackend()
    if name == "docker":
        from cred_snapshot import ensure_snapshot
        snap = ensure_snapshot()  # 精制快照 (spec §7)，每次 Master 启动生成一份
        return DockerBackend(image=cfg.docker_image if cfg else "ctf-solver:latest",
                             snapshot_dir=snap)
    if name == "fake":
        # 零成本手动调试用: 不起 codex，秒级"解出"题目 (mock 平台下直接判对)
        from adapters.mock import MOCK_FLAGS
        lookup = MOCK_FLAGS.get if (cfg and cfg.adapter == "mock") else None
        return FakeBackend(flag_lookup=lookup)
    raise ValueError(f"unknown backend: {name}")


# ───────────────────────── Master ─────────────────────────


class Master:
    """调度主循环。单线程主循环 + Submitter 后台线程。"""

    def __init__(self, cfg: Config, adapter=None, backend: Optional[SolverBackend] = None):
        self.cfg = cfg
        state_path = Path(cfg.state_file)
        if not state_path.is_absolute():
            state_path = REPO_DIR / state_path
        self.state = MasterState(state_path, max_submit_per_challenge=cfg.max_submit_per_challenge)
        self.adapter = adapter if adapter is not None else make_adapter(cfg.adapter)
        self.backend = backend if backend is not None else make_backend(cfg.backend, cfg)
        self.submitter = Submitter(
            self.adapter, self.state, min_interval=cfg.submit_min_interval
        )
        self.running: dict[str, SolverHandle] = {}
        self.dashboard_server = None
        self._started_targets: set[str] = set()   # 已开靶机的 web 题
        self._stop = threading.Event()
        self._interrupted = False
        self.paused = False
        self.log = logging.getLogger("master")

    # ─── 生命周期 ───

    def run(self) -> int:
        try:
            signal.signal(signal.SIGINT, self._on_signal)
            signal.signal(signal.SIGTERM, self._on_signal)
        except ValueError:
            pass  # 非主线程 (测试嵌入)，无信号处理

        restored = self.state.load()
        if restored:
            self.log.info("恢复状态: %d 条题目记录", len(self.state.all_records()))
            self._recover()
        self.submitter.start()

        # Phase 3: 总览面板 (失败不影响调度)
        self.dashboard_server = None
        if getattr(self.cfg, "dashboard_port", 0):
            try:
                from master_dashboard import start_dashboard
                self.dashboard_server, port = start_dashboard(self, self.cfg.dashboard_port)
                self.log.info("总览面板: http://localhost:%d", port)
            except Exception as e:
                self.log.error("面板启动失败 (不影响调度): %s", e)

        try:
            while not self._stop.is_set():
                if not self.paused:
                    self._sync_challenges()
                    self._fill_slots()
                self._drain_results()
                self._monitor_solvers()
                self.state.save()
                self._log_status()
                if self._should_exit():
                    self.log.info("调度完成，退出")
                    break
                self._stop.wait(self.cfg.poll_interval)
        finally:
            self._shutdown()
        self._log_summary()
        return 0

    def _on_signal(self, signum, frame) -> None:
        self.log.warning("收到信号 %s，停止调度...", signum)
        self._interrupted = True
        self._stop.set()

    # ─── 恢复 (Master 崩溃重启) ───

    def _recover(self) -> None:
        """重启后，上次运行中的 solver 进程已不存在，按失败处理 (走重试规则)。"""
        for rec in self.state.all_records():
            if rec.status in ACTIVE_STATES:
                self.log.warning("恢复: %s 上次处于 %s，标记失败", rec.id, rec.status)
                self._finalize(rec.id, FAILED, "Master 重启，solver 进程丢失")

    # ─── 主循环各阶段 ───

    def _sync_challenges(self) -> None:
        """拉取题目列表，同步元数据 (动态分数/解出人数)，新题入队。"""
        try:
            metas = self.adapter.list_challenges()
        except Exception as e:
            self.log.error("list_challenges 失败: %s", e)
            return
        for meta in metas:
            self.state.sync_challenge(meta)

    def _fill_slots(self) -> None:
        """
        把队列中的题分发到空闲槽位。

        max_challenges 上限只约束"新题"：已尝试过的题重试不受上限限制
        (给高分难题的第二次机会不挤占新题名额，也不重复计数)。
        """
        while len(self.running) < self.cfg.max_solvers:
            allow_new = self.state.distinct_attempted() < self.cfg.max_challenges
            rec = self._next_candidate(allow_new=allow_new)
            if rec is None:
                break
            self._dispatch(rec)

    def _next_candidate(self, allow_new: bool = True):
        now = time.time()
        queued = [
            r
            for r in self.state.all_records()
            if r.status == QUEUED
            and (r.next_eligible_at or 0) <= now
            and (allow_new or r.attempts >= 1)
        ]
        if not queued:
            return None
        ordered = prioritizer.rule_order(queued)
        if self.cfg.llm_priority:
            ordered = prioritizer.llm_order(ordered)
        return ordered[0]

    def _dispatch(self, rec) -> Optional[SolverHandle]:
        """分发一道题。基础设施失败 → 冷却重排；成功 → RUNNING。"""
        self.state.set_status(rec.id, "dispatched")

        # 1. web 题开靶机
        if rec.type == "web":
            try:
                url = self.adapter.start_challenge(rec.id)
                if not url:
                    raise RuntimeError("start_challenge 未返回靶机 URL")
                rec.url = url
                self._started_targets.add(rec.id)
            except Exception as e:
                self._dispatch_cooldown(rec, f"开靶机失败: {e}")
                return None

        # 2. 下载附件 (已有本地文件则跳过，重试复用)
        if rec.attachment_url and not rec.attachment_path:
            try:
                dest = CHALLENGES_DIR / "attachments" / rec.id
                rec.attachment_path = str(self.adapter.download_attachment(rec.attachment_url, dest))
            except Exception as e:
                self._dispatch_cooldown(rec, f"下载附件失败: {e}")
                return None

        # 3. 启动 solver
        try:
            handle = self.backend.start(self._to_challenge(rec))
        except Exception as e:
            self._dispatch_cooldown(rec, f"启动 solver 失败: {e}")
            return None

        # 清掉上次运行残留的 progress.md (比本次启动旧的)，避免陈旧的
        # Flags Found 在 run.sh 重写前被误检测 (重试/重启场景)
        stale = Path(handle.work_dir) / "progress.md"
        try:
            if stale.exists() and stale.stat().st_mtime < handle.started_at:
                stale.unlink()
        except OSError:
            pass

        rec.attempts += 1
        rec.started_at = time.time()
        rec.finished_at = None
        rec.error = ""
        rec.work_dir = str(handle.work_dir)
        self.state.set_status(rec.id, RUNNING)
        self.running[rec.id] = handle
        self.log.info(
            "分发 %s (第 %d 次尝试) -> %s [%s]",
            rec.id, rec.attempts, handle.work_dir.name, rec.type,
        )
        return handle

    def _dispatch_cooldown(self, rec, error: str) -> None:
        rec.next_eligible_at = time.time() + DISPATCH_COOLDOWN
        self.state.set_status(rec.id, QUEUED, error)
        self.log.warning("分发 %s 失败: %s，冷却 %.0fs 后重试", rec.id, error, DISPATCH_COOLDOWN)

    def _monitor_solvers(self) -> None:
        """检查每个运行中的 solver: flag / 超时 / 死亡。"""
        for cid, handle in list(self.running.items()):
            rec = self.state.get(cid)
            if rec is None:
                self.running.pop(cid, None)
                continue

            # 1. flag 检测 (读 progress.md 的 Flags Found 段)
            for flag in self._read_flags(handle):
                if self.state.mark_flag_seen(cid, flag):
                    self.log.info("检测到 flag: %s -> %s", cid, flag)
                    if self.state.can_submit(cid):
                        self.submitter.submit(cid, flag)
                    else:
                        self.log.warning("%s 达到提交上限，不再提交: %s", cid, flag)
                    if rec.status == RUNNING:
                        self.state.set_status(cid, FLAG_FOUND)

            # 2. 死亡检测
            if not self.backend.is_alive(handle):
                if self.state.pending_submits(cid) > 0:
                    continue  # 等提交结果，下轮再判定
                if rec.status == SUBMITTED_CORRECT:
                    self._release(cid)
                    continue
                self._finalize(cid, FAILED, "solver 已结束且无待定 flag")
                continue

            # 3. 超时检测
            if rec.started_at and time.time() - rec.started_at > self.cfg.solver_timeout:
                self.log.warning("%s 超时 (%ds)，终止", cid, self.cfg.solver_timeout)
                self.backend.stop(handle)
                self._finalize(cid, TIMEOUT, f"solver 超时 ({self.cfg.solver_timeout}s)")

    def _drain_results(self) -> None:
        """处理提交结果。"""
        for res in self.submitter.results():
            cid, flag, status = res["cid"], res["flag"], res["status"]
            rec = self.state.get(cid)
            if rec is None:
                continue
            self.state.record_submit_result(cid, flag, status, res.get("message", ""))

            if status == "correct":
                self.state.mark_correct(cid, flag)
                self.log.info("=== FLAG ACCEPTED: %s %s ===", cid, flag)
                handle = self.running.pop(cid, None)
                if handle:
                    self.backend.stop(handle)  # run.sh 找到 flag 后通常已自行退出
                self._release_target(rec)
            elif status == "wrong":
                self.log.warning("%s flag 提交错误: %s (solver 继续跑)", cid, flag)
            else:  # error / skipped
                self.log.error("%s 提交未成功 (%s): %s", cid, status, res.get("message", ""))

    # ─── 终止/重试/释放 ───

    def _finalize(self, cid: str, status: str, error: str) -> None:
        """solver 失败/超时收尾: 释放槽位 + 靶机，按规则决定是否重试。"""
        self.running.pop(cid, None)
        rec = self.state.get(cid)
        if rec is None:
            return
        rec.finished_at = time.time()
        self._release_target(rec)

        # 手动终止不重试
        if status == MANUAL_STOP:
            self.state.set_status(cid, MANUAL_STOP, error)
            self.log.info("%s 终态: manual_stop", cid)
            return

        max_attempts = 1 + self.cfg.max_retries_per_challenge
        if (
            rec.attempts < max_attempts
            and retry_eligible(
                rec,
                self.state.all_records(),
                self.cfg.retry_value_threshold,
                self.cfg.retry_rarity_threshold,
            )
        ):
            rec.next_eligible_at = time.time() + RETRY_COOLDOWN
            self.state.set_status(cid, QUEUED)
            self.log.info(
                "%s 高价值题 (%d 分/%d 解)，%.0fs 后重试 (第 %d/%d 次)",
                cid, rec.score, rec.solve_count, RETRY_COOLDOWN, rec.attempts + 1, max_attempts,
            )
        else:
            self.state.set_status(cid, status, error)
            self.log.info("%s 终态: %s (%s)", cid, status, error or "-")

    def _release(self, cid: str) -> None:
        """槽位释放 (correct 后由 drain 处理过状态，这里只清 handle)。"""
        handle = self.running.pop(cid, None)
        if handle:
            self.backend.stop(handle)

    def _release_target(self, rec) -> None:
        """释放 web 题靶机 (赛方有数量/时长限制，即用即释放)。"""
        if rec.id in self._started_targets:
            self._started_targets.discard(rec.id)
            try:
                self.adapter.stop_challenge(rec.id)
                self.log.info("靶机已释放: %s", rec.id)
            except Exception as e:
                self.log.warning("释放靶机 %s 失败 (仅告警): %s", rec.id, e)

    # ─── 停止判定 / 收尾 ───

    def _should_exit(self) -> bool:
        if self.paused:
            return False
        records = self.state.all_records()
        if self.running:
            return False
        if any(r.status in ACTIVE_STATES for r in records):
            return False
        if any(self.state.pending_submits(r.id) > 0 for r in records):
            return False
        if self.submitter.has_pending():
            return False
        queued = [r for r in records if r.status == QUEUED]
        attempted = self.state.distinct_attempted()
        # 上限只挡新题；已尝试题的重试仍可分发 (与 _fill_slots 语义一致)
        if queued:
            has_retry = any(r.attempts >= 1 for r in queued)
            allow_new = attempted < self.cfg.max_challenges
            if has_retry or allow_new:
                return False
        if attempted >= self.cfg.max_challenges:
            self.log.info(
                "已达题目数上限 (%d/%d)，不再分发", attempted, self.cfg.max_challenges
            )
            return True
        if not queued:
            self.log.info("队列已空 (尝试 %d 题)", attempted)
            return True
        return False

    def _shutdown(self) -> None:
        self.log.info("收尾: 停止 %d 个运行中的 solver...", len(self.running))
        for cid, handle in list(self.running.items()):
            try:
                self.backend.stop(handle)
            except Exception as e:
                self.log.error("停止 %s 失败: %s", cid, e)
            if self._interrupted:
                rec = self.state.get(cid)
                if rec is not None:
                    rec.finished_at = time.time()
                    self.state.set_status(cid, MANUAL_STOP)
        for cid in list(self._started_targets):
            try:
                self.adapter.stop_challenge(cid)
            except Exception:
                pass
            self._started_targets.discard(cid)
        self.submitter.stop()
        if self.dashboard_server is not None:
            try:
                self.dashboard_server.shutdown()
            except Exception:
                pass
        self.state.save()

    def _log_summary(self) -> None:
        records = sorted(self.state.all_records(), key=lambda r: r.status)
        self.log.info("===== 总结 =====")
        for r in records:
            flag = r.flag or "-"
            self.log.info(
                "%-28s %-18s attempts=%d flag=%s",
                r.id, r.status, r.attempts, flag,
            )

    def _log_status(self) -> None:
        records = self.state.all_records()
        n_correct = sum(1 for r in records if r.status == SUBMITTED_CORRECT)
        n_queued = sum(1 for r in records if r.status == QUEUED)
        self.log.info(
            "[状态] 槽位 %d/%d | 尝试 %d/%d | 已解 %d | 排队 %d",
            len(self.running), self.cfg.max_solvers,
            self.state.distinct_attempted(), self.cfg.max_challenges,
            n_correct, n_queued,
        )

    # ─── 工具 ───

    def _to_challenge(self, rec) -> Challenge:
        return Challenge(
            id=rec.id,
            title=rec.title,
            type=rec.type,
            score=rec.score,
            solve_count=rec.solve_count,
            description=rec.description,
            url=rec.url,
            attachment_url=rec.attachment_url,
            attachment_path=Path(rec.attachment_path) if rec.attachment_path else None,
        )

    def _read_flags(self, handle: SolverHandle) -> list[str]:
        path = Path(handle.work_dir) / "progress.md"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return []
        return extract_flags(text)

    # ─── 手动控制 (Phase 3 面板 / 测试用) ───

    def pause(self) -> None:
        self.paused = True
        self.log.info("已暂停调度 (运行中的 solver 不受影响)")

    def resume(self) -> None:
        self.paused = False
        self.log.info("恢复调度")

    def update_config(self, max_solvers=None, max_challenges=None) -> None:
        """运行时调整并发数/题目上限 (面板用)。"""
        if max_solvers is not None:
            max_solvers = int(max_solvers)
            if max_solvers < 1:
                raise ValueError("max_solvers 必须 >= 1")
            self.cfg.max_solvers = max_solvers
        if max_challenges is not None:
            max_challenges = int(max_challenges)
            if max_challenges < 1:
                raise ValueError("max_challenges 必须 >= 1")
            self.cfg.max_challenges = max_challenges
        self.log.info(
            "配置已更新: max_solvers=%d max_challenges=%d",
            self.cfg.max_solvers, self.cfg.max_challenges,
        )

    def stop_solver(self, cid: str) -> bool:
        handle = self.running.get(cid)
        if handle is None:
            return False
        self.backend.stop(handle)
        self._finalize(cid, MANUAL_STOP, "手动终止")
        return True


# ───────────────────────── CLI ─────────────────────────


def setup_logging(log_file: Path) -> None:
    fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def main() -> int:
    parser = argparse.ArgumentParser(description="CTF Master 多题调度器")
    parser.add_argument("--config", default=str(SCRIPT_DIR / "master_config.json"))
    args = parser.parse_args()

    cfg = Config.load(Path(args.config))
    log_file = Path(cfg.log_file)
    if not log_file.is_absolute():
        log_file = REPO_DIR / log_file
    setup_logging(log_file)

    logging.getLogger("master").info(
        "Master 启动: adapter=%s backend=%s max_solvers=%d max_challenges=%d",
        cfg.adapter, cfg.backend, cfg.max_solvers, cfg.max_challenges,
    )
    return Master(cfg).run()


if __name__ == "__main__":
    sys.exit(main())
