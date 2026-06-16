"""跟踪 25-8715_动模水套镶件_3001（已打印）"""
import re
PREFIX_PATTERN = re.compile(r"^(25-8715)[\s_\-]*", re.IGNORECASE)
SINGLE_NUM = re.compile(r"(?:^|_)(\d{4,5})(?:_|$)")
RANGE_NUM = re.compile(r"(?:^|_)(\d{4,5})\s*[-~]\s*(\d{4,5})(?:_|$)")
INNER_RANGE = re.compile(r"(?:^|_)(\d{4,5})\s*[-~]\s*(\d{4,5})")
INNER_SINGLE = re.compile(r"(?:^|_)(\d{4,5})")
HAS_CHINESE = re.compile(r"[\u4e00-\u9fff]")
PRINTED_SUFFIX = re.compile(r"[（(]\s*已打印\s*[)）]?\s*$")

stem = "25-8715_动模水套镶件_3001（已打印）"
print(f"原: {stem!r}")
s = PREFIX_PATTERN.sub("", stem, count=1)
s = PRINTED_SUFFIX.sub("", s).strip()
print(f"处理后: {s!r}")

m = RANGE_NUM.search(s)
print(f"RANGE: {m}")
m2 = SINGLE_NUM.search(s)
print(f"SINGLE: {m2}, start={m2.start() if m2 else None}, end={m2.end() if m2 else None}")
if m2:
    print(f"  desc_part = s[{m2.end()}:] = {s[m2.end():]!r}")
    # 注意：当 single 在中间时，s[m2.end():] = ""，因为 _3001 是末尾
lead = re.search(r"^(\d{4,5})(?:_|$)", s)
print(f"LEAD: {lead}")
print()
# 实际走的是哪条路径？
# RANGE 不匹配 → SINGLE 匹配 `_3001`（在末尾）→ desc_part = s[m2.end():] = ''
# 但 INNER_SINGLE 还没轮到！
