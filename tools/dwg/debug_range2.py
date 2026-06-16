"""跟踪 6517~6518_定模螺旋冷却柱"""
import re
PREFIX_PATTERN = re.compile(r"^(25-8715)[\s_\-]*", re.IGNORECASE)
RANGE_NUM = re.compile(r"(?:^|_)(\d{4,5})\s*[-~]\s*(\d{4,5})(?:_|$)")

for stem in ["25-8715_6517~6518_定模螺旋冷却柱（已打印）", "25-8715_2021~2023_推杆密封套(已打印)"]:
    s = PREFIX_PATTERN.sub("", stem, count=1)
    print(f"原: {stem!r} -> {s!r}")
    m = RANGE_NUM.search(s)
    if m:
        print(f"  RANGE 匹配: {m.group(0)!r}  start={m.start()}  end={m.end()}")
        print(f"  desc_part = s[:{m.start()}].rstrip('_') = {s[:m.start()].rstrip('_')!r}")
    print()
