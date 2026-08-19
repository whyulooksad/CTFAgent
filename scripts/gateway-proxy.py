#!/usr/bin/env python3
"""gateway-proxy.py -- DASCTF 比赛网关本地转发代理

背景: 平台「大模型 API 配置」的网关 URL 是原始 URL 的完整代理
  (POST <网关URL> 本身 = 一次调用, 实测 anthropic/responses 均 200),
  而 claude/hermes/codex 的 SDK 会拼路径 (anthropic 拼 /v1/messages,
  codex 拼 /responses) -> 网关URL/v1/messages -> 404。

本代理监听本地端口, 剥离 SDK 拼的路径, 转发到网关完整 URL:
  POST /v1/messages -> POST <anthropic 网关 URL>
  POST /responses  -> POST <responses 网关 URL>
其余路径 404。SSE 流式响应 chunked 转发。

用法:
  python3 gateway-proxy.py [port]      # 默认 8765, 前台运行
  或 gateway-proxy.py --bg             # 后台运行 (nohup, 写日志到同目录)

配置指向 (网关快照):
  claude  ANTHROPIC_BASE_URL = http://127.0.0.1:8765
  hermes  base_url = http://127.0.0.1:8765  (api_mode=anthropic_messages)
  codex   base_url = "http://127.0.0.1:8765"

转发目标可用环境变量覆盖 (默认 DASCTF 比赛网关):
  GW_ANTHROPIC_URL  (默认 https://llm-gateway.dasctf.com/llm-gateway/proxy/e/adHBctoNwQbmLUvp)
  GW_RESPONSES_URL  (默认 https://llm-gateway.dasctf.com/llm-gateway/proxy/e/lFfnjnPhYeLWKnl7)
"""

import http.client
import http.server
import os
import sys
import urllib.parse

DEFAULT_ANTHROPIC = "https://llm-gateway.dasctf.com/llm-gateway/proxy/e/adHBctoNwQbmLUvp"
DEFAULT_RESPONSES = "https://llm-gateway.dasctf.com/llm-gateway/proxy/e/lFfnjnPhYeLWKnl7"
DEFAULT_PORT = 8765
UPSTREAM_TIMEOUT = 900  # 解题工具调用周期可能几分钟

GW_ANTHROPIC = os.environ.get("GW_ANTHROPIC_URL", DEFAULT_ANTHROPIC)
GW_RESPONSES = os.environ.get("GW_RESPONSES_URL", DEFAULT_RESPONSES)

# 路径 -> 上游完整 URL
ROUTES = {
    "/v1/messages": GW_ANTHROPIC,
    "/responses": GW_RESPONSES,
}


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        # 忽略 query string: claude SDK 请求 /v1/messages?beta=true (beta 参数),
        # 精确匹配 self.path 会 404 (实测 claude 报 model issue 的根因)
        path = self.path.split("?")[0]
        upstream = ROUTES.get(path)
        if upstream is None:
            self.send_error(404, f"only POST /v1/messages | /responses supported, got {self.path}")
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""

        u = urllib.parse.urlparse(upstream)
        try:
            conn = http.client.HTTPSConnection(u.hostname, u.port, timeout=UPSTREAM_TIMEOUT)
            hdrs = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in ("host", "content-length", "connection", "transfer-encoding")
            }
            conn.request("POST", u.path, body=body, headers=hdrs)
            resp = conn.getresponse()
        except Exception as e:  # noqa: BLE001
            self.send_error(502, f"upstream error: {e}")
            return

        # 透传响应头 (去 hop-by-hop, chunked 流式转发)
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except BrokenPipeError:
            pass
        finally:
            conn.close()

    def log_message(self, fmt, *args):
        if "-v" in sys.argv:
            sys.stderr.write("[proxy] " + (fmt % args) + "\n")


def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), ProxyHandler)
    print(f"[gateway-proxy] 监听 http://127.0.0.1:{port}")
    print(f"[gateway-proxy]   /v1/messages -> {GW_ANTHROPIC}")
    print(f"[gateway-proxy]   /responses  -> {GW_RESPONSES}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[gateway-proxy] 已停止")


if __name__ == "__main__":
    if "--bg" in sys.argv:
        import subprocess
        import tempfile
        # 日志写 /tmp: 容器内 ubuntu(1000) 对 /opt/ctf-agent 无写权限 (root 属主),
        # 写脚本目录会 PermissionError 导致代理起不来 (实测容器内 8765 无监听)
        log = os.path.join(tempfile.gettempdir(), "gateway-proxy.log")
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), *[a for a in sys.argv if a != "--bg"]],
            stdout=open(log, "a"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(f"[gateway-proxy] 后台启动, 日志: {log}")
        sys.exit(0)
    main()
