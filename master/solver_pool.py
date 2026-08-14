#!/usr/bin/env python3
"""
solver_pool.py -- Solver 后端抽象与实现 (master-agent-spec.md §4.4)。

Master 通过 SolverBackend 接口管理 Solver 生命周期，后端可替换:
  - ProcessBackend: Phase 1 主力，直接以子进程运行现有 run.sh (容器外)
  - DockerBackend:  Phase 2，每题一个容器，销毁重建 (未实现)
  - FakeBackend:    开发/测试用，不起 codex，线程模拟解题生命周期

Solver 交互协议 (与容器内一致): Master 只读 work_dir/progress.md 检测 flag，
其余 (codex.log/hermes.log/board.md) 由 Solver 自行维护。
"""

from __future__ import annotations

import abc
import hashlib
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from adapters.base import Challenge

SCRIPT_DIR = Path(__file__).resolve().parent          # master/
REPO_DIR = SCRIPT_DIR.parent                          # 仓库根
CHALLENGES_DIR = REPO_DIR / "challenges"


def _safe_name(cid: str) -> str:
    """cid 转安全文件名。"""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", cid)


@dataclass
class SolverHandle:
    """一个运行中的 Solver 实例 (内存对象，不持久化)。"""

    cid: str
    type: str
    work_dir: Path                     # Solver 的解题现场 (progress.md 所在目录)
    started_at: float
    proc: Optional[subprocess.Popen] = None   # ProcessBackend
    container: Optional[str] = None           # DockerBackend (Phase 2)
    opaque: dict = field(default_factory=dict)  # 后端私有数据 (FakeBackend 线程等)


class SolverBackend(abc.ABC):
    """Solver 生命周期后端抽象。"""

    @abc.abstractmethod
    def start(self, ch: Challenge) -> SolverHandle:
        """启动 Solver。失败抛异常 (Master 负责 cooldown 重试)。"""

    @abc.abstractmethod
    def is_alive(self, handle: SolverHandle) -> bool:
        """Solver 进程/容器是否仍在运行。"""

    @abc.abstractmethod
    def stop(self, handle: SolverHandle) -> None:
        """优雅终止: 先 SIGINT/优雅停止，超时强杀。"""


# ───────────────────────── ProcessBackend ─────────────────────────


class ProcessBackend(SolverBackend):
    """
    Phase 1 主力后端: 直接 `bash run.sh` 起一个 Solver 子进程。

    work_dir 不由 Master 指定，而是按 run.sh 的命名规则预测
    (与 dashboard.py 的预测逻辑一致，保持 Solver 侧零改动):
      web:         challenges/manual_web_<md5(url)[:12]>
      crypto/misc: challenges/manual_<type>_<md5(attachment_path)[:12]>
    注意 attachment 的哈希对象是"传给 run.sh 的路径字符串"，必须与
    构建 cmd 时使用的字符串完全一致。
    """

    def start(self, ch: Challenge) -> SolverHandle:
        work_dir = self._predict_work_dir(ch)

        cmd = ["bash", str(REPO_DIR / "solver" / "run.sh"), "--type", ch.type]
        if ch.type == "web":
            cmd += ["--url", ch.url or ""]
        else:
            cmd += ["--attachment", str(ch.attachment_path)]
        hint = (ch.description or "").strip() or "(无)"
        cmd += ["--hint", hint]

        # run.sh 的 stdout 横幅收集到 master_logs (codex/hermes 日志在 work_dir 内)
        log_path = REPO_DIR / "master_logs" / f"{_safe_name(ch.id)}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        log_f = open(log_path, "w", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=str(REPO_DIR),
                start_new_session=True,  # 独立进程组，方便整组终止
            )
        finally:
            log_f.close()  # 子进程持有 fd 副本，父进程侧关闭

        return SolverHandle(
            cid=ch.id,
            type=ch.type,
            work_dir=work_dir,
            started_at=time.time(),
            proc=proc,
        )

    @staticmethod
    def _predict_work_dir(ch: Challenge) -> Path:
        if ch.type == "web":
            digest = hashlib.md5(ch.url.encode()).hexdigest()[:12]
            name = f"manual_web_{digest}"
        else:
            digest = hashlib.md5(str(ch.attachment_path).encode()).hexdigest()[:12]
            name = f"manual_{ch.type}_{digest}"
        return CHALLENGES_DIR / name

    def is_alive(self, handle: SolverHandle) -> bool:
        return handle.proc is not None and handle.proc.poll() is None

    def stop(self, handle: SolverHandle) -> None:
        """
        优雅终止: SIGINT 进程组 (run.sh trap 优雅清理)，8s 后强杀。

        注意 run.sh 退出后组内可能残留孙进程 (如在途 hermes chat)，
        wait() 收割 leader 后 pgid 就查不到了，所以先取 pgid、
        leader 退出后再对组内残留补一刀 SIGKILL。
        """
        proc = handle.proc
        if proc is None:
            return
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError):
            pgid = None

        self._signal_group(proc, signal.SIGINT)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self._kill_group(pgid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return
        # leader 已退出，清扫组内残留孙进程
        time.sleep(0.5)
        self._kill_group(pgid)

    @staticmethod
    def _kill_group(pgid: Optional[int]) -> None:
        if pgid is None:
            return
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    @staticmethod
    def _signal_group(proc: subprocess.Popen, sig: int) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            pass


# ───────────────────────── DockerBackend (Phase 2) ─────────────────────────


class DockerBackend(SolverBackend):
    """
    Phase 2 后端: 每题一个容器，销毁重建 (master-agent-spec.md §4.4 / §7)。

    挂载:
      challenges/ → /opt/ctf-agent/challenges   整目录 bind mount，容器内 run.sh
        创建的 work_dir 直接落到宿主机 (删容器不删数据，Master 靠这个读 progress.md)
      <snapshot>/codex → /root/.codex            精制快照 (cred_snapshot.py)
      <snapshot>/hermes → /root/.hermes          同上

    附件路径语义: 传给 run.sh 的是容器内路径
      /opt/ctf-agent/challenges/attachments/<cid>/<name>
    run.sh 用该路径字符串做 md5 命名 work_dir，所以本类的 _predict_work_dir
    必须用容器路径预测 (与 ProcessBackend 的宿主路径语义不同)。
    """

    CONTAINER_ROOT = Path("/opt/ctf-agent")

    def __init__(self, image: str = "ctf-solver:latest", snapshot_dir: Optional[Path] = None):
        self.image = image
        if snapshot_dir is not None:
            self.snapshot_dir = Path(snapshot_dir)
        else:
            self.snapshot_dir = REPO_DIR / "cred_snapshots" / "current"
        if not self.snapshot_dir.exists():
            raise FileNotFoundError(
                f"凭据快照不存在: {self.snapshot_dir} (先运行 python3 cred_snapshot.py)"
            )

    # ─── 路径换算 ───

    def _container_attachment(self, ch: Challenge) -> Optional[Path]:
        """宿主机附件路径 → 容器内路径。"""
        if not ch.attachment_path:
            return None
        host = Path(ch.attachment_path).resolve()
        rel = host.relative_to(CHALLENGES_DIR.resolve())
        return self.CONTAINER_ROOT / "challenges" / rel

    def _predict_work_dir(self, ch: Challenge) -> Path:
        """与 run.sh 命名规则一致，但附件用容器路径语义。"""
        if ch.type == "web":
            digest = hashlib.md5(ch.url.encode()).hexdigest()[:12]
            name = f"manual_web_{digest}"
        else:
            digest = hashlib.md5(str(self._container_attachment(ch)).encode()).hexdigest()[:12]
            name = f"manual_{ch.type}_{digest}"
        return CHALLENGES_DIR / name  # bind mount 下与容器内同名

    # ─── 生命周期 ───

    def start(self, ch: Challenge) -> SolverHandle:
        work_dir = self._predict_work_dir(ch)
        cname = f"solver-{_safe_name(ch.id)}-{int(time.time())}"

        cmd = [
            "docker", "run", "-d", "--name", cname,
            "-v", f"{CHALLENGES_DIR}:{self.CONTAINER_ROOT}/challenges",
            "-v", f"{self.snapshot_dir}/codex:/root/.codex",
            "-v", f"{self.snapshot_dir}/hermes:/root/.hermes",
            "--memory", "4g",
            self.image,
            # 镜像 ENTRYPOINT 已 exec run.sh，这里只传 run.sh 参数
            "--type", ch.type,
        ]
        if ch.type == "web":
            cmd += ["--url", ch.url or ""]
        else:
            cmd += ["--attachment", str(self._container_attachment(ch))]
        cmd += ["--hint", (ch.description or "").strip() or "(无)"]

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if res.returncode != 0:
            raise RuntimeError(f"docker run 失败: {res.stderr.strip()[:500]}")
        return SolverHandle(
            cid=ch.id,
            type=ch.type,
            work_dir=work_dir,
            started_at=time.time(),
            container=cname.strip(),
        )

    def is_alive(self, handle: SolverHandle) -> bool:
        if not handle.container:
            return False
        try:
            res = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", handle.container],
                capture_output=True, text=True, timeout=15,
            )
        except subprocess.TimeoutExpired:
            return True  # docker daemon 卡顿时保守视为存活
        return res.returncode == 0 and res.stdout.strip() == "true"

    def stop(self, handle: SolverHandle) -> None:
        """docker stop (SIGTERM → run.sh trap 优雅清理 → 8s 后强杀) + rm。"""
        if not handle.container:
            return
        for args in (
            ["docker", "stop", "-t", "8", handle.container],
            ["docker", "rm", "-f", handle.container],
        ):
            try:
                subprocess.run(args, capture_output=True, timeout=60)
            except subprocess.TimeoutExpired:
                pass

    def logs_tail(self, handle: SolverHandle, n: int = 50) -> str:
        """容器 stdout 尾部 (run.sh 横幅; codex/hermes 日志在挂载的 work_dir 里)。"""
        if not handle.container:
            return ""
        try:
            res = subprocess.run(
                ["docker", "logs", "--tail", str(n), handle.container],
                capture_output=True, text=True, timeout=15,
            )
            return (res.stdout or "") + (res.stderr or "")
        except subprocess.TimeoutExpired:
            return ""


# ───────────────────────── FakeBackend (开发/测试) ─────────────────────────


class FakeBackend(SolverBackend):
    """
    测试用假 Solver: 不起 codex，用线程模拟解题生命周期，验证 Master 调度逻辑。

    行为由题目 title 中的标记驱动:
      含 "[fail]"  -> 永远不产出 flag，直到被 Master 停掉 (测超时/重试路径)
      含 "[wrong]" -> 写一个错误 flag 后退出 (测错误提交路径)
      其他         -> solve_delay 秒后写入正确 flag 并退出
    """

    def __init__(self, flag_lookup: Optional[Callable[[str], str]] = None, solve_delay: float = 1.0):
        self.flag_lookup = flag_lookup
        self.solve_delay = solve_delay

    def start(self, ch: Challenge) -> SolverHandle:
        work_dir = CHALLENGES_DIR / "fake" / _safe_name(ch.id)
        handle = SolverHandle(
            cid=ch.id,
            type=ch.type,
            work_dir=work_dir,
            started_at=time.time(),
        )
        handle.opaque["stop_event"] = threading.Event()
        t = threading.Thread(target=self._simulate, args=(ch, handle), daemon=True)
        handle.opaque["thread"] = t
        t.start()
        return handle

    def _simulate(self, ch: Challenge, handle: SolverHandle) -> None:
        ev: threading.Event = handle.opaque["stop_event"]
        if "[fail]" in ch.title:
            ev.wait()  # 一直"解题"直到被停止
            return
        # 分几步写假日志，供面板 SSE 调试 (真实后端日志由 run.sh 生成)
        handle.work_dir.mkdir(parents=True, exist_ok=True)
        steps = max(1, int(self.solve_delay / 0.25))
        for i in range(steps):
            if ev.wait(0.25):
                return
            with open(handle.work_dir / "codex.log", "a", encoding="utf-8") as f:
                f.write(f"[fake-codex] {ch.id} 分析步骤 {i + 1}/{steps}...\n")
            with open(handle.work_dir / "hermes.log", "a", encoding="utf-8") as f:
                f.write(f"[fake-hermes] 第 {i + 1} 次监控: 正常推进，无需介入\n")
        if "[wrong]" in ch.title:
            flag = f"flag{{wrong_{_safe_name(ch.id)}}}"
        elif self.flag_lookup:
            flag = self.flag_lookup(ch.id)
        else:
            flag = f"flag{{fake_{_safe_name(ch.id)}}}"
        (handle.work_dir / "progress.md").write_text(
            f"## Flags Found\n{flag}\n", encoding="utf-8"
        )

    def is_alive(self, handle: SolverHandle) -> bool:
        t: Optional[threading.Thread] = handle.opaque.get("thread")
        return t is not None and t.is_alive()

    def stop(self, handle: SolverHandle) -> None:
        handle.opaque["stop_event"].set()
