#!/usr/bin/env python3
import collections
import csv
import re
import socket
import struct
import sys
import urllib.parse

PCAP = sys.argv[1] if len(sys.argv) > 1 else "111.pcap"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/blind_requests.csv"

REQ_RE = re.compile(
    rb"GET\s+(/comments\.php\?name=if\(\(substr\(\(select\(text\)from"
    rb"\(wfy_comments\)where\(id=100\)\),(\d+),1\)=(%22.*?%22)\),100,0\))"
)


def ipstr(b):
    return socket.inet_ntoa(b)


with open(PCAP, "rb") as f:
    gh = f.read(24)
    magic = gh[:4]
    if magic == b"\xd4\xc3\xb2\xa1":
        endian, scale = "<", 1e-6
    elif magic == b"\xa1\xb2\xc3\xd4":
        endian, scale = ">", 1e-6
    elif magic == b"\x4d\x3c\xb2\xa1":
        endian, scale = "<", 1e-9
    elif magic == b"\xa1\xb2\x3c\x4d":
        endian, scale = ">", 1e-9
    else:
        raise SystemExit("unsupported pcap magic")
    network = struct.unpack(endian + "I", gh[20:24])[0]
    packets = []
    idx = 0
    while True:
        rh = f.read(16)
        if not rh:
            break
        sec, frac, caplen, wirelen = struct.unpack(endian + "IIII", rh)
        raw = f.read(caplen)
        idx += 1
        # This capture is DLT_NULL (0): a four-byte loopback address-family
        # prefix precedes IPv4. Also tolerate RAW and Ethernet inputs.
        off = 4 if network == 0 else (0 if network in (12, 101) else 14)
        if len(raw) < off + 20 or raw[off] >> 4 != 4:
            continue
        ihl = (raw[off] & 0x0f) * 4
        if len(raw) < off + ihl or raw[off + 9] != 6:
            continue
        total = struct.unpack("!H", raw[off + 2:off + 4])[0]
        src, dst = ipstr(raw[off + 12:off + 16]), ipstr(raw[off + 16:off + 20])
        toff = off + ihl
        if len(raw) < toff + 20:
            continue
        sport, dport, seq, ack = struct.unpack("!HHII", raw[toff:toff + 12])
        doff = (raw[toff + 12] >> 4) * 4
        flags = raw[toff + 13]
        plen = max(0, min(len(raw), off + total) - (toff + doff))
        payload = raw[toff + doff:toff + doff + plen]
        packets.append({
            "i": idx, "t": sec + frac * scale, "src": src, "dst": dst,
            "sp": sport, "dp": dport, "seq": seq, "ack": ack,
            "flags": flags, "payload": payload, "wirelen": wirelen,
        })

# Discover each request directly from captured payload. Requests fit in one segment here.
requests = []
for p in packets:
    m = REQ_RE.search(p["payload"])
    if not m:
        continue
    pos = int(m.group(2))
    enc = m.group(3).decode("ascii", "replace")
    val = urllib.parse.unquote(enc)
    if len(val) >= 2 and val[0] == val[-1] == '"':
        val = val[1:-1]
    requests.append({
        **p, "pos": pos, "cand": val, "enc": enc,
        "flow": (p["src"], p["sp"], p["dst"], p["dp"]),
    })

# Index packets by normalized bidirectional TCP connection.
by_conn = collections.defaultdict(list)
def connkey(p):
    a, b = (p["src"], p["sp"]), (p["dst"], p["dp"])
    return tuple(sorted((a, b)))
for p in packets:
    by_conn[connkey(p)].append(p)

rows = []
for n, r in enumerate(requests):
    ps = by_conn[connkey(r)]
    later = [p for p in ps if p["i"] >= r["i"]]
    rev = [p for p in later if p["src"] == r["dst"] and p["sp"] == r["dp"]]
    srv_data = [p for p in rev if p["payload"]]
    endings = [p for p in later if p["flags"] & 0x05]  # FIN or RST
    first = min((p["t"] for p in srv_data), default=None)
    end = max((p["t"] for p in later), default=r["t"])
    close = min((p["t"] for p in endings), default=None)
    body = b"".join(p["payload"] for p in srv_data)
    status = re.search(rb"HTTP/1\.[01]\s+(\d+)", body)
    clen = re.search(rb"Content-Length:\s*(\d+)", body, re.I)
    rows.append({
        "n": n + 1, "packet": r["i"], "time": f'{r["t"]:.6f}',
        "src": r["src"], "sport": r["sp"], "dst": r["dst"], "dport": r["dp"],
        "pos": r["pos"], "cand": r["cand"], "encoded": r["enc"],
        "to_first_data": "" if first is None else f"{first-r['t']:.6f}",
        "to_last_packet": f"{end-r['t']:.6f}",
        "to_close": "" if close is None else f"{close-r['t']:.6f}",
        "server_payload_bytes": sum(len(p["payload"]) for p in srv_data),
        "server_data_packets": len(srv_data),
        "conn_packets_after": len(later),
        "fin": int(any(p["flags"] & 1 for p in later)),
        "rst": int(any(p["flags"] & 4 for p in later)),
        "status": status.group(1).decode() if status else "",
        "content_length": clen.group(1).decode() if clen else "",
        "response_text": body.decode("utf-8", "replace").replace("\r", "\\r").replace("\n", "\\n"),
    })

with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["n"])
    w.writeheader()
    w.writerows(rows)

print(f"network={network} tcp_packets={len(packets)} requests={len(rows)} output={OUT}")
print("endpoints:", collections.Counter((r["src"], r["dst"], r["dp"]) for r in requests))
positions = collections.defaultdict(list)
for r in rows:
    positions[r["pos"]].append(r)
for pos in sorted(positions):
    rr = positions[pos]
    timings = [(x["cand"], x["to_first_data"], x["to_last_packet"],
                x["server_payload_bytes"], x["rst"]) for x in rr]
    print(f"pos={pos:3d} count={len(rr):2d} first={rr[0]['cand']!r} last={rr[-1]['cand']!r} "
          f"next_gap={(float(positions[pos+1][0]['time'])-float(rr[-1]['time'])) if pos+1 in positions else 0:.6f} "
          f"tail={timings[-3:]}")
