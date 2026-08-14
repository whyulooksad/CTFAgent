#!/usr/bin/env python3
"""
master_dashboard.py -- Master 总览面板后端 (HTTP + SSE, 纯 stdlib)。

由 master.py 在主循环启动前拉起 (dashboard_port > 0 时)，与 Master 同进程，
直接持有 Master 引用读状态 / 发控制命令，不引入跨进程通信。

- GET  /                          总览页 (master_dashboard.html)
- GET  /api/overview              全部题目状态 + 槽位/得分汇总
- GET  /api/logs/<cid>/<which>    SSE: 该题 work_dir 的 codex.log / hermes.log 增量
- POST /api/pause | /api/resume   暂停/恢复调度 (不发新题，运行中的不动)
- POST /api/stop-solver           手动终止某 solver {"cid": ...}
- POST /api/config                运行时改 max_solvers / max_challenges
"""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Optional
from urllib.parse import unquote, urlparse

from challenge_state import SUBMITTED_CORRECT

SCRIPT_DIR = Path(__file__).resolve().parent          # master/
FRONTEND_FILE = SCRIPT_DIR / "master_dashboard.html"


# ───────────────────────── 数据构建 ─────────────────────────


def tail_file(path: Path, offset: int) -> tuple[str, int]:
    """读取文件从 offset 到末尾，返回 (增量, 新 offset)。"""
    if not path.exists():
        return "", offset
    size = path.stat().st_size
    if size < offset:
        offset = 0  # 文件被重置 (run.sh 重写日志)
    if size == offset:
        return "", offset
    with open(path, "r", errors="replace") as f:
        f.seek(offset)
        content = f.read()
    return content, size


def build_overview(master) -> dict:
    """汇总全部题目状态 (线程安全: 只走 MasterState 的加锁接口)。"""
    now = time.time()
    records = master.state.all_records()
    challenges = []
    for r in records:
        item = {
            "id": r.id,
            "title": r.title,
            "type": r.type,
            "score": r.score,
            "solve_count": r.solve_count,
            "status": r.status,
            "attempts": r.attempts,
            "flag": r.flag,
            "last_submit_status": r.last_submit_status,
            "error": r.error,
            "is_running": r.id in master.running,
        }
        if r.id in master.running and r.started_at:
            item["elapsed"] = int(now - r.started_at)
        if r.work_dir:
            item["has_logs"] = (Path(r.work_dir) / "codex.log").exists()
        challenges.append(item)

    # 运行中在前，其余按状态字母序
    challenges.sort(key=lambda c: (not c["is_running"], c["status"], c["id"]))

    solved = [r for r in records if r.status == SUBMITTED_CORRECT]
    return {
        "paused": master.paused,
        "running": len(master.running),
        "max_solvers": master.cfg.max_solvers,
        "attempted": master.state.distinct_attempted(),
        "max_challenges": master.cfg.max_challenges,
        "solved": len(solved),
        "score_earned": sum(r.score for r in solved),
        "challenges": challenges,
    }


# ───────────────────────── HTTP Handler ─────────────────────────


def _make_handler(master):
    class DashboardHandler(BaseHTTPRequestHandler):

        def log_message(self, fmt, *args) -> None:
            pass  # 静默

        # ── GET ──

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._serve_frontend()
            elif path == "/api/overview":
                self._json(HTTPStatus.OK, build_overview(master))
            elif path.startswith("/api/logs/"):
                self._handle_sse(path)
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        # ── POST ──

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return

            if path == "/api/pause":
                master.pause()
                self._json(HTTPStatus.OK, {"paused": True})
            elif path == "/api/resume":
                master.resume()
                self._json(HTTPStatus.OK, {"paused": False})
            elif path == "/api/stop-solver":
                ok = master.stop_solver(data.get("cid", ""))
                self._json(HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND,
                           {"stopped": ok})
            elif path == "/api/config":
                try:
                    master.update_config(
                        max_solvers=data.get("max_solvers"),
                        max_challenges=data.get("max_challenges"),
                    )
                except (TypeError, ValueError) as e:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                    return
                self._json(HTTPStatus.OK, {
                    "max_solvers": master.cfg.max_solvers,
                    "max_challenges": master.cfg.max_challenges,
                })
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
                self._json(HTTPStatus.NOT_FOUND, {"error": "master_dashboard.html not found"})

        def _handle_sse(self, path: str) -> None:
            # /api/logs/<cid>/<codex|hermes>
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[3] not in ("codex", "hermes"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "bad log path"})
                return
            cid = unquote(parts[2])
            which = parts[3]
            rec = master.state.get(cid)
            if rec is None or not rec.work_dir:
                self._json(HTTPStatus.NOT_FOUND, {"error": f"no work dir: {cid}"})
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            offset = 0
            current_work_dir = None
            while True:
                try:
                    # 重试/重分发会换 work_dir，跟随变化并重置 offset
                    r = master.state.get(cid)
                    work_dir = r.work_dir if r else None
                    if not work_dir:
                        break
                    if work_dir != current_work_dir:
                        current_work_dir = work_dir
                        offset = 0
                    content, offset = tail_file(Path(work_dir) / f"{which}.log", offset)
                    if content:
                        payload = "\n".join(f"data: {line}" for line in content.split("\n"))
                        self.wfile.write(f"event: append\n{payload}\n\n".encode())
                        self.wfile.flush()
                    time.sleep(0.5)
                except (BrokenPipeError, ConnectionResetError):
                    break
                except Exception:
                    time.sleep(1.0)

        def _json(self, status: int, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return DashboardHandler


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def start_dashboard(master, port: int) -> tuple[ThreadedHTTPServer, int]:
    """在后台线程启动面板，返回 (server, 实际端口)。port=0 时自动分配。"""
    server = ThreadedHTTPServer(("0.0.0.0", port), _make_handler(master))
    actual_port = server.server_address[1]
    threading.Thread(
        target=server.serve_forever, daemon=True, name="master-dashboard"
    ).start()
    return server, actual_port
