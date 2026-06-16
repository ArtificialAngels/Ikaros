"""
二进制扫描 DWG，查找实体数据
"""
from pathlib import Path
import re

f = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dwg")
print(f"文件: {f.name} ({f.stat().st_size/1024/1024:.2f} MB)")

with open(f, 'rb') as fp:
    data = fp.read()

print(f"读取字节数: {len(data):,}")
print(f"文件头: {data[:6]}")

# 1. 搜索 UTF-8/GBK 编码的中文字符串
print()
print("=" * 70)
print("搜索 UTF-8 中文")
print("=" * 70)
chinese_utf8 = re.compile(rb'[\xe4-\xe9][\x80-\xbf][\x80-\xbf]')
matches = list(chinese_utf8.finditer(data))
print(f"  UTF-8 中文字符数: {len(matches)}")
if matches:
    # 提取连续的字符串
    strings = []
    start = matches[0].start()
    prev = start
    for m in matches:
        if m.start() - prev > 10:  # 间隔 > 10 视为新串
            strings.append((start, prev, data[start:prev+50]))
            start = m.start()
        prev = m.start()
    strings.append((start, prev, data[start:prev+50]))
    print(f"  连续字符串数: {len(strings)}")
    for s, e, content in strings[:30]:
        try:
            s_str = content.decode('utf-8', errors='replace')
            print(f"    @{s:>10}-{e:>10}: {s_str[:60]}")
        except:
            print(f"    @{s:>10}: {content[:50]}")

# 2. 搜索 GBK 中文
print()
print("=" * 70)
print("搜索 GBK 中文 (双字节 0x81-0xFE, 0x40-0xFE)")
print("=" * 70)
gbk_pattern = re.compile(rb'[\x81-\xfe][\x40-\xfe]')
gbk_matches = list(gbk_pattern.finditer(data))
print(f"  GBK 中文字符数: {len(gbk_matches)}")
if gbk_matches:
    # 提取字符串
    strings = []
    start = gbk_matches[0].start()
    prev = start
    for m in gbk_matches:
        if m.start() - prev > 10:
            strings.append((start, prev, data[start:prev+60]))
            start = m.start()
        prev = m.start()
    strings.append((start, prev, data[start:prev+60]))
    print(f"  连续字符串数: {len(strings)}")
    for s, e, content in strings[:30]:
        try:
            s_str = content.decode('gbk', errors='replace')
            print(f"    @{s:>10}-{e:>10}: {s_str[:60]}")
        except:
            print(f"    @{s:>10}: {content[:50]}")

# 3. 统计 ASCII 字符串
print()
print("=" * 70)
print("搜索 ASCII 可打印字符串 (4 字符以上)")
print("=" * 70)
ascii_pattern = re.compile(rb'[\x20-\x7e]{4,}')
ascii_matches = list(ascii_pattern.finditer(data))
print(f"  ASCII 字符串数: {len(ascii_matches)}")
for m in ascii_matches[:30]:
    print(f"    @{m.start():>10}: {m.group()[:60]}")
