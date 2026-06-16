"""
诊断脚本：检查为什么编号列是空的
"""
import re

PROJECT_PREFIXES = [
    r"25-8777",
    r"25-8763",
    r"225-8777",
    r"L250864-C1411\s*\(25-8777\)",
]
PREFIX_PATTERN = re.compile(
    r"^(" + "|".join(PROJECT_PREFIXES) + r")[\s_\-]*",
    re.IGNORECASE
)
# 修正：_XXXX.dwg 模式，XXXX 必须是数字
NUM_SUFFIX_PATTERN = re.compile(r"_(\d{3,5})(?=\.[^.]+$)", re.IGNORECASE)

# 测试一些文件名
tests = [
    "225-8777_1568_STD_LQZ_B_定模螺旋冷却柱_1568",
    "25-8777_1550_STD_WATER_A_定模集水箱A_1550",
    "25-8777-M20_PLUG_M20吊环孔堵塞_2519",
    "25-8777_3028_S1_INSERT_B_S1下滑块镶件3028",
    "25-8777_3101_S1_CORE_PIN_S1滑块型芯3101_3101",
    "25-8777_25-8777_S3_CORE_HOLDER_4515_小型芯顶柱_4515",
    "25-8777_S6_CORE_COVER_S6挤压连接杆B_3202",
    "25-8777_S11挤压小型芯顶柱_6218",
    "L250864-C1411 (25-8777)配板确认图",
    "ge266_2.3an(tac2)压铸模具_预铸销布局图",
    "25-8763_S7散件(已打印)",
    "25-8777_1208_S5_CY_HOLDER_S5连接杆_1208",
]

for t in tests:
    s = PREFIX_PATTERN.sub("", t, count=1)
    m = NUM_SUFFIX_PATTERN.search(s)
    number = m.group(1) if m else "(无)"
    # 进一步看：去前缀后字符串
    print(f"原: {t}")
    print(f"  去前缀: {s}")
    print(f"  编号: {number}")
    print()
