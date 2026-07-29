#!/usr/bin/env python3
"""Deep analysis of blind SQLi pcap - verify flag reconstruction."""
import re
from collections import defaultdict

with open('111.pcap', 'rb') as f:
    raw = f.read()

text = raw.decode('latin-1')
pattern = r'substr\(\(select\(text\)from\(wfy_comments\)where\(id=100\)\),(\d+),1\)=%22(.)%22'
matches = re.findall(pattern, text)

print(f"Total SQLi requests: {len(matches)}")

pos_chars = defaultdict(list)
for pos_str, char in matches:
    pos = int(pos_str)
    pos_chars[pos].append(char)

# Show details for ALL positions
print("\nAll positions:")
result = ''
for pos in sorted(pos_chars.keys()):
    chars = pos_chars[pos]
    charset = ''.join(chars)
    last = chars[-1]
    count = len(chars)
    result += last
    # Flag suspicious positions (all 26 lowercase = special char not in charset)
    flag = ""
    if count == 26 and all(c.islower() for c in chars):
        flag = " <-- ALL 26 lowercase, likely special char ({, }, etc.)"
    if count <= 10 and last == '0':
        flag = " <-- few chars, likely end of string"
    print(f"  pos {pos:2d}: {count:2d} chars: {charset:40s} -> {last}{flag}")

print(f"\nRaw reconstruction: {result}")

# Smart reconstruction: replace positions where all 26 lowercase were tested
# with { or } based on position
smart = list(result)
brace_positions = []
for pos in sorted(pos_chars.keys()):
    chars = pos_chars[pos]
    if len(chars) == 26 and all(c.islower() for c in chars):
        brace_positions.append(pos)

print(f"\nPositions with all 26 lowercase (special chars): {brace_positions}")
print(f"String length (max position): {max(pos_chars.keys())}")

# The flag is likely flag{...} where { is at position 5 and } at the last brace position
if len(brace_positions) >= 2:
    open_brace = brace_positions[0]   # should be position 5
    close_brace = brace_positions[-1] # should be the closing brace
    smart[open_brace - 1] = '{'
    smart[close_brace - 1] = '}'
    # Truncate anything after close brace
    smart_str = ''.join(smart[:close_brace])
    print(f"\nSmart reconstruction: {smart_str}")
    print(f"Length: {len(smart_str)}")
