#!/usr/bin/env python3
"""
branch.py -- Subagent daemon + CLI for CTF parallel probing.

Architecture:
  - daemon (长驻进程): 管理 Codex subagent 生命周期，unix socket 通信
  - CLI (thin client): Codex 主进程调用，通过 socket 发 JSON 请求

Usage:
  # 启动 daemon (run.sh 自动拉起)
  python3 branch.py daemon --work-dir <dir>

  # 子命令 (Codex 调用)
  python3 branch.py spawn   --work-dir <dir> --name "..." --prompt "..." [--timeout 300]
  python3 branch.py status  --work-dir <dir>
  python3 branch.py kill    --work-dir <dir> <id>
  python3 branch.py results --work-dir <dir> [id]
  python3 branch.py wait    --work-dir <dir> [id] [--timeout 60]
  python3 branch.py shutdown --work-dir <dir>
  python3 branch.py socket-path --work-dir <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import signal
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

CODEX_CMD = os.environ.get("CODEX_CMD", "codex")
DEFAULT_TIMEOUT = 900  # 单个 subagent 默认 15 分钟，xhigh 推理与大型源码审计需要更多时间
SOCKET_BACKLOG = 8
SELECT_TIMEOUT = 1.0  # select 轮询间隔 (秒)
RECV_BUF = 1 << 20  # 1MB
SOCKET_DIR_ENV = "CTF_AGENT_SOCKET_DIR"


def branch_socket_path(work_dir: Path) -> Path:
    """为工作目录生成稳定、短小的 Unix socket 路径。"""
    digest = hashlib.sha256(os.fsencode(work_dir.resolve())).hexdigest()[:20]
    configured_dir = os.environ.get(SOCKET_DIR_ENV)
    if configured_dir:
        socket_dir = Path(configured_dir).expanduser()
        if not socket_dir.is_absolute():
            raise ValueError(f"{SOCKET_DIR_ENV} must be an absolute path")
    else:
        socket_dir = Path("/tmp") / f"ctf-agent-{os.getuid()}"
    return socket_dir / f"branch-{digest}.sock"


def ensure_socket_dir(sock_path: Path) -> None:
    """创建当前用户专属的 socket 目录，并拒绝不安全的已有路径。"""
    socket_dir = sock_path.parent
    socket_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if socket_dir.is_symlink():
        raise RuntimeError(f"socket directory must not be a symlink: {socket_dir}")
    info = socket_dir.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError(f"socket path parent is not a directory: {socket_dir}")
    if info.st_uid != os.getuid():
        raise PermissionError(f"socket directory is not owned by current user: {socket_dir}")
    socket_dir.chmod(0o700)


# ───────────────────────── Data Models ─────────────────────────


@dataclass
class Subagent:
    """单个 subagent 的状态记录。"""

    id: str
    name: str
    pid: int
    started_at: float
    timeout: int
    status: str = "running"  # running | done | timeout | killed | crashed
    exit_code: Optional[int] = None
    result_file: str = ""
    finished_at: Optional[float] = None

    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return end - self.started_at

    def to_dict(self) -> dict:
        return asdict(self)


# ───────────────────────── Daemon ─────────────────────────


class BranchDaemon:
    """长驻进程，管理所有 Codex subagent。"""

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.sock_path = branch_socket_path(work_dir)
        self.state_path = work_dir / "branch_state.json"
        self.subagents: dict[str, Subagent] = {}
        self._counter = 0
        self._running = True

    # ─── lifecycle ───

    def run(self) -> None:
        """daemon 主循环。"""
        self._setup_signals()
        self._restore_state()
        self._bind_socket()
        print(f"[branch-daemon] listening on {self.sock_path}", flush=True)

        while self._running:
            readable, _, _ = select.select(
                [self._srv], [], [], SELECT_TIMEOUT
            )
            if readable:
                self._accept_and_handle()
            self._reap_subagents()
            self._check_timeouts()
            self._persist_state()

        self._shutdown_all()
        self._srv.close()
        if self.sock_path.exists():
            self.sock_path.unlink()
        print("[branch-daemon] exited cleanly", flush=True)

    # ─── internal: setup ───

    def _setup_signals(self) -> None:
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

    def _on_signal(self, signum, frame) -> None:
        print(f"[branch-daemon] received signal {signum}, shutting down...", flush=True)
        self._running = False

    def _bind_socket(self) -> None:
        ensure_socket_dir(self.sock_path)
        if self.sock_path.exists():
            self.sock_path.unlink()
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(str(self.sock_path))
        self._srv.listen(SOCKET_BACKLOG)
        self._srv.setblocking(False)

    # ─── internal: state persistence ───

    def _persist_state(self) -> None:
        data = {sid: sa.to_dict() for sid, sa in self.subagents.items()}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(self.state_path)  # atomic

    def _restore_state(self) -> None:
        if not self.state_path.exists():
            return
        data = json.loads(self.state_path.read_text())
        for sid, sa_dict in data.items():
            sa = Subagent(**sa_dict)
            # daemon 重启后检查 running 的进程是否还活着
            if sa.status == "running":
                if not self._pid_alive(sa.pid):
                    sa.status = "killed"
                    sa.finished_at = time.time()
            self.subagents[sid] = sa
            self._counter = max(self._counter, int(sid.split("_")[1]))
        if self.subagents:
            print(
                f"[branch-daemon] restored {len(self.subagents)} subagents from state",
                flush=True,
            )

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    # ─── internal: subagent management ───

    def _reap_subagents(self) -> None:
        """非阻塞回收已结束的子进程。"""
        for sa in self.subagents.values():
            if sa.status != "running":
                continue
            try:
                waited_pid, status = os.waitpid(sa.pid, os.WNOHANG)
            except ChildProcessError:
                sa.status = "killed"
                sa.finished_at = time.time()
                continue
            if waited_pid == 0:
                continue  # still running
            if os.WIFEXITED(status):
                sa.exit_code = os.WEXITSTATUS(status)
                sa.status = "done" if sa.exit_code == 0 else "crashed"
            elif os.WIFSIGNALED(status):
                sa.status = "killed"
                sa.exit_code = -os.WTERMSIG(status)
            sa.finished_at = time.time()

    def _check_timeouts(self) -> None:
        """检查超时，主动 SIGTERM。"""
        now = time.time()
        for sa in self.subagents.values():
            if sa.status != "running":
                continue
            if now - sa.started_at > sa.timeout:
                self._terminate(sa, status="timeout")

    def _terminate(self, sa: Subagent, status: str) -> None:
        """终止 subagent 进程组并更新状态。"""
        try:
            os.killpg(os.getpgid(sa.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        sa.status = status
        sa.finished_at = time.time()

    def _shutdown_all(self) -> None:
        for sa in self.subagents.values():
            if sa.status == "running":
                self._terminate(sa, status="killed")
        self._persist_state()

    # ─── internal: request handling ───

    def _accept_and_handle(self) -> None:
        try:
            conn, _ = self._srv.accept()
        except BlockingIOError:
            return
        try:
            raw = conn.recv(RECV_BUF)
            if not raw:
                return
            req = json.loads(raw)
            resp = self._dispatch(req)
        except json.JSONDecodeError:
            resp = {"error": "invalid JSON"}
        except Exception as e:
            resp = {"error": str(e)}
        conn.sendall(json.dumps(resp, ensure_ascii=False).encode())
        conn.close()

    def _dispatch(self, req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd == "spawn":
            return self._cmd_spawn(req)
        elif cmd == "status":
            return self._cmd_status()
        elif cmd == "kill":
            return self._cmd_kill(req)
        elif cmd == "results":
            return self._cmd_results(req)
        elif cmd == "wait":
            return self._cmd_wait(req)
        elif cmd == "shutdown":
            self._running = False
            return {"status": "shutting down"}
        else:
            return {"error": f"unknown command: {cmd}"}

    # ─── commands ───

    def _cmd_spawn(self, req: dict) -> dict:
        name = req["name"]
        prompt = req["prompt"]
        timeout = req.get("timeout", DEFAULT_TIMEOUT)

        self._counter += 1
        sid = f"branch_{self._counter:03d}"
        result_file = f"branch_result_{sid}.md"
        log_file = self.work_dir / f"{sid}.log"

        full_prompt = self._build_prompt(prompt, name, sid, result_file)

        try:
            proc = subprocess.Popen(
                [
                    CODEX_CMD,
                    "exec",
                    "--profile",
                    "ctf",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "--dangerously-bypass-hook-trust",
                    "--ignore-rules",
                    "--disable",
                    "guardian_approval",
                    "-c",
                    "model_reasoning_effort=xhigh",
                    full_prompt,
                ],
                stdout=open(log_file, "w"),
                stderr=subprocess.STDOUT,
                cwd=str(self.work_dir),
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError:
            return {"error": f"codex command not found: {CODEX_CMD}"}

        sa = Subagent(
            id=sid,
            name=name,
            pid=proc.pid,
            started_at=time.time(),
            timeout=timeout,
            result_file=result_file,
        )
        self.subagents[sid] = sa
        print(f"[branch-daemon] spawned {sid} (pid={proc.pid}): {name}", flush=True)
        return {"id": sid, "pid": proc.pid}

    def _build_prompt(
        self, prompt: str, name: str, sid: str, result_file: str
    ) -> str:
        return (
            f"{prompt}\n\n"
            f"协作隔离规则：你是只读试探分支。不要修改 board.md、progress.md、guidance.md、dead_ends.md，"
            f"也不要创建或修改主线文件；只允许在完成时写入指定的结果文件。\n\n"
            f"---\n"
            f"完成后将结果写入 {self.work_dir}/{result_file}，格式:\n"
            f"## Branch Result\n"
            f"direction: {name}\n"
            f"subagent_id: {sid}\n"
            f"status: FEASIBLE | INFEASIBLE\n"
            f"### 发现\n"
            f"(列出关键发现)\n"
            f"### 命令和结果\n"
            f"(关键命令及其输出)\n"
            f"### 结论\n"
            f"(可行/不可行 + 建议)"
        )

    def _cmd_status(self) -> dict:
        now = time.time()
        items = []
        for sa in self.subagents.values():
            item = {"id": sa.id, "name": sa.name, "status": sa.status}
            if sa.status == "running":
                item["elapsed"] = f"{int(now - sa.started_at)}s"
                item["timeout"] = f"{sa.timeout}s"
            else:
                item["exit_code"] = sa.exit_code
                item["result"] = sa.result_file if sa.result_file else None
            items.append(item)
        return {"subagents": items}

    def _cmd_kill(self, req: dict) -> dict:
        sid = req.get("id")
        sa = self.subagents.get(sid)
        if not sa:
            return {"error": f"unknown id: {sid}"}
        if sa.status == "running":
            self._terminate(sa, status="killed")
        return {"id": sid, "status": sa.status}

    def _cmd_results(self, req: dict) -> dict:
        sid = req.get("id")
        if sid:
            sa = self.subagents.get(sid)
            if not sa:
                return {"error": f"unknown id: {sid}"}
            path = self.work_dir / sa.result_file
            return {
                "id": sid,
                "status": sa.status,
                "content": path.read_text() if path.exists() else None,
            }
        # 返回所有已完成的结果摘要
        results = []
        for sa in self.subagents.values():
            if sa.status in ("done", "timeout", "killed", "crashed"):
                path = self.work_dir / sa.result_file
                results.append(
                    {
                        "id": sa.id,
                        "name": sa.name,
                        "status": sa.status,
                        "has_result": path.exists(),
                    }
                )
        return {"results": results}

    def _cmd_wait(self, req: dict) -> dict:
        sid = req.get("id")
        timeout = req.get("timeout", 60)
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._reap_subagents()
            if sid:
                sa = self.subagents.get(sid)
                if sa and sa.status != "running":
                    return {"id": sid, "status": sa.status}
            else:
                if all(sa.status != "running" for sa in self.subagents.values()):
                    return {"status": "all_done"}
            time.sleep(1)
        return {"status": "timeout"}


# ───────────────────────── CLI Client ─────────────────────────


class BranchClient:
    """Thin client，连接 daemon socket 发请求。"""

    def __init__(self, work_dir: Path):
        self.sock_path = branch_socket_path(work_dir)

    def call(self, req: dict) -> dict:
        if not self.sock_path.exists():
            return {"error": "daemon not running (socket not found)"}
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(120)  # wait 命令可能阻塞较久
            sock.connect(str(self.sock_path))
            sock.sendall(json.dumps(req, ensure_ascii=False).encode())
            raw = sock.recv(RECV_BUF)
            return json.loads(raw)
        except (ConnectionRefusedError, socket.timeout) as e:
            return {"error": str(e)}
        finally:
            sock.close()


# ───────────────────────── CLI ─────────────────────────


def _print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cli_main(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir).resolve()
    client = BranchClient(work_dir)

    if args.command == "spawn":
        resp = client.call(
            {
                "cmd": "spawn",
                "name": args.name,
                "prompt": args.prompt,
                "timeout": args.timeout,
            }
        )
    elif args.command == "status":
        resp = client.call({"cmd": "status"})
    elif args.command == "kill":
        resp = client.call({"cmd": "kill", "id": args.id})
    elif args.command == "results":
        resp = client.call({"cmd": "results", "id": args.id})
    elif args.command == "wait":
        resp = client.call(
            {"cmd": "wait", "id": args.id, "timeout": args.timeout}
        )
    elif args.command == "shutdown":
        resp = client.call({"cmd": "shutdown"})
    else:
        print(f"unknown command: {args.command}")
        return 1

    _print_json(resp)
    return 0 if "error" not in resp else 1


def daemon_main(args: argparse.Namespace) -> int:
    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    daemon = BranchDaemon(work_dir)
    daemon.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Subagent daemon + CLI for CTF parallel probing"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # daemon
    p_daemon = sub.add_parser("daemon", help="start the daemon")
    p_daemon.add_argument("--work-dir", required=True)

    # socket-path
    p_socket_path = sub.add_parser("socket-path", help="print daemon socket path")
    p_socket_path.add_argument("--work-dir", required=True)

    # spawn
    p_spawn = sub.add_parser("spawn", help="spawn a subagent")
    p_spawn.add_argument("--work-dir", required=True)
    p_spawn.add_argument("--name", required=True, help="direction name")
    p_spawn.add_argument("--prompt", required=True, help="prompt for the subagent")
    p_spawn.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)

    # status
    p_status = sub.add_parser("status", help="show all subagent status")
    p_status.add_argument("--work-dir", required=True)

    # kill
    p_kill = sub.add_parser("kill", help="kill a subagent")
    p_kill.add_argument("--work-dir", required=True)
    p_kill.add_argument("id", help="subagent id")

    # results
    p_results = sub.add_parser("results", help="read subagent results")
    p_results.add_argument("--work-dir", required=True)
    p_results.add_argument("id", nargs="?", default=None, help="specific id (optional)")

    # wait
    p_wait = sub.add_parser("wait", help="wait for subagent(s) to finish")
    p_wait.add_argument("--work-dir", required=True)
    p_wait.add_argument("id", nargs="?", default=None, help="specific id (optional)")
    p_wait.add_argument("--timeout", type=int, default=60)

    # shutdown
    p_shutdown = sub.add_parser("shutdown", help="shutdown daemon")
    p_shutdown.add_argument("--work-dir", required=True)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "socket-path":
        print(branch_socket_path(Path(args.work_dir)))
        return 0
    if args.command == "daemon":
        return daemon_main(args)
    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())
