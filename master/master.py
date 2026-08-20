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
import os
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
from adapters.manual import ManualAdapter, NoPlatformAdapter
from challenge_state import (
    ACTIVE_STATES,
    FAILED,
    FLAG_FOUND,
    MANUAL_STOP,
    MasterState,
    QUEUED,
    RUNNING,
    SUBMITTED_CORRECT,
    TERMINAL_STATES,
    TIMEOUT,
    extract_flags,
)
from solver_pool import DockerBackend, FakeBackend, ProcessBackend, SolverBackend, SolverHandle
from submitter import Submitter

SCRIPT_DIR = Path(__file__).resolve().parent          # master/
REPO_DIR = SCRIPT_DIR.parent                          # 仓库根
CHALLENGES_DIR = REPO_DIR / "challenges"

# dispatch 基础设施失败 (开靶机/下载附件) 的冷却时间
DISPATCH_COOLDOWN = 30.0
# 平台 active 满 (start 409): 5 分钟后再试 (30s 重试无意义, 实例释放要分钟级)
PLATFORM_FULL_COOLDOWN = 300.0
# 平台容器启动等待上限 (次 × 30s; 12 次 ≈ 6 分钟。实测 tsec 容器就绪 2-3 分钟,
# 6 分钟余量足够; 再久就是真起不来, 判死释放名额 (2026-08-20))
MAX_BOOT_FAILS = 12
# 失败重试重新入队前的冷却
RETRY_COOLDOWN = 5.0


# ───────────────────────── Config ─────────────────────────


@dataclass
class Config:
    adapter: str = "mock"                    # mock | live
    backend: str = "process"                 # process | docker | fake(测试)
    max_solvers: int = 5                     # 并发 Solver 槽位数 (手动可配)
    max_challenges: int = 20                 # 尝试题目数上限 (去重计)
    platform_max_active: int = 3             # 平台同时活跃实例上限 (腾讯 409 报 3)
    solver_timeout: int = 3600               # 兜底超时: 单次 solver 整体上限 (秒)
    # 轮转调度 (时间片轮转 + 渐进式时间预算):
    #   第 1 圈每道题 round_time_base 秒, 超时换题 (保留 cc session);
    #   所有题做完一圈后 current_round+1, 时间上限 +round_time_step;
    #   最多 max_rounds 圈, 之后未解出的题终态。
    round_time_base: int = 1200              # 首轮每题时间上限 (秒, 默认 20 分钟)
    round_time_step: int = 600               # 每圈递增 (秒, 默认 +10 分钟)
    max_rounds: int = 5                      # 最多轮数 (默认 5 圈)
    poll_interval: int = 15                  # 主循环间隔 (秒)
    llm_priority: bool = True                # LLM 软修正开关 (Phase 3 生效)
    llm_priority_effort: str = "low"
    submit_min_interval: int = 10            # 提交频控: 最小间隔 (秒)
    max_submit_per_challenge: int = 3        # 单题提交上限
    dashboard_port: int = 8081               # Phase 3
    docker_image: str = "ctf-solver:latest"  # Phase 2
    agent_cli: str = "codex"                 # codex | claude | hermes (hermes=chat 协议, 托管网关兜底)
    state_file: str = "master_state.json"
    log_file: str = "master.log"
    flags_file: str = "flags.jsonl"           # 已解出 flag 的落盘文件 (面板数据源)
    resident: bool = False              # 常驻模式: 不退出，面板永远在线 (手动加题/平台接入)

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
    if name == "tsec":
        # 腾讯 Tsecbench。本地模式: TSEC_BASE_URL/TSEC_TOKEN;
        # 平台托管模式: 注入 BENCHMARK_BASE_URL/BENCHMARK_TOKEN (优先)
        base_url = (
            os.environ.get("BENCHMARK_BASE_URL")
            or os.environ.get("TSEC_BASE_URL", "https://tsecbench.zc.tencent.com")
        )
        token = os.environ.get("BENCHMARK_TOKEN") or os.environ.get("TSEC_TOKEN", "")
        if not token:
            raise ValueError("adapter=tsec 需要环境变量 TSEC_TOKEN 或 BENCHMARK_TOKEN")
        from adapters.tsec import TSecAdapter
        return TSecAdapter(base_url, token)
    if name in ("none", "live"):
        # none: 只用手动题 + 面板「平台接入」热切换 (手动测试其他平台的主模式)
        # live: 同 none 启动，接入信息由面板输入后切换到 LiveAdapter
        return NoPlatformAdapter()
    raise ValueError(f"unknown adapter: {name}")


def make_manual_adapter():
    """手动输入题目池 (面板「加题」)，独立于平台 adapter。"""
    return ManualAdapter()


def make_backend(name: str, cfg: Optional[Config] = None) -> SolverBackend:
    if name == "process":
        return ProcessBackend(agent_cli=getattr(cfg, "agent_cli", "codex"))
    if name == "docker":
        # 快照: 默认复用 cred_snapshots/current (deepseek 配置, 镜像/本地一致)。
        # 需要从宿主机重新生成时: CTF_SNAPSHOT_REFRESH=1 环境变量显式触发。
        if os.environ.get("CTF_SNAPSHOT_REFRESH") == "1":
            from cred_snapshot import ensure_snapshot
            snap = ensure_snapshot()
        else:
            snap = REPO_DIR / "cred_snapshots" / "current"
        return DockerBackend(image=cfg.docker_image if cfg else "ctf-solver:latest",
                             snapshot_dir=snap,
                             agent_cli=getattr(cfg, "agent_cli", "codex"))
    if name == "fake":
        # 零成本手动调试用: 不起 codex，秒级"解出"题目 (mock 平台下直接判对)
        from adapters.mock import MOCK_FLAGS
        lookup = MOCK_FLAGS.get if (cfg and cfg.adapter == "mock") else None
        return FakeBackend(flag_lookup=lookup)
    raise ValueError(f"unknown backend: {name}")


class _SourceRouterAdapter:
    """提交路由: 按题目 source 分发到平台/手动 adapter (Submitter 只用 submit)。"""

    def __init__(self, master: "Master"):
        self._master = master

    def submit(self, cid: str, flag: str):
        rec = self._master.state.get(cid)
        if rec is not None and rec.source == "manual":
            return self._master.manual_adapter.submit(cid, flag)
        return self._master.adapter.submit(cid, flag)


# ───────────────────────── Master ─────────────────────────


class Master:
    """调度主循环。单线程主循环 + Submitter 后台线程。"""

    def __init__(self, cfg: Config, adapter=None, backend: Optional[SolverBackend] = None):
        self.cfg = cfg
        # 确保 challenges/ 存在且属主为当前用户 (stw):
        # docker run -v 挂载不存在的源目录会由 docker daemon 自动创建 (root 属主),
        # 容器内 ubuntu(1000) 无写权限 → run.sh 建 work dir Permission denied → 秒崩
        # (2026-08-19 实测: 清理 challenges/ 后 master 重启, 全部题 failed 的根因)
        CHALLENGES_DIR.mkdir(parents=True, exist_ok=True)
        state_path = Path(cfg.state_file)
        if not state_path.is_absolute():
            state_path = REPO_DIR / state_path
        self.state = MasterState(state_path, max_submit_per_challenge=cfg.max_submit_per_challenge)
        self.adapter = adapter if adapter is not None else make_adapter(cfg.adapter)
        self.manual_adapter = ManualAdapter()
        self.backend = backend if backend is not None else make_backend(cfg.backend, cfg)

        # 提交按题目来源路由: manual 题 -> manual_adapter (无判定平台，恒 correct)
        self.submitter = Submitter(
            _SourceRouterAdapter(self), self.state, min_interval=cfg.submit_min_interval
        )
        self.running: dict[str, SolverHandle] = {}
        self.dashboard_server = None
        flags_file = Path(cfg.flags_file)
        self.flags_file = flags_file if flags_file.is_absolute() else REPO_DIR / flags_file
        self._flags_lock = threading.Lock()
        self.session_flags: list[dict] = []   # 本次启动解出的 flag (面板展示用)
        self._started_targets: set[str] = set()   # 已开靶机的 web 题 (仅平台题)
        self._platform_full = False               # 本 tick 内平台 active 满 (409 熔断)
        self._stop = threading.Event()
        self._interrupted = False
        self.paused = False
        self.platform_connected = not isinstance(self.adapter, NoPlatformAdapter)
        self.platform_info: dict = {"base_url": "", "token": ""}
        self.current_round = 1        # 轮转圈数 (第 1 圈 round_time_base 秒/题)
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
            # 恢复圈数: 从已完成的最高圈推断 (做过第 1 圈的题 → 继续第 2 圈)
            max_done = max(
                [getattr(r, "last_done_round", 0) for r in self.state.all_records()] + [0]
            )
            self.current_round = min(max_done + 1, self.cfg.max_rounds)
            if max_done:
                self.log.info("恢复圈数: 第 %d 圈 (上次最高完成 %d)", self.current_round, max_done)
            self._recover()
        # 兜底: 清掉平台上所有残留活跃靶机 (kill -9 强杀等无法优雅退出的残留，
        # 状态文件可能没记录到 running，_recover 管不到)
        closer = getattr(self.adapter, "close_all_active", None)
        if closer and self.platform_connected:
            try:
                n = int(closer() or 0)
                if n:
                    self.log.info("启动清扫: 关闭 %d 个残留活跃靶机", n)
            except Exception as e:
                self.log.warning("启动清扫失败: %s", e)
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
                    self._advance_round_if_done()
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
            # 收尾前最后一次 drain: 退出瞬间可能还有刚提交完的 flag 结果没处理
            try:
                self._drain_results()
            except Exception:
                pass
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
                # 关闭平台残留靶机 (释放 max active 名额，否则新题 start 全 409)
                try:
                    if self.adapter is not None:
                        self.adapter.stop_challenge(rec.id)
                        self.log.info("恢复: %s 平台靶机已关闭", rec.id)
                except Exception as e:
                    self.log.warning("恢复: %s 平台靶机关闭失败: %s", rec.id, e)
                self._finalize(rec.id, FAILED, "Master 重启，solver 进程丢失")

    # ─── 主循环各阶段 ───

    def _sync_challenges(self) -> None:
        """拉取题目列表 (平台 + 手动题池)，同步元数据 (动态分数/解出人数)，新题入队。"""
        if self.platform_connected:
            try:
                metas = self.adapter.list_challenges()
                for meta in metas:
                    self.state.sync_challenge(meta)
            except Exception as e:
                self.log.error("list_challenges 失败: %s", e)
        for meta in self.manual_adapter.list_challenges():
            self.state.sync_challenge(meta)

    def _platform_attempted(self) -> int:
        """平台题的已尝试数 (手动题不计入 max_challenges 上限)。"""
        return sum(
            1
            for r in self.state.all_records()
            if r.attempts >= 1 and r.source != "manual"
        )

    def _fill_slots(self) -> None:
        """
        把队列中的题分发到空闲槽位。

        max_challenges 上限只约束平台新题：已尝试题的重试、手动加入的题
        均不受上限限制 (手动加题是明确意图，不挤占平台名额)。
        """
        self._platform_full = False  # 每个 tick 重置熔断标记
        while len(self.running) < self.cfg.max_solvers:
            allow_new = self._platform_attempted() < self.cfg.max_challenges
            rec = self._next_candidate(allow_new=allow_new)
            if rec is None:
                break
            # 平台 active 名额感知: 需要新 start 的平台 web/binary 题,
            # 已开实例数 >= 平台上限 → 本轮停止 start, 等实例释放。
            # 否则 3 个槽位一口气 start 3 个 → 平台 active 满 →
            # 其余排队题全 409 饿死 (2026-08-20 用户日志实锤)。
            # 复用 URL 的题 (预检失败等容器) 不算新开实例, 不受限。
            if (
                rec.type in ("web", "binary")
                and rec.source != "manual"
                and not (rec.url and getattr(rec, "boot_fails", 0) > 0)
            ):
                if len(self._started_targets) >= self.cfg.platform_max_active:
                    self.log.info(
                        "平台 active 实例已满 (%d/%d)，本轮不再 start，等待释放: %s",
                        len(self._started_targets), self.cfg.platform_max_active, rec.id,
                    )
                    break
            self._dispatch(rec)
            if self._platform_full:
                # 平台 active 满: 本轮停止尝试, 等实例释放 (下一 tick 再试)
                break

    def _next_candidate(self, allow_new: bool = True):
        now = time.time()
        queued = [
            r
            for r in self.state.all_records()
            if r.status == QUEUED
            and (r.next_eligible_at or 0) <= now
            and (allow_new or r.attempts >= 1 or r.source == "manual")
            # 轮转: 本圈已做过的题不再分发, 等圈数推进
            and getattr(r, "last_done_round", 0) < self.current_round
        ]
        if not queued:
            return None
        ordered = prioritizer.rule_order(queued)
        # 基础设施失败 (本圈让路过, 没进过 solver) 最高优先: 空槽先补试它,
        # 平台恢复第一时间拉起, 不浪费圈次 (2026-08-20)
        yielded = [r for r in queued if getattr(r, "yielded_round", 0) == self.current_round]
        if yielded:
            ordered = yielded + [r for r in ordered if r not in yielded]
        if self.cfg.llm_priority:
            ordered = prioritizer.llm_order(ordered)
        return ordered[0]

    def _dispatch(self, rec) -> Optional[SolverHandle]:
        """分发一道题。基础设施失败 → 冷却重排；成功 → RUNNING。"""
        self.state.set_status(rec.id, "dispatched")
        adapter = self.manual_adapter if rec.source == "manual" else self.adapter

        # 1. web/binary 题开靶机 (手动题: 用户输入的 URL 即靶机；binary 远程服务同此)
        if rec.type in ("web", "binary"):
            # 复用: 有 URL 且 (首次 start 后容器未就绪 或 预检失败等容器启动中)
            # → 直接预检等它就绪, 不重复 POST start (平台会再开实例 → 409/泄漏)
            if (
                rec.source != "manual"
                and rec.url
                and (rec.attempts == 0 or getattr(rec, "boot_fails", 0) > 0)
            ):
                url = rec.url
            else:
                try:
                    url = adapter.start_challenge(rec.id)
                    if not url:
                        raise RuntimeError("start_challenge 未返回靶机 URL")
                    rec.url = url
                    if rec.source != "manual":
                        self._started_targets.add(rec.id)
                except Exception as e:
                    self._dispatch_cooldown(rec, f"开靶机失败: {e}")
                    return None

        # 2. 下载附件 (已有本地文件则跳过，重试复用；手动题为本地路径拷贝)
        if rec.attachment_url and not rec.attachment_path:
            try:
                dest = CHALLENGES_DIR / "attachments" / rec.id
                rec.attachment_path = str(adapter.download_attachment(rec.attachment_url, dest))
            except Exception as e:
                self._dispatch_cooldown(rec, f"下载附件失败: {e}")
                return None

        # 2.5 web 靶机存活预检
        #   - 手动题: URL 填错立即终态反馈，不烧一整轮 solver
        #   - 平台题: 每次拿到 URL 都先确认容器就绪再进 solver (首次也要,
        #     否则 solver 白跑一轮); 未就绪 → 复用 URL 等容器起来, 不重复
        #     POST start; 连续多次仍不可达才判死 (容器启动慢, 阈值放宽)
        if rec.type in ("web", "binary") and rec.url:
            if not self._target_alive(rec.url):
                if rec.source == "manual":
                    rec.finished_at = time.time()
                    self.state.set_status(rec.id, FAILED, f"靶机不可达: {rec.url}")
                    self._release_target(rec)
                    self.log.warning("%s 靶机不可达 (%s)，不启动 solver", rec.id, rec.url)
                    return None
                # 平台题: 等容器就绪，连续多次仍不可达才判死
                rec.boot_fails = getattr(rec, "boot_fails", 0) + 1
                if rec.boot_fails >= MAX_BOOT_FAILS:  # 16 x 30s ≈ 8 分钟仍不就绪
                    rec.finished_at = time.time()
                    self.state.set_status(rec.id, FAILED, f"靶机长时间未就绪: {rec.url}")
                    self._release_target(rec)
                    self.log.warning("%s 靶机 %s 约%d分钟未就绪，判失败",
                                     rec.id, rec.url, MAX_BOOT_FAILS * DISPATCH_COOLDOWN // 60)
                    return None
                self._dispatch_cooldown(
                    rec, f"靶机未就绪 ({rec.url})，等容器启动后重试 "
                         f"({rec.boot_fails}/{MAX_BOOT_FAILS})")
                return None
            rec.boot_fails = 0  # 就绪了，清计数

        # 3. 启动 solver
        try:
            handle = self.backend.start(self._to_challenge(rec))
        except Exception as e:
            self._dispatch_cooldown(rec, f"启动 solver 失败: {e}")
            return None

        # 注意: 不删除 work_dir/progress.md。run.sh 有 IS_RESUME 续跑逻辑
        # (progress.md 存在即保留上下文，仅全新目录初始化模板)；
        # 旧 Flags Found 由 mark_flag_seen 去重 (flags_seen 持久化在 master_state.json)，
        # 不会重复提交。曾经这里 unlink progress.md 防误检测，反而破坏了续跑上下文。

        rec.attempts += 1
        rec.yielded_round = 0  # 分发成功, 清基础设施失败让路标记
        rec.started_at = time.time()
        rec.started_round = self.current_round  # 超时预算按此圈 base (2026-08-21)
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

    @staticmethod
    def _target_alive(url: str) -> bool:
        """
        靶机存活探测: 直连 (不走代理)，5s 超时。任一探测方式成功即算存活。

        题库类型多样 (a=web挖掘, b=多阶段渗透, c=面板, d=云, e=对抗规避,
        f1=TCP内存安全, f2=MCU固件)，靶机可能是 HTTP 服务 / TCP 服务 /
        UDP 服务 / 仅 ICMP 可达 —— 不能只按 HTTP 判断 (2026-08-20 修复,
        否则 binary/其他非 HTTP 题永远被误判"未就绪")。

        探测顺序:
          1. HTTP GET      (web 服务, 403/404/500 也算活)
          2. TCP connect   (TCP 服务 / 端口监听)
          3. UDP send/recv (UDP 服务, 收到任何响应算活)
          4. ICMP ping     (host 可达兜底)

        URL 可能是容器视角 (host.docker.internal，宿主机解析不了)，
        探测失败时等价换 127.0.0.1 再试一次。
        """
        import socket
        import urllib.error
        import urllib.request

        candidates = [url]
        if "host.docker.internal" in url:
            candidates.append(url.replace("host.docker.internal", "127.0.0.1"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for u in candidates:
            from urllib.parse import urlparse
            p = urlparse(u)
            host = p.hostname or ""
            port = p.port or (443 if p.scheme == "https" else 80)
            # 1. HTTP GET (web 服务)
            try:
                opener.open(urllib.request.Request(u), timeout=5)
                return True
            except urllib.error.HTTPError:
                return True  # 403/404/500 也是活的 (服务端有响应)
            except Exception:
                pass  # 落到 TCP/UDP/ICMP
            # 2. TCP connect (TCP 服务 / 端口监听)
            try:
                s = socket.create_connection((host, port), timeout=5)
                s.close()
                return True
            except Exception:
                pass
            # 3. UDP 探测 (UDP 服务: 发一个字节, 收到任何响应算活)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(5)
                s.sendto(b"\x00", (host, port))
                s.recvfrom(512)
                s.close()
                return True
            except Exception:
                pass
            # 4. ICMP ping (host 可达兜底; 无 root 权限时跳过, 不算失败)
            # 跳过 loopback: ping 127.0.0.1 永远成功, 会让"容器未就绪"模拟失效
            if host not in ("127.0.0.1", "::1", "localhost"):
                try:
                    import subprocess
                    r = subprocess.run(
                        ["ping", "-c", "1", "-W", "3", host],
                        capture_output=True, timeout=8,
                    )
                    if r.returncode == 0:
                        return True
                except Exception:
                    pass
        return False  # 所有探测方式都不通: 靶机未就绪/DNS 不通

    def _dispatch_cooldown(self, rec, error: str) -> None:
        """分发基础设施失败 (开靶机/下载附件/启动 solver): 本圈让路, 冷却后可再试。

        失败 1 次即让路 (yielded_round 标记本圈已试过), 不动 last_done_round:
        没进过 solver 的题不算"本圈做过", 冷却后按最高优先级重新拉起补试
        (空槽先试失败题), 同时不阻塞圈推进 (2026-08-20 优化)。
        """
        rec.yielded_round = self.current_round
        rec.next_eligible_at = time.time() + DISPATCH_COOLDOWN
        self.state.set_status(rec.id, QUEUED, error)
        # 平台 active 满 (409 max active): 熔断本轮 fill, 别把排队题全试一遍;
        # 冷却拉长到 5 分钟 —— 平台实例释放要分钟级, 30s 重试纯刷屏 (2026-08-20)
        if "max active" in str(error) or "instances reached" in str(error):
            self._platform_full = True
            rec.next_eligible_at = time.time() + PLATFORM_FULL_COOLDOWN
            self.log.warning(
                "分发 %s 失败: %s，平台实例已满，本轮熔断 (5 分钟后重试)",
                rec.id, error,
            )
            return
        self.log.warning(
            "分发 %s 失败: %s，本圈让路，%.0fs 后按高优先级重试",
            rec.id, error, DISPATCH_COOLDOWN,
        )

    def _monitor_solvers(self) -> None:
        """检查每个运行中的 solver: flag / 超时 / 死亡。"""
        for cid, handle in list(self.running.items()):
            rec = self.state.get(cid)
            if rec is None:
                self.running.pop(cid, None)
                continue

            # 0. 同步 cc session id (run.sh 每次调用后写 work_dir/.cc_session)
            wd = getattr(rec, "work_dir", None)
            if wd:
                try:
                    sid = Path(wd, ".cc_session").read_text(encoding="utf-8").strip()
                    if sid and sid != getattr(rec, "cc_session_id", None):
                        rec.cc_session_id = sid
                except OSError:
                    pass

            # 1. flag 检测 (读 progress.md 的 Flags Found 段)
            manual_accepted = False
            for flag in self._read_flags(handle):
                if self.state.mark_flag_seen(cid, flag):
                    self.log.info("检测到 flag: %s -> %s", cid, flag)
                    if rec.source == "manual":
                        # 手动调试模式: 只记录展示，不提交 (用户自行到目标平台提交)
                        self._accept_manual_flag(rec, flag, handle)
                        manual_accepted = True
                        break
                    if self.state.can_submit(cid):
                        self.submitter.submit(cid, flag)
                    else:
                        self.log.warning("%s 达到提交上限，不再提交: %s", cid, flag)
                    if rec.status == RUNNING:
                        self.state.set_status(cid, FLAG_FOUND)
            if manual_accepted:
                continue

            # 2. 死亡检测: solver 自行结束 (正常收工/崩溃)。有 flag 由上面处理;
            #    无 flag → 本圈做完, 保留 session, 下圈再试 (轮转)
            if not self.backend.is_alive(handle):
                if self.state.pending_submits(cid) > 0:
                    continue  # 等提交结果，下轮再判定
                if rec.status == SUBMITTED_CORRECT:
                    self._release(cid)
                    continue
                self._round_rotate(rec, "solver 已结束且无待定 flag")
                continue

            # 3. 超时检测: 有可换的题 → 用当前圈时间上限 (到点换题);
            #    没有其他可做的题 (全终态/解出/无新题) → 轮转换的是空气,
            #    让当前题连续做到兜底上限, 不再被圈超时打断
            if self._has_other_candidate(rec):
                base = self._round_timeout(rec)
                if rec.started_at and time.time() - rec.started_at > base:
                    self.log.warning("%s 本圈超时 (%ds, 第 %d 圈)，换题保留断点", cid, base, self.current_round)
                    self.backend.stop(handle)
                    self._round_rotate(rec, f"本圈超时 ({base}s)")
            else:
                base = self.cfg.solver_timeout
                if rec.started_at and time.time() - rec.started_at > base:
                    self.log.warning("%s 无题可换，连续解题超时 (%ds)，终态", cid, base)
                    self.backend.stop(handle)
                    self._finalize(cid, TIMEOUT, f"连续解题超时 ({base}s)")

    def _has_other_candidate(self, rec) -> bool:
        """是否存在其他可轮转的题: 只要还有 QUEUED 且冷却已过的题就换。

        不看 last_done_round: 每圈最后一批跑时, 同圈已 rotate 的题虽然
        ld=current_round, 但它们是"等待下圈"而不是终态, 轮转仍应换题,
        否则最后一批独占 solver_timeout (生产 3600s) 让全队列干等,
        圈推进被拖慢数倍 (2026-08-20 修复)。
        """
        now = time.time()
        for r in self.state.all_records():
            if r.id == rec.id:
                continue
            if r.status == QUEUED and (r.next_eligible_at or 0) <= now:
                return True
        return False

    def _round_timeout(self, rec) -> int:
        """当前圈的单题时间上限。

        单 flag 题: base (round_time_base + (round-1)*step), 不乘系数;
        多 flag 题: base × flag数 × 0.7 (多 flag 给更多时间, 系数只作用于多 flag)。
        注意: 用 started_round (本次分发所在圈) 而非 current_round —— 长跑题
        solver 存活跨圈时 current_round 已推进, 若按当前圈算预算会被放大,
        占槽时间被拉长 (2026-08-21 实锤: b-01 第 1 圈 3360s 被放大成 5040s)。
        """
        round_no = getattr(rec, "started_round", 0) or self.current_round
        base = self.cfg.round_time_base + (round_no - 1) * self.cfg.round_time_step
        fc = max(1, int(getattr(rec, "flag_count", 1) or 1))
        if fc <= 1:
            return base
        return int(base * fc * 0.7)

    def _round_rotate(self, rec, reason: str) -> None:
        """本圈做完: 释放槽位/靶机, 记 last_done_round, 下圈再试 (最后一圈则终态)。"""
        self.running.pop(rec.id, None)
        # 回收前最后一次同步 cc session (solver 快速结束时轮询可能没读到)
        wd = getattr(rec, "work_dir", None)
        if wd:
            try:
                sid = Path(wd, ".cc_session").read_text(encoding="utf-8").strip()
                if sid:
                    rec.cc_session_id = sid
            except OSError:
                pass
        rec.finished_at = time.time()
        rec.last_done_round = self.current_round
        self._release_target(rec)
        if self.current_round >= self.cfg.max_rounds:
            self.state.set_status(rec.id, TIMEOUT, f"{reason} (达到最大轮数 {self.cfg.max_rounds})")
            self.log.info("%s 终态: %s (达到最大轮数)", rec.id, reason)
        else:
            rec.next_eligible_at = time.time() + 3
            self.state.set_status(rec.id, QUEUED, reason)
            self.log.info("%s 本圈未解 (%s)，保留 session 等第 %d 圈", rec.id, reason, self.current_round + 1)

    def _is_multiflag_longrun(self, rec) -> bool:
        """多 flag 题 solver 存活跨圈长跑 (已得部分 flag 未拿满)。

        这类题不参与"本圈完成"语义: solver 在持续攻剩余 flag, 它永远
        ld < current_round → 会永久阻塞圈推进 → 其他轮转题等不到下一圈
        → 空槽不填、排队题饿死 (2026-08-20 b 系实锤: b-02 6flag 长跑,
        b-01/b-03 轮转后永远不再分发)。
        判定: **solver 存活** 且 0 < flags_correct < flag_count。
        solver 已死的多 flag 未通关题不属于长跑 —— 它们必须正常走
        _recycle_for_next_flag 回收重分发 (场景 F: FakeBackend solver
        写完 flag 就退出, 若误判为长跑会被圈推进跳过回收, attempts 卡 1)。
        """
        if rec is None:
            return False
        fc = max(1, int(getattr(rec, "flag_count", 1) or 1))
        if not (fc > 1 and 0 < getattr(rec, "flags_correct", 0) < fc):
            return False
        # solver 存活检查: running 里且 backend 认为还活着才算长跑
        handle = self.running.get(rec.id)
        if handle is None:
            return False
        try:
            return self.backend.is_alive(handle)
        except Exception:
            return False

    def _advance_round_if_done(self) -> None:
        """所有非终态题都做过本圈 (且无运行中) → 圈数+1, 时间上限提升。"""
        if self.current_round >= self.cfg.max_rounds:
            # 最后一圈结束: 还没解出的题终态化 (否则 QUEUED+ld>=max 永不分发)
            for r in self.state.all_records():
                if r.status == QUEUED and (
                    getattr(r, "last_done_round", 0) >= self.cfg.max_rounds
                    # 基础设施失败题 ld 可能一直 0 (没进过 solver), 但每圈都
                    # 试过了 (yielded_round 累计), 同样该终态 (2026-08-20)
                    or getattr(r, "yielded_round", 0) >= self.cfg.max_rounds
                ):
                    r.finished_at = time.time()
                    self.state.set_status(r.id, TIMEOUT, f"达到最大轮数 ({self.cfg.max_rounds}) 未解出")
                    # 必须释放平台实例, 否则 pending/让路题的容器永久泄漏
                    # 占着 active 名额 → 全平台 409 (2026-08-20 修复)
                    self._release_target(r)
                    self.log.info("%s 终态: 达到最大轮数未解出", r.id)
            return
        recs = self.state.all_records()
        # 排除本圈基础设施失败让路的题 (yielded): 它们没进过 solver 不算"做过",
        # 但也别阻塞圈推进 —— 冷却后会按高优先级补试
        # 同时排除多 flag 跨圈长跑题 (见 _is_multiflag_longrun): solver 存活
        # 持续攻剩余 flag, 不算"本圈待完成", 否则永久阻塞圈推进 (2026-08-20)
        active = [
            r for r in recs
            if (r.status in ACTIVE_STATES or r.status == QUEUED)
            and getattr(r, "yielded_round", 0) != self.current_round
            and not self._is_multiflag_longrun(r)
        ]
        # 注意: active 空 (全终态或全 yielded) 也要推进圈数, 否则
        # 失败题永远停在当前圈 → master 永不退出 (2026-08-20 修复)
        if active and any(getattr(r, "last_done_round", 0) < self.current_round for r in active):
            return
        # 多 flag 长跑题不阻塞圈推进: 只等普通 solver 结束
        if any(
            not self._is_multiflag_longrun(self.state.get(cid))
            for cid in self.running
        ):
            return  # 还有普通 solver 在跑, 等它结束
        self.current_round += 1
        # 日志显示单 flag 圈超时 = base (多 flag 系数只作用于多 flag 题)
        base = self.cfg.round_time_base + (self.current_round - 1) * self.cfg.round_time_step
        self.log.info(
            "=== 全部题目已完成第 %d 圈，进入第 %d 圈 (单 flag 时间上限 %d 秒) ===",
            self.current_round - 1, self.current_round, base,
        )

    def _drain_results(self) -> None:
        """处理提交结果。"""
        for res in self.submitter.results():
            cid, flag, status = res["cid"], res["flag"], res["status"]
            rec = self.state.get(cid)
            if rec is None:
                continue
            self.state.record_submit_result(cid, flag, status, res.get("message", ""))

            if status == "correct":
                extra = res.get("data") or {}
                # duplicate: 平台幂等返回"该 flag 已计过分"。
                # ① 终态已通关 (flags_seen 正常) 时: 防御性跳过, 不重复计分/不回收。
                # ② 非终态 (容器重启丢 master_state.json 后重做, flags_seen 丢失):
                #    平台确认该 flag 已计分 = 这题实际已通关 → 必须恢复终态并释放,
                #    否则 solver 每轮重做都提交同 flag → 又 duplicate → 死循环
                #    (2026-08-20 E3-03 实锤: 得分成功后 19:16:54 又被重新调度)。
                if extra.get("duplicate"):
                    if rec.status == SUBMITTED_CORRECT:
                        self.log.warning("%s 重复 flag 已计过分，跳过: %s", cid, flag)
                        continue
                    fc = max(1, int(getattr(rec, "flag_count", 1) or 1))
                    if fc <= 1 or (rec.flags_correct + 1) >= fc:
                        self.state.mark_correct(cid, flag, all_flags_done=True)
                        self.log.warning(
                            "%s duplicate 恢复通关终态 (平台已计分): %s", cid, flag)
                        handle = self.running.pop(cid, None)
                        if handle:
                            self.backend.stop(handle)
                        self._release_target(rec)
                    else:
                        self.state.mark_correct(cid, flag, all_flags_done=False)
                        self.log.warning(
                            "%s duplicate 已计分, 记录进度 (%d/%d), 继续攻剩余 flag",
                            cid, rec.flags_correct, fc)
                    continue
                # 多 flag 题: 平台返回的进度判定是否通关
                total = int(extra.get("total_flag_count") or rec.flag_count or 1)
                done_cnt = int(extra.get("correct_flag_count") or (rec.flags_correct + 1))
                all_done = done_cnt >= total
                self.state.mark_correct(cid, flag, all_flags_done=all_done)
                self._log_flag(rec, flag, auto_submitted=True)
                if all_done:
                    self.log.info("=== FLAG ACCEPTED: %s %s (通关 %d/%d) ===",
                                  cid, flag, done_cnt, total)
                    handle = self.running.pop(cid, None)
                    if handle:
                        self.backend.stop(handle)  # run.sh 找到 flag 后通常已自行退出
                    self._release_target(rec)
                else:
                    # 多 flag 未通关: solver 活着就不动它 (同一 codex 会话继续攻剩余
                    # flag，run.sh 在拿满前不退出)；死了才回队列重跑
                    self.log.info("=== FLAG ACCEPTED: %s %s (%d/%d，未通关继续) ===",
                                  cid, flag, done_cnt, total)
                    handle = self.running.get(cid)
                    if handle is not None and self.backend.is_alive(handle):
                        self.log.info("%s solver 存活，继续攻剩余 flag (%d/%d)",
                                      cid, done_cnt, total)
                    else:
                        self._recycle_for_next_flag(rec)
            elif status == "wrong":
                self.log.warning("%s flag 提交错误: %s (solver 继续跑)", cid, flag)
            else:  # error / skipped
                self.log.error("%s 提交未成功 (%s): %s", cid, status, res.get("message", ""))
            # 回写平台判定到 work_dir (run.sh 收工确认制: solver 据此决定收工还是继续)
            self._write_submit_result(rec, flag, status, res.get("message", ""))

    def _write_submit_result(self, rec, flag: str, status: str, message: str) -> None:
        """把平台判定追加写回 work_dir/submit_results.jsonl，run.sh 据此纠错/收工。
        追加式 (jsonl) 而非单文件覆盖: 多 flag 题多次提交不丢结果。"""
        wd = getattr(rec, "work_dir", None)
        if not wd:
            return
        try:
            path = Path(wd) / "submit_results.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "flag": flag,
                    "status": status,
                    "message": message[:200],
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _recycle_for_next_flag(self, rec) -> None:
        """
        多 flag 题收到一个正确 flag 但未通关: 回队列继续攻剩余 flag。

        已提交的 flag 保留在 flags_seen (防止下一轮 solver 重解同一 flag 后再提交，
        平台会返回 duplicate)；并把已得 flag 注入 hint，让 codex 明确找"不同的" flag。
        """
        handle = self.running.pop(rec.id, None)
        if handle:
            try:
                self.backend.stop(handle)
            except Exception as e:
                self.log.error("停止 %s 失败: %s", rec.id, e)
        got = list(rec.flags_submitted)  # 已提交成功的 flag
        if got:
            rec.description = (
                (rec.description or "").split("\n\n[多 flag 进度]")[0]
                + "\n\n[多 flag 进度] 该题共 " + str(rec.flag_count) + " 个 flag，已提交 "
                + str(len(got)) + " 个: " + ", ".join(got)
                + "。这些 flag 已计分，不要再提交，去找剩余的 flag (注意换攻击点/入口)。"
            )
        # 注意: 不能清 results_received! pending_submits 按
        # flags_submitted - results_received 计算, 清空会让已提交的 flag
        # 永远 pending → 死亡检测 continue → 卡 RUNNING → 轮转冻结 (2026-08-20)
        rec.next_eligible_at = time.time() + 3
        rec.attempts -= 1         # 不消耗重试配额 (这是同一轮攻略的延续)
        self.state.set_status(rec.id, QUEUED)

    def _accept_manual_flag(self, rec, flag: str, handle: SolverHandle) -> None:
        """手动模式 flag 闭环: 不走提交器，记录 + 展示 + 回收 solver。"""
        # record_submit_result 消掉 pending 计数 (不走 submitter 时也要闭环)
        self.state.record_submit_result(rec.id, flag, "manual_display")
        self.state.mark_correct(rec.id, flag)
        self._log_flag(rec, flag, auto_submitted=False)
        self.log.info("=== FLAG (手动模式，仅展示): %s %s ===", rec.id, flag)
        self.running.pop(rec.id, None)
        try:
            self.backend.stop(handle)
        except Exception as e:
            self.log.error("停止 %s 失败: %s", rec.id, e)
        self._release_target(rec)

    def _log_flag(self, rec, flag: str, auto_submitted: bool) -> None:
        """flag 落盘 flags.jsonl (历史归档) + 内存列表 (本次启动的面板展示)。"""
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "cid": rec.id,
            "title": rec.title,
            "type": rec.type,
            "source": rec.source,
            "flag": flag,
            "auto_submitted": auto_submitted,
        }
        self.session_flags.append(entry)   # 本次启动的 flag (面板只展示这些)
        try:
            with self._flags_lock:
                with open(self.flags_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            self.log.error("写 %s 失败: %s", self.flags_file, e)

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

        # 手动题不自动重试: 手动测试失败由用户决定是否重跑 (score/solve_count
        # 均为 0 会被 rarity 公式判成"高价值"，但那是平台题的语义)
        if rec.source == "manual":
            self.state.set_status(cid, status, error)
            self.log.info("%s 手动题不重试，终态: %s", cid, status)
            return

        # 轮转机制 (取代旧"高价值题才重试"): 失败/崩溃 → 本圈让路,
        # 下圈再试; 圈数由 max_rounds 兜底 (2026-08-20)
        if self.current_round >= self.cfg.max_rounds:
            self.state.set_status(cid, TIMEOUT, f"{error} (达到最大轮数 {self.cfg.max_rounds})")
            self.log.info("%s 终态: %s (达到最大轮数 %d)", cid, error or "-", self.cfg.max_rounds)
        else:
            rec.last_done_round = self.current_round
            rec.next_eligible_at = time.time() + RETRY_COOLDOWN
            rec.yielded_round = 0
            self.state.set_status(cid, QUEUED, error)
            self.log.info(
                "%s 失败 (%s)，本圈让路，第 %d 圈再试",
                cid, error or "-", self.current_round + 1,
            )

    def _release(self, cid: str) -> None:
        """槽位释放 (correct 后由 drain 处理过状态，这里只清 handle)。"""
        handle = self.running.pop(cid, None)
        if handle:
            self.backend.stop(handle)

    def _release_target(self, rec) -> None:
        """
        释放 web 题靶机 (赛方有数量/时长限制，即用即释放)。

        close 失败会泄漏平台槽位 (腾讯活跃上限 3，泄漏一个就永久少一个并发)，
        所以失败时退避重试 3 次，全部失败才放弃并告警。
        """
        if rec.id in self._started_targets:
            self._started_targets.discard(rec.id)
            last_err = None
            for wait in (0, 5, 15):
                if wait:
                    time.sleep(wait)
                try:
                    self.adapter.stop_challenge(rec.id)
                    rec.url = None  # 实例已释放, URL 失效 (下次干净重 start)
                    self.log.info("靶机已释放: %s", rec.id)
                    return
                except Exception as e:
                    last_err = e
            self.log.error("释放靶机 %s 失败 (平台槽位可能泄漏!): %s", rec.id, last_err)

    # ─── 停止判定 / 收尾 ───

    def _should_exit(self) -> bool:
        if self.cfg.resident:
            return False  # 常驻模式: 面板永远在线，等待手动加题/平台接入
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
        attempted = self._platform_attempted()
        # 上限只挡平台新题；重试与手动题不受限 (与 _fill_slots 语义一致)
        if queued:
            has_retry = any(r.attempts >= 1 for r in queued)
            has_manual = any(r.source == "manual" for r in queued)
            allow_new = attempted < self.cfg.max_challenges
            if has_retry or has_manual or allow_new:
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
            flag_count=getattr(rec, "flag_count", 1),
            cc_session_id=getattr(rec, "cc_session_id", None),
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

    def update_config(self, max_solvers=None, max_challenges=None, agent_cli=None) -> None:
        """运行时调整并发数/题目上限/引擎 (面板用)。"""
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
        if agent_cli is not None:
            agent_cli = str(agent_cli).strip().lower()
            if agent_cli not in ("codex", "claude", "hermes"):
                raise ValueError("agent_cli 必须是 codex / claude / hermes")
            self.cfg.agent_cli = agent_cli
            # 同步到 backend (DockerBackend 实例), 新容器生效
            if hasattr(self.backend, "agent_cli"):
                self.backend.agent_cli = agent_cli
            self.log.info("引擎已切换: %s (对新容器生效，运行中的容器保持原引擎)", agent_cli)
        self.log.info(
            "配置已更新: max_solvers=%d max_challenges=%d agent_cli=%s",
            self.cfg.max_solvers, self.cfg.max_challenges, self.cfg.agent_cli,
        )

    # ─── 手动加题 / 平台接入 (面板 API) ───

    def add_manual_challenges(self, items: list[dict]) -> list[str]:
        """
        面板手动批量加题。items: [{type,title,url,attachment,description,score,solve_count}]。
        校验失败的条目跳过并记录，不影响其他条目。返回入队成功题目 id。
        """
        added = []
        for item in items:
            try:
                ch = self._build_manual_challenge(item)
                # 同名题已终态 (解过/失败过) -> 换新 id 重新入队
                base_id, n = ch.id, 2
                while (r := self.state.get(ch.id)) is not None and r.status in TERMINAL_STATES:
                    ch.id = f"{base_id}-{n}"
                    n += 1
                self.manual_adapter.add(ch)
                self.state.sync_challenge(ch)
                added.append(ch.id)
            except (ValueError, FileNotFoundError) as e:
                self.log.warning("手动加题失败 (%s): %s", item.get("title", "?"), e)
        if added:
            self.log.info("手动加题 %d 道: %s", len(added), added)
        return added

    def _build_manual_challenge(self, item: dict) -> Challenge:
        ctype = str(item.get("type", "")).strip().lower()
        if ctype not in ("web", "crypto", "misc", "binary"):
            raise ValueError(f"题目类型必须是 web/crypto/misc/binary，收到: {ctype!r}")
        url = str(item.get("url", "")).strip()
        attachment = str(item.get("attachment", "")).strip()
        title = str(item.get("title", "")).strip()

        if ctype in ("web", "binary"):
            if not url:
                raise ValueError(f"{ctype} 题必须填 URL")
            if ctype == "binary" and attachment and not Path(attachment).is_file():
                raise FileNotFoundError(f"附件不存在: {attachment}")
            if ctype == "binary" and attachment:
                attachment = f"manual://{Path(attachment).resolve()}"
        else:
            if not attachment:
                raise ValueError(f"{ctype} 题必须填附件路径")
            if not Path(attachment).is_file():
                raise FileNotFoundError(f"附件不存在: {attachment}")
            # 附件本地路径编码进 attachment_url，分发时经 download_attachment 拷入挑战目录
            attachment = f"manual://{Path(attachment).resolve()}"

        return Challenge(
            id=f"manual-{ctype}-{title or Path(url or attachment).stem}"[:60],
            title=title or url or Path(attachment or "").stem or ctype,
            type=ctype,
            score=int(item.get("score") or 0),
            solve_count=int(item.get("solve_count") or 0),
            description=str(item.get("description", "")).strip(),
            url=url or None,
            attachment_url=attachment or None,
            source="manual",
        )

    def connect_platform(self, base_url: str, token: str = "", adapter_type: str = "live") -> dict:
        """
        面板「平台接入」: 热切换到真实赛方 API 并立即拉题。
        adapter_type: live(通用赛方 API) / tsec(腾讯 TSec openapi)。
        base_url/token 由用户在面板手动输入 (参考赛方提供的接入信息)。
        """
        base_url = base_url.strip()
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("API 地址必须以 http:// 或 https:// 开头")
        if adapter_type == "tsec":
            from adapters.tsec import TSecAdapter
            if not token:
                raise ValueError("TSec 接入需要 Token (BENCHMARK_TOKEN)")
            candidate = TSecAdapter(base_url, token)
        elif adapter_type == "dasctf":
            from adapters.dasctf import DasctfAdapter
            if not token:
                raise ValueError("DASCTF 接入需要 AccessKey")
            candidate = DasctfAdapter(base_url, token)
        else:
            from adapters.live import LiveAdapter
            candidate = LiveAdapter(base_url, token)
        metas = candidate.list_challenges()  # 连通性验证，失败抛异常
        # 接入成功即清扫平台残留靶机 (面板模式启动时 adapter 为空，启动清扫不生效，补这里)
        closer = getattr(candidate, "close_all_active", None)
        if closer:
            try:
                n = int(closer() or 0)
                if n:
                    self.log.info("平台接入清扫: 关闭 %d 个残留活跃靶机", n)
            except Exception as e:
                self.log.warning("平台接入清扫失败: %s", e)
        self.adapter = candidate
        self.platform_connected = True
        self.platform_info = {"base_url": base_url, "token": "***" if token else ""}
        for meta in metas:
            self.state.sync_challenge(meta)
        self.log.info("平台已接入(%s): %s，拉到 %d 道题", adapter_type, base_url, len(metas))
        return {"base_url": base_url, "adapter": adapter_type, "challenges": len(metas)}

    def stop_solver(self, cid: str) -> bool:
        handle = self.running.get(cid)
        if handle is None:
            return False
        self.backend.stop(handle)
        self._finalize(cid, MANUAL_STOP, "手动终止")
        return True

    def force_retry(self, cid: str) -> bool:
        """面板手动重试: 失败/终止的题强制重新排队分发。

        语义: 复用原 work_dir (run.sh 保留 board.md/progress.md/submit_results.jsonl),
        Codex 新会话读 board.md 恢复状态继续, 非从 0 做。
        attempts 清零 = 完全重来, 不消耗原尝试次数; 任何状态都重发 (用户明确要求)。
        """
        rec = self.state.get(cid)
        if rec is None:
            return False
        if rec.status == RUNNING:
            self.log.warning("面板手动重试: %s 正在运行, 先终止再重试", cid)
            return False
        rec.attempts = 0
        rec.error = ""
        rec.finished_at = 0.0
        rec.next_eligible_at = 0.0
        self.state.set_status(cid, QUEUED)
        self.log.info("面板手动重试: %s 强制重新排队 (work_dir=%s)", cid, rec.work_dir)
        return True

    def kill_all(self) -> dict:
        """面板「Kill All」: 停掉所有 running solver (容器+平台靶机)，再清扫平台残留靶机。"""
        killed = {"solver": 0, "targets": 0}
        # 1. 停所有 running solver (容器 + 对应平台靶机)
        for cid, handle in list(self.running.items()):
            try:
                self.backend.stop(handle)
                killed["solver"] += 1
            except Exception as e:
                self.log.warning("kill-all: %s 容器停止失败: %s", cid, e)
            try:
                if self.adapter is not None:
                    self.adapter.stop_challenge(cid)
                    killed["targets"] += 1
            except Exception as e:
                self.log.warning("kill-all: %s 靶机关闭失败: %s", cid, e)
            self._finalize(cid, MANUAL_STOP, "面板 kill-all")
        self.running.clear()
        # 2. 平台清扫: 关掉所有活跃残留靶机 (释放 max active 名额)
        if self.platform_connected and self.adapter is not None:
            closer = getattr(self.adapter, "close_all_active", None)
            if closer:
                try:
                    killed["targets"] += int(closer() or 0)
                except Exception as e:
                    self.log.warning("kill-all: 平台清扫失败: %s", e)
        self.log.info("kill-all: 已停 %d solver、关闭 %d 靶机", killed["solver"], killed["targets"])
        return killed


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


def _apply_token_scoping(cfg: Config) -> None:
    """
    按 TSEC_TOKEN 隔离状态/flag 文件 (用户需求: 每次 token 一份，历史保留不删)。

    不同 token 是不同的跑分任务，题目进度互不可比 —— 复用旧 token 的状态会把
    上一次的 submitted_correct/running/重试计数带进新任务 (实测下午启动加载了
    凌晨 57 条记录，重试配额直接耗尽)。
    同一 token 重启则正确恢复自己的进度。
    """
    token = os.environ.get("TSEC_TOKEN", "").strip()
    if cfg.adapter == "tsec" and token:
        suffix = token[:8]
        for attr in ("state_file", "flags_file"):
            p = Path(getattr(cfg, attr))
            stem = p.stem
            if not stem.endswith(f"_{suffix}"):  # 已带后缀则幂等
                setattr(cfg, attr, str(p.with_name(f"{stem}_{suffix}{p.suffix}")))


def main() -> int:
    parser = argparse.ArgumentParser(description="CTF Master 多题调度器")
    parser.add_argument("--config", default=str(SCRIPT_DIR / "master_config.json"))
    args = parser.parse_args()

    cfg = Config.load(Path(args.config))
    _apply_token_scoping(cfg)
    log_file = Path(cfg.log_file)
    if not log_file.is_absolute():
        log_file = REPO_DIR / log_file
    setup_logging(log_file)
    logging.getLogger("master").info(
        "状态文件: %s (token 隔离)", cfg.state_file,
    )

    logging.getLogger("master").info(
        "Master 启动: adapter=%s backend=%s max_solvers=%d max_challenges=%d",
        cfg.adapter, cfg.backend, cfg.max_solvers, cfg.max_challenges,
    )
    return Master(cfg).run()


if __name__ == "__main__":
    sys.exit(main())
