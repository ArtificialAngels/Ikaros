"""快速调试"""
import re
RANGE_NUM = re.compile(r"(?:^|_)(\d{4,5})\s*[-~]\s*(\d{4,5})$")
SINGLE_NUM = re.compile(r"(?:^|_)(\d{4,5})$")
RANGE_ANY = re.compile(r"(?:^|_)(\d{4,5})\s*[-~]\s*(\d{4,5})")

for stem in ["6517~6518_定模螺旋冷却柱", "3002-3007_动模镶件", "2021~2023_推杆密封套", "7121-7127_S1下滑块型芯"]:
    m = RANGE_NUM.search(stem)
    print(f"{stem!r}: RANGE= ", end="")
    if m: print(f"{m.group(1)}-{m.group(2)}")
    else: print("NO", end="; ")
    m2 = SINGLE_NUM.search(stem)
    print(f" SINGLE= ", end="")
    if m2: print(m2.group(1))
    else: print("NO")
