"""诊断：为什么描述空了"""
import re

PREFIX_PATTERN = re.compile(r"^(25-8715)[\s_\-]*", re.IGNORECASE)
SINGLE_NUM = re.compile(r"(?:^|_)(\d{4,5})(?:_|$)")
RANGE_NUM = re.compile(r"(?:^|_)(\d{4,5})\s*[-~]\s*(\d{4,5})(?:_|$)")
INNER_RANGE = re.compile(r"(?:^|_)(\d{4,5})\s*[-~]\s*(\d{4,5})")
INNER_SINGLE = re.compile(r"(?:^|_)(\d{4,5})")
HAS_CHINESE = re.compile(r"[\u4e00-\u9fff]")
PRINTED_SUFFIX = re.compile(r"[（(]\s*已打印\s*[)）]?\s*$")

tests = [
    "25-8715_1022_M20堵塞（已打印）",
    "25-8715_6517~6518_定模螺旋冷却柱（已打印）",
    "25-8715_推杆编号布局",
    "25-8715_水箱油箱及其固定件（已打印）",
]

for stem in tests:
    print(f"原: {stem!r}")
    s = PREFIX_PATTERN.sub("", stem, count=1)
    s = PRINTED_SUFFIX.sub("", s).strip()
    print(f"  处理后: {s!r}")

    m = RANGE_NUM.search(s)
    if m:
        print(f"  → RANGE: {m.group(1)}-{m.group(2)}  desc={s[:m.start()]!r}")
        continue
    m2 = SINGLE_NUM.search(s)
    if m2:
        print(f"  → SINGLE: {m2.group(1)}  desc={s[:m2.start()]!r}  end={s[m2.end()-1:]!r}")
        continue
    lead = re.search(r"^(\d{4,5})(?:_|$)", s)
    if lead:
        print(f"  → LEAD: {lead.group(1)}  desc={s[lead.end():]!r}")
        continue
    print(f"  → 无编号")
    print()
