#!/usr/bin/env python3
"""
adapters/mock.py -- 本地 mock 平台 (开发/测试主力)。

内置 3 道假题 (master-agent-spec.md §9)，附件按需生成到 tests/mock_challenges/:
  mock-easy-misc   misc    zip 内 flag_b64.txt = base64(flag)      100 分 / 200 解
  mock-mid-crypto  crypto  zip 内 task.py + output.bin (XOR)      300 分 /  50 解
  mock-mid-web     web     本地 http.server 登录页注释里藏 flag    400 分 /  30 解

分数/解出数静态设计成能验证优先级排序 (base = 0.5*ease + 0.5*value):
  easy-misc 0.625 > mid-web 0.575 > mid-crypto 0.500
"""

from __future__ import annotations

import base64
import http.server
import os
import shutil
import tempfile
import threading
import zipfile
from itertools import cycle
from pathlib import Path

from .base import Challenge, PlatformAdapter, SubmitResult

# web 靶机的监听地址与返回给 solver 的主机名。
# DockerBackend 场景 (solver 在容器里) 需设 CTF_MOCK_PUBLIC_HOST:
#   Docker Desktop: host.docker.internal
#   WSL 原生 docker: 172.17.0.1 (docker0 网关，容器经它访问 WSL 宿主)
# (真实平台靶机是公网 URL，无此问题)
_BIND_HOST = "0.0.0.0"
_PUBLIC_HOST = os.environ.get("CTF_MOCK_PUBLIC_HOST", "127.0.0.1")

REPO_DIR = Path(__file__).resolve().parent.parent.parent   # 仓库根
ATTACH_DIR = REPO_DIR / "tests" / "mock_challenges"

MOCK_FLAGS = {
    "mock-easy-misc": "flag{mock_easy_misc_welcome}",
    "mock-mid-crypto": "flag{mock_xor_is_easy}",
    "mock-mid-web": "flag{mock_web_hidden}",
}

CRYPTO_KEY = b"MOCKXORKEY"

MOCK_HINTS = {
    "mock-easy-misc": "试试 base64 解码",
    "mock-mid-crypto": "task.py 里有 key",
    "mock-mid-web": "看看页面源码",
}

WEB_INDEX = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Member Login</title></head>
<body>
  <h1>Member Login</h1>
  <form method="post" action="/login">
    <input name="user" placeholder="username">
    <input type="password" name="pass" placeholder="password">
    <button type="submit">Login</button>
  </form>
  <!-- TODO: remove before production deploy
       debug flag: flag{mock_web_hidden}
  -->
  <p>&copy; 2026 MockCorp. All rights reserved.</p>
</body>
</html>
"""


def _build_attachments() -> None:
    """按需生成 mock 附件 (确定性内容，存在即跳过)。"""
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)

    # 1) misc 签到: zip 内 flag_b64.txt = base64(flag)
    misc_zip = ATTACH_DIR / "easy_misc.zip"
    if not misc_zip.exists():
        encoded = base64.b64encode(MOCK_FLAGS["mock-easy-misc"].encode()).decode()
        with zipfile.ZipFile(misc_zip, "w") as z:
            z.writestr("flag_b64.txt", encoded + "\n")

    # 2) crypto: task.py (含 key) + output.bin = xor(flag, key)
    crypto_zip = ATTACH_DIR / "mid_crypto.zip"
    if not crypto_zip.exists():
        flag = MOCK_FLAGS["mock-mid-crypto"].encode()
        ct = bytes(a ^ b for a, b in zip(flag, cycle(CRYPTO_KEY)))
        task_py = (
            "# task.py -- 加密脚本 (flag.txt 未提供，需从 output.bin 恢复)\n"
            "from itertools import cycle\n\n"
            f"key = {CRYPTO_KEY!r}\n"
            "flag = open('flag.txt', 'rb').read().strip()\n"
            "ct = bytes(a ^ b for a, b in zip(flag, cycle(key)))\n"
            "open('output.bin', 'wb').write(ct)\n"
        )
        with zipfile.ZipFile(crypto_zip, "w") as z:
            z.writestr("task.py", task_py)
            z.writestr("output.bin", ct)


def _make_handler(docroot: Path):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(docroot), **kwargs)

        def log_message(self, fmt, *args) -> None:  # 静默
            pass

    return Handler


class MockAdapter(PlatformAdapter):
    """mock 平台。web 题 start_challenge 拉起本地 http.server 模拟靶机。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._servers: dict[str, tuple[http.server.ThreadingHTTPServer, Path]] = {}
        _build_attachments()

    # ─── 题目 ───

    def list_challenges(self) -> list[Challenge]:
        return [
            Challenge(
                id="mock-easy-misc",
                title="签到题-base64",
                type="misc",
                score=100,
                solve_count=200,
                description="misc 签到题。附件是一个压缩包，解压后看看文件内容是什么。",
                attachment_url="mock://easy_misc.zip",
            ),
            Challenge(
                id="mock-mid-crypto",
                title="简单异或",
                type="crypto",
                score=300,
                solve_count=50,
                description="crypto 题。给了加密脚本和输出文件，恢复明文拿到 flag。",
                attachment_url="mock://mid_crypto.zip",
            ),
            Challenge(
                id="mock-mid-web",
                title="藏在页面里的秘密",
                type="web",
                score=400,
                solve_count=30,
                description="web 题。一个登录页面，flag 就藏在某个不起眼的地方。",
            ),
        ]

    # ─── 靶机 ───

    def start_challenge(self, cid: str) -> str:
        with self._lock:
            if cid in self._servers:  # 已在运行
                srv = self._servers[cid][0]
                return f"http://{_PUBLIC_HOST}:{srv.server_address[1]}"
        if cid != "mock-mid-web":
            return ""  # 非 web 题 / 未知题无靶机

        docroot = Path(tempfile.mkdtemp(prefix="mock-web-"))
        (docroot / "index.html").write_text(WEB_INDEX, encoding="utf-8")
        srv = http.server.ThreadingHTTPServer((_BIND_HOST, 0), _make_handler(docroot))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with self._lock:
            self._servers[cid] = (srv, docroot)
        return f"http://{_PUBLIC_HOST}:{srv.server_address[1]}"

    def stop_challenge(self, cid: str) -> None:
        with self._lock:
            item = self._servers.pop(cid, None)
        if item:
            srv, docroot = item
            srv.shutdown()
            srv.server_close()
            shutil.rmtree(docroot, ignore_errors=True)

    # ─── 提交 / 提示 / 附件 ───

    def submit(self, cid: str, flag: str) -> SubmitResult:
        expected = MOCK_FLAGS.get(cid)
        if expected is None:
            return SubmitResult("error", f"unknown challenge: {cid}")
        if flag.strip() == expected:
            return SubmitResult("correct", "mock 平台判定正确")
        return SubmitResult("wrong", "flag 不正确")

    def get_hint(self, cid: str) -> str:
        return MOCK_HINTS.get(cid, "")

    def download_attachment(self, url: str, dest_dir: Path) -> Path:
        name = url.split("/")[-1]
        src = ATTACH_DIR / name
        if not src.exists():
            raise FileNotFoundError(f"mock attachment not found: {src}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        shutil.copy2(src, dest)
        return dest
