#!/usr/bin/env python3
"""
dashboard.py -- CTF Agent 实时监控面板。

HTTP 服务器 + SSE 推流，零第三方依赖。
- GET  /                    -> 前端页面
- GET  /api/status          -> 当前状态 JSON
- GET  /api/logs/codex      -> SSE 流 (codex.log 增量)
- GET  /api/logs/hermes     -> SSE 流 (hermes.log 增量)
- POST /api/start           -> 启动挑战
- POST /api/stop            -> 停止挑战

Usage:
  python3 dashboard.py [--port 8080]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Optional

# ───────────────────────── Config ─────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent          # solver/
FRONTEND_FILE = SCRIPT_DIR / "dashboard.html"
CHALLENGES_DIR = SCRIPT_DIR.parent / "challenges"     # 仓库根/challenges
DEFAULT_PORT = 8080

# ───────────────────────── State ─────────────────────────


class ChallengeState:
    """线程安全的挑战状态。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._work_dir: Optional[Path] = None

    @property
    def work_dir(self) -> Optional[Path]:
        with self._lock:
            return self._work_dir

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self, process: subprocess.Popen, work_dir: Path) -> None:
        with self._lock:
            # 清理残留进程
            if self._process is not None and self._process.poll() is None:
                try:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                except Exception:
                    pass
            self._process = process
            self._work_dir = work_dir

    def stop(self) -> bool:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                return False
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                except Exception:
                    pass
            return True

    def clear(self) -> None:
        with self._lock:
            self._process = None
            self._work_dir = None


STATE = ChallengeState()

# ───────────────────────── Helpers ─────────────────────────


def parse_progress(path: Path) -> dict:
    """解析 progress.md。"""
    if not path.exists():
        return {}
    text = path.read_text(errors="replace")
    result: dict = {"raw": text}

    phase = re.search(r"##\s*Current Phase\s*\n(.+)", text)
    result["phase"] = phase.group(1).strip() if phase else ""

    ns = re.search(r"##\s*Next Steps\s*\n(.*?)(?:\n##|\Z)", text, re.DOTALL)
    result["next_steps"] = ns.group(1).strip() if ns else ""

    flags = re.search(r"##\s*Flags Found\s*\n(.*?)(?:\n##|\Z)", text, re.DOTALL)
    result["flags"] = flags.group(1).strip() if flags else ""

    return result


def get_branch_status(work_dir: Path) -> list:
    """查 branch.py subagent 状态。"""
    try:
        result = subprocess.run(
            ["python3", str(SCRIPT_DIR / "branch.py"), "status", "--work-dir", str(work_dir)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("subagents", [])
    except Exception:
        pass
    return []


def find_latest_challenge() -> Optional[Path]:
    """找最新的挑战工作目录。"""
    if not CHALLENGES_DIR.exists():
        return None
    dirs = sorted(CHALLENGES_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs[0] if dirs else None


def kill_all_ctf_processes() -> list[str]:
    """杀死所有 CTF Agent 相关进程，返回被杀的 PID 列表。"""
    killed = []
    # 先杀当前管理的挑战进程
    if STATE._process is not None:
        try:
            os.killpg(os.getpgid(STATE._process.pid), signal.SIGKILL)
            killed.append(f"run.sh(PID={STATE._process.pid})")
        except Exception:
            pass
    STATE.clear()
    # 再用 pkill 清理所有残留
    patterns = ["run.sh", "branch.py", "monitor.py", "codex exec", "hermes chat"]
    for pat in patterns:
        r = subprocess.run(["pkill", "-f", pat], capture_output=True)
        if r.returncode == 0:
            killed.append(pat)
    return killed


def tail_file(path: Path, offset: int) -> tuple[str, int]:
    """读取文件从 offset 到末尾的内容，返回 (content, new_offset)。"""
    if not path.exists():
        return "", 0
    size = path.stat().st_size
    if size < offset:
        offset = 0  # 文件被重置
    if size == offset:
        return "", offset
    with open(path, "r", errors="replace") as f:
        f.seek(offset)
        content = f.read()
    return content, size


# ───────────────────────── SSE Stream ─────────────────────────


def sse_format(event: str, data: str) -> bytes:
    """格式化 SSE 消息。"""
    lines = data.split("\n")
    payload = "\n".join(f"data: {line}" for line in lines)
    return f"event: {event}\n{payload}\n\n".encode()


# ───────────────────────── Handler ─────────────────────────


class DashboardHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args) -> None:
        pass  # 静默日志

    # ── GET ──

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            self._serve_frontend()
        elif self.path == "/api/status":
            self._handle_status()
        elif self.path == "/api/logs/codex":
            self._handle_sse("codex.log")
        elif self.path == "/api/logs/hermes":
            self._handle_sse("hermes.log")
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    # ── POST ──

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            return

        if self.path == "/api/start":
            self._handle_start(data)
        elif self.path == "/api/stop":
            self._handle_stop()
        elif self.path == "/api/killall":
            self._handle_killall()
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    # ── handlers ──

    def _serve_frontend(self) -> None:
        if FRONTEND_FILE.exists():
            content = FRONTEND_FILE.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "dashboard.html not found"})

    def _handle_status(self) -> None:
        # 没有正在运行的挑战且没有 work_dir -> 返回空状态，不读旧挑战目录
        if not STATE.is_running and not STATE.work_dir:
            self._json(HTTPStatus.OK, {"running": False})
            return

        work_dir = STATE.work_dir or find_latest_challenge()
        if not work_dir or not work_dir.exists():
            self._json(HTTPStatus.OK, {"running": False})
            return

        progress = parse_progress(work_dir / "progress.md")
        subagents = get_branch_status(work_dir) if STATE.is_running else []

        # 检查 round 信息
        round_info = ""
        codex_log = work_dir / "codex.log"
        if codex_log.exists():
            text = codex_log.read_text(errors="replace")
            m = re.search(r"Codex round (\d+)/(\d+)", text)
            if m:
                round_info = f"{m.group(1)}/{m.group(2)}"

        self._json(HTTPStatus.OK, {
            "running": STATE.is_running,
            "work_dir": str(work_dir),
            "phase": progress.get("phase", ""),
            "next_steps": progress.get("next_steps", ""),
            "flags": progress.get("flags", ""),
            "round": round_info,
            "subagents": subagents,
        })

    def _handle_sse(self, log_filename: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        offset = 0
        current_work_dir = None

        while True:
            try:
                # 每次循环重新读 work_dir，处理新挑战启动/切换
                work_dir = STATE.work_dir or find_latest_challenge()
                if not work_dir:
                    time.sleep(1.0)
                    continue

                # work_dir 变了 -> 重置 offset，先发全量
                if work_dir != current_work_dir:
                    current_work_dir = work_dir
                    offset = 0

                log_path = work_dir / log_filename
                content, offset = tail_file(log_path, offset)
                if content:
                    self.wfile.write(sse_format("append", content))
                    self.wfile.flush()
                time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError):
                break
            except Exception:
                time.sleep(1.0)

    def _handle_start(self, data: dict) -> None:
        if STATE.is_running:
            self._json(HTTPStatus.CONFLICT, {"error": "已有挑战在运行"})
            return

        ctype = data.get("type", "web")
        hint = data.get("hint", "")
        url = data.get("url", "")
        attachment = data.get("attachment", "")

        # 构建命令
        cmd = ["bash", str(SCRIPT_DIR / "run.sh"), "--type", ctype]
        if ctype == "web":
            if not url:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "web 类型需要 url"})
                return
            cmd += ["--url", url]
        else:
            if not attachment:
                self._json(HTTPStatus.BAD_REQUEST, {"error": f"{ctype} 类型需要 attachment"})
                return
            cmd += ["--attachment", attachment]
        cmd += ["--hint", hint]

        # 计算工作目录
        if ctype == "web":
            short_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            dir_name = f"manual_web_{short_hash}"
        else:
            short_hash = hashlib.md5(attachment.encode()).hexdigest()[:12]
            dir_name = f"manual_{ctype}_{short_hash}"
        work_dir = CHALLENGES_DIR / dir_name

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,  # 新进程组，方便 kill
        )
        STATE.start(proc, work_dir)

        # 等 2 秒看进程是否秒退（附件不存在/参数错误等）
        time.sleep(2)
        if proc.poll() is not None:
            # 进程已退出，读取输出作为错误信息
            out = proc.stdout.read(4096).decode(errors="replace").strip() if proc.stdout else ""
            STATE.clear()
            # 取最后几行作为错误
            lines = [l for l in out.split("\n") if l.strip()]
            err_msg = lines[-1] if lines else "进程启动后立即退出"
            self._json(HTTPStatus.BAD_REQUEST, {"error": err_msg})
            return

        # 后台线程监控进程退出
        def _watcher():
            proc.wait()
            STATE.clear()

        threading.Thread(target=_watcher, daemon=True).start()

        self._json(HTTPStatus.OK, {"pid": proc.pid, "work_dir": str(work_dir)})

    def _handle_stop(self) -> None:
        if STATE.stop():
            STATE.clear()
            self._json(HTTPStatus.OK, {"stopped": True})
        else:
            self._json(HTTPStatus.OK, {"stopped": False, "reason": "no running process"})

    def _handle_killall(self) -> None:
        """杀死所有 CTF 相关进程。"""
        killed = kill_all_ctf_processes()
        self._json(HTTPStatus.OK, {"killed": killed})

    # ── utils ──

    def _json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ───────────────────────── Server ─────────────────────────


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser(description="CTF Agent Dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    server = ThreadedHTTPServer(("0.0.0.0", args.port), DashboardHandler)
    print(f"[dashboard] http://localhost:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] shutting down")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
