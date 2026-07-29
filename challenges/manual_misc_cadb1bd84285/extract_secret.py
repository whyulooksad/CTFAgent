#!/usr/bin/env python3
"""Extract secret from blind SQLi pcap - analyze last char per position."""
import re

with open('111.pcap', 'rb') as f:
    raw = f.read()

# Extract all strings containing substr pattern
text = raw.decode('latin-1')
pattern = r'substr\(\(select\(text\)from\(wfy_comments\)where\(id=100\)\),(\d+),1\)=%22(.)%22'
matches = re.findall(pattern, text)

print(f"Total SQLi requests found: {len(matches)}")

from collections import defaultdict
pos_chars = defaultdict(list)
for pos_str, char in matches:
    pos = int(pos_str)
    pos_chars[pos].append(char)

# Method 1: last char per position (attacker stops after finding correct char)
result_last = ''
for pos in sorted(pos_chars.keys()):
    result_last += pos_chars[pos][-1]

print(f"\nMethod 1 (last char per position): {result_last}")
print(f"Length: {len(result_last)}")

# Show details for first 10 positions
print("\nDetails (first 10 positions):")
for pos in sorted(pos_chars.keys())[:10]:
    chars = pos_chars[pos]
    print(f"  pos {pos:2d}: {len(chars):2d} chars tested: {''.join(chars)} -> last: {chars[-1]}")
