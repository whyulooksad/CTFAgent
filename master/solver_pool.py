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
import json
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

    def __init__(self, agent_cli: str = "codex"):
        self.agent_cli = agent_cli  # codex | claude | hermes (传给 run.sh 环境变量)

    def start(self, ch: Challenge) -> SolverHandle:
        work_dir = self._predict_work_dir(ch)

        cmd = ["bash", str(REPO_DIR / "solver" / "run.sh"), "--type", ch.type]
        cmd += ["--challenge-id", ch.id]  # run.sh 用 id 哈希命名 work_dir (url 会被平台复用)
        if ch.type in ("web", "binary"):
            cmd += ["--url", ch.url or ""]
            if ch.type == "binary" and ch.attachment_path:
                cmd += ["--attachment", str(ch.attachment_path)]  # 可选制品
        else:
            cmd += ["--attachment", str(ch.attachment_path)]
        hint = (ch.description or "").strip() or "(无)"
        cmd += ["--hint", hint]
        fc = int(getattr(ch, "flag_count", 1) or 1)
        if fc > 1:
            cmd += ["--flag-count", str(fc)]  # 多 flag: solver 拿满前不退出
        # 轮转断点: 上一圈 session id 传给 run.sh → claude --resume 恢复会话
        sid = getattr(ch, "cc_session_id", None)
        if sid:
            cmd += ["--resume-session", sid]

        # run.sh 的 stdout 横幅收集到 master_logs (codex/hermes 日志在 work_dir 内)
        # 每次尝试追加写入 (不覆盖)，带分隔头 -- "w" 模式曾把重试前一轮的日志抹掉
        log_path = REPO_DIR / "master_logs" / f"{_safe_name(ch.id)}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        log_f = open(log_path, "a", encoding="utf-8")
        print(f"\n===== [master] dispatch {time.strftime('%Y-%m-%d %H:%M:%S')} "
              f"work_dir={work_dir.name} =====", file=log_f, flush=True)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=str(REPO_DIR),
                start_new_session=True,  # 独立进程组，方便整组终止
                env={**os.environ, "AGENT_CLI": self.agent_cli},  # 引擎传给 run.sh (默认 codex)
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
        # 与 run.sh 的命名规则一致 (run.sh 优先用 challenge-id 做 md5 命名 work_dir，
        # master 只是预测)。用 id: 同题重试同目录，不同题永不撞 (url 会被平台复用)。
        digest = hashlib.md5(ch.id.encode()).hexdigest()[:12]
        return CHALLENGES_DIR / f"manual_{ch.type}_{digest}"

    def is_alive(self, handle: SolverHandle) -> bool:
        return handle.proc is not None and handle.proc.poll() is None

    def stop(self, handle: SolverHandle) -> None:
        """
        优雅终止: SIGINT 进程组 (run.sh trap 优雅清理)，8s 后强杀。

        注意 run.sh 退出后组内可能残留孙进程 (如在途 hermes chat)，
        wait() 收割 leader 后 pgid 就查不到了，所以先取 pgid、
        leader 退出后再对组内残留补一刀 SIGKILL。

        2026-08-21 修复: claude (deepseek API 请求中) 可能忽略 SIGINT,
        run.sh 等 claude 退出一直阻塞 → trap/cleanup 不执行 → 8s 后 SIGKILL
        全组 → .cc_session 丢失 → 第二轮无法 resume 原会话。
        现在 SIGINT 后先 SIGKILL 组内 claude 子进程 (让 run.sh 的 claude
        返回 → trap 执行 cleanup 提取 session), 再等 run.sh 退出。
        """
        proc = handle.proc
        if proc is None:
            return
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError):
            pgid = None

        self._signal_group(proc, signal.SIGINT)
        # 2026-08-21 修复 (核心): claude 在 deepseek API 请求中忽略 SIGINT,
        # run.sh 的 bash 等 claude (前台管道或 wait) 不结束 → 不执行 trap →
        # 8s 后 SIGKILL 全组 → cleanup 丢失 → .cc_session 空 → 第二轮无法 resume。
        # 方案: SIGINT 后立即 SIGKILL 组内 claude/codex 进程 (让 run.sh 的
        # claude 返回 → bash 走失败分支 → EXIT → cleanup 提取 session 写
        # .cc_session), 然后等 run.sh 正常退出。
        if pgid is not None:
            try:
                for p in _iter_group(pgid):
                    cmd = " ".join(p.cmdline()) if hasattr(p, "cmdline") else ""
                    if ("claude" in cmd or "codex" in cmd) and p.pid != proc.pid:
                        try:
                            os.kill(p.pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            pass
            except Exception:
                pass
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


def _iter_group(pgid: int):
    """遍历进程组内的进程 (读 /proc/*/stat 的 pgrp 字段, 无 psutil 依赖)。"""
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                stat_path = Path("/proc") / entry / "stat"
                with open(stat_path, "r") as f:
                    stat_text = f.read()
                # stat 格式: pid (comm) state ppid pgrp session ...
                # comm 可能含空格/括号, 用最后一个 ')' 之后的部分
                rparen = stat_text.rfind(")")
                fields = stat_text[rparen + 1:].split()
                if len(fields) >= 2 and fields[1] == str(pgid):
                    yield _ProcInfo(int(entry))
            except (OSError, ValueError):
                continue
    except OSError:
        return


class _ProcInfo:
    """轻量进程信息 (cmdline 读取失败时返回空)。"""

    def __init__(self, pid: int):
        self.pid = pid

    def cmdline(self) -> list:
        try:
            return (Path("/proc") / str(self.pid) / "cmdline") \
                .read_bytes().split(b"\x00")[:-1] or []
        except OSError:
            return []


def _read_claude_env(snapshot_dir: Optional[Path] = None) -> dict:
    """读 claude 引擎容器注入用的 env 段 (ANTHROPIC_BASE_URL / AUTH_TOKEN / MODEL 等)。

    优先读快照 claude/settings.json (比赛网关切换 switch-api.sh gateway 只改快照,
    宿主 ~/.claude 保持官方); 快照无 claude 配置时回退宿主。
    """
    candidates = []
    if snapshot_dir is not None:
        candidates.append(Path(snapshot_dir) / "claude" / "settings.json")
    candidates.append(Path.home() / ".claude" / "settings.json")
    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            env = data.get("env", {})
            env = {k: v for k, v in env.items() if isinstance(v, str)}
            if env:
                return env
        except Exception:
            continue
    return {}


def _detect_host_proxy() -> Optional[str]:
    """
    探测宿主机代理，返回容器可用的代理 URL (如 http://host.docker.internal:7892)。

    优先级: 环境变量 PROXY_FOR_CONTAINERS > 常见代理端口探测。
    codex 在容器里访问 OpenAI 需要走宿主机代理 (账号登录 + 本地网络环境)。
    """
    explicit = os.environ.get("PROXY_FOR_CONTAINERS", "").strip()
    if explicit:
        return explicit

    # 常见代理端口 (Clash/v2ray 系; 7897 = Windows 宿主 clash 默认)
    ports = ["7890", "7892", "1087", "7897"]

    # 端口在宿主机 127.0.0.1 上监听才算有效；返回给容器用的地址则是
    # host.docker.internal (Docker Desktop 将其映射到宿主机)
    import socket as _socket

    seen = []
    for port in dict.fromkeys(ports):  # 去重保序
        try:
            with _socket.create_connection(("127.0.0.1", int(port)), timeout=1):
                seen.append(port)
        except OSError:
            continue
    if not seen:
        return None
    host = os.environ.get("CONTAINER_HOST_GATEWAY", "host.docker.internal")
    return f"http://{host}:{seen[0]}"


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

    def __init__(self, image: str = "ctf-solver:latest", snapshot_dir: Optional[Path] = None, agent_cli: str = "codex"):
        self.image = image
        self.agent_cli = agent_cli  # codex | claude (claude 引擎: deepseek anthropic 端点)
        self.proxy_url: Optional[str] = None   # 宿主代理 (惰性探测)
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
        """与 run.sh 命名规则一致 (run.sh 优先用 challenge-id 哈希)。"""
        digest = hashlib.md5(ch.id.encode()).hexdigest()[:12]
        return CHALLENGES_DIR / f"manual_{ch.type}_{digest}"  # bind mount 下与容器内同名

    # ─── 生命周期 ───

    def start(self, ch: Challenge) -> SolverHandle:
        work_dir = self._predict_work_dir(ch)
        cname = f"solver-{_safe_name(ch.id)}-{int(time.time())}"

        cmd = [
            "docker", "run", "-d", "--name", cname,
            "--user", "1000:1000",  # 对齐宿主 stw uid: work_dir 文件宿主可删
            "-v", f"{CHALLENGES_DIR}:{self.CONTAINER_ROOT}/challenges",
            "-v", f"{self.snapshot_dir}/codex:/home/ubuntu/.codex",
            "-v", f"{self.snapshot_dir}/hermes:/home/ubuntu/.hermes",
            "--memory", "4g",
            "-e", f"AGENT_CLI={self.agent_cli}",
        ]
        # claude 引擎: 容器内 claude 读 deepseek anthropic 端点环境变量
        # (优先快照 claude/settings.json = 当前模式端点; 无快照回退宿主)
        if self.agent_cli == "claude":
            claude_env = _read_claude_env(self.snapshot_dir)
            for k, v in claude_env.items():
                cmd += ["-e", f"{k}={v}"]
        # codex 访问 OpenAI 走宿主机代理 (探测一次并缓存)
        if self.proxy_url is None:
            self.proxy_url = _detect_host_proxy()
        if self.proxy_url:
            for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                cmd += ["-e", f"{var}={self.proxy_url}"]
            # VPN/内网段直连 (靶机在 10.x VPN 网内，必须绕过代理)
            no_proxy = "localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
            cmd += ["-e", f"NO_PROXY={no_proxy}", "-e", f"no_proxy={no_proxy}"]
        cmd += [
            self.image,
            # 镜像 ENTRYPOINT 已 exec run.sh，这里只传 run.sh 参数
            "--type", ch.type,
            "--challenge-id", ch.id,  # run.sh 用 id 哈希命名 work_dir (url 会被平台复用)
        ]
        if ch.type in ("web", "binary"):
            cmd += ["--url", ch.url or ""]
            if ch.type == "binary":
                attach = self._container_attachment(ch)
                if attach:
                    cmd += ["--attachment", str(attach)]  # 可选制品
        else:
            cmd += ["--attachment", str(self._container_attachment(ch))]
        cmd += ["--hint", (ch.description or "").strip() or "(无)"]
        fc = int(getattr(ch, "flag_count", 1) or 1)
        if fc > 1:
            cmd += ["--flag-count", str(fc)]  # 多 flag: solver 拿满前不退出
        # 轮转断点: 上一圈 session id 传给 run.sh → claude --resume 恢复会话
        sid = getattr(ch, "cc_session_id", None)
        if sid:
            cmd += ["--resume-session", sid]

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
