"""专门调试 动模水套镶件_3001"""
import re
INNER_SINGLE = re.compile(r"(?:^|_)(\d{4,5})")
s = "动模水套镶件_3001"
m = INNER_SINGLE.search(s)
print(f"原: {s!r}")
print(f"匹配: {m}")
if m:
    print(f"  group(1): {m.group(1)!r}")
    print(f"  start: {m.start()}, end: {m.end()}")
    print(f"  desc_part = s[:{m.start()}] + s[{m.end()}:] = {s[:m.start()]!r} + {s[m.end():]!r}")
    print(f"  = {s[:m.start()] + s[m.end():]!r}")
