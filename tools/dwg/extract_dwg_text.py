"""
精确提取 DWG 内的中文文本片段
"""
from pathlib import Path
import re

f = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dwg")
with open(f, 'rb') as fp:
    data = fp.read()

# 1. 找 UTF-8 中文（更可能是真的文字）
print("=" * 70)
print("UTF-8 中文连续串（每串 > 4 字）")
print("=" * 70)
chinese_utf8 = re.compile(rb'(?:[\xe4-\xe9][\x80-\xbf][\x80-\xbf]){2,}')
matches = list(chinese_utf8.finditer(data))
print(f"  总匹配数: {len(matches)}")
seen = set()
for m in matches:
    try:
        s = m.group().decode('utf-8', errors='replace')
        if s not in seen and len(s) > 1:
            seen.add(s)
            print(f"    @{m.start():>10}: {s}")
    except:
        pass

# 2. 找 GBK 中文（ANSI_936 编码）
print()
print("=" * 70)
print("GBK 中文连续串（每串 > 4 字）")
print("=" * 70)
gbk_pattern = re.compile(rb'(?:[\x81-\xfe][\x40-\xfe]){2,}')
matches = list(gbk_pattern.finditer(data))
print(f"  总匹配数: {len(matches)}")
seen = set()
for m in matches:
    try:
        s = m.group().decode('gbk', errors='replace')
        # 过滤掉全空的串
        if s.strip() and s not in seen:
            seen.add(s)
            if len(s) > 1:
                print(f"    @{m.start():>10}: {s}")
    except:
        pass
