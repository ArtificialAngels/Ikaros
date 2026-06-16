"""对真实文件名做解析调试"""
import re

PREFIX_PATTERN = re.compile(r"^(25-8716)[\s_\-]*", re.IGNORECASE)
SINGLE_NUM = re.compile(r"_(\d{4,5})$")
RANGE_NUM = re.compile(r"_(\d{4,5})\s*[-~]\s*(\d{4,5})$")
INNER_RANGE = re.compile(r"_(\d{4,5})\s*[-~]\s*(\d{4,5})")
INNER_SINGLE = re.compile(r"_(\d{4,5})")
HAS_CHINESE = re.compile(r"[\u4e00-\u9fff]")
PRINTED_SUFFIX = re.compile(r"[（(]\s*已打印\s*[)）]?\s*$")

# 真实测试 - 从实际目录取
real = [
    "25-8716_总装图(已打印)",
    "25-8716_6652_定模螺旋冷却柱（已打印）",
    "25-8716_3000_动模镶块(已打印)",
    "25-8716_3002-3007_动模镶件（已打印）",
    "25-8716_1028_动模拼块定位键（已打印)",  # 缺右括号
]

for stem in real:
    print(f"原: {stem!r}")
    s = PREFIX_PATTERN.sub("", stem, count=1)
    print(f"  去前缀: {s!r}")
    s2 = PRINTED_SUFFIX.sub("", s).strip()
    print(f"  去'已打印': {s2!r}")

    m = RANGE_NUM.search(s2)
    if m:
        print(f"  → RANGE: {m.group(1)}-{m.group(2)}, 描述={s2[:m.start()]!r}")
        continue
    m2 = SINGLE_NUM.search(s2)
    if m2:
        print(f"  → SINGLE: {m2.group(1)}, 描述={s2[:m2.start()]!r}")
        continue
    print(f"  → 无编号")
    print()
