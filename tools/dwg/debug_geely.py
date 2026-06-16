"""调试脚本"""
import re
ROOT_TESTS = [
    "25-8716_6652_定模螺旋冷却柱（已打印）",
    "25-8716_7452_S4滑块1_4转M16水管（已打印）",
    "25-8716_3000_动模镶块(已打印)",
    "25-8716_动定模水箱油箱及其固定件（已打印）",
    "25-8716_直运水管（已打印）",
    "25-8716_总装图(已打印)",
    "25-8716_1028_动模拼块定位键（已打印)",  # 缺失右括号
    "25-8716_2021~2023_推杆密封套(已打印)",  # 波浪号
    "25-8716_3002-3007_动模镶件（已打印）",  # 范围
    "25-8716_7121-7127_S1下滑块型芯（已打印）",  # 范围
]

PREFIX_PATTERN = re.compile(r"^(25-8716)[\s_\-]*", re.IGNORECASE)
SINGLE_NUM = re.compile(r"_(\d{4,5})$")
RANGE_NUM = re.compile(r"_(\d{4,5})\s*[-~]\s*(\d{4,5})$")
INNER_RANGE = re.compile(r"_(\d{4,5})\s*[-~]\s*(\d{4,5})")
INNER_SINGLE = re.compile(r"_(\d{4,5})")
HAS_CHINESE = re.compile(r"[\u4e00-\u9fff]")
PRINTED_SUFFIX = re.compile(r"[（(]\s*已打印\s*[)）]?\s*$")  # 容错：右括号可选

for stem in ROOT_TESTS:
    s = PREFIX_PATTERN.sub("", stem, count=1)
    print(f"原: {stem}")
    print(f"  去前缀: {s}")

    m = RANGE_NUM.search(s)
    if m:
        print(f"  RANGE_NUM 匹配: {m.group(1)}-{m.group(2)}")
        print(f"  desc_part: {s[:m.start()]}")
    else:
        m2 = SINGLE_NUM.search(s)
        if m2:
            print(f"  SINGLE_NUM 匹配: {m2.group(1)}")
            print(f"  desc_part: {s[:m2.start()]}")
        else:
            m3 = INNER_RANGE.search(s)
            if m3:
                print(f"  INNER_RANGE 匹配: {m3.group(1)}-{m3.group(2)}")
            else:
                m4 = INNER_SINGLE.search(s)
                if m4:
                    print(f"  INNER_SINGLE 匹配: {m4.group(1)}")
                    print(f"  desc_part: {s.replace(m4.group(0), '')}")
                else:
                    print(f"  无编号")
    print()
