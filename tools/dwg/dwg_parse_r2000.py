"""
直接用 ezdxf 的低级 loader 加载 TABLES/ENTITIES 段
"""
from ezdxf.addons.dwg import loader
from pathlib import Path
import struct

f = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dwg")

# 直接读 R2000 文件头
with open(f, 'rb') as fp:
    data = fp.read()

# R2000 文件头（参考 OpenDesign 规范）
# 偏移 0-5: 版本字符串 (AC1015)
# 偏移 6: 5 字节维护版本 + unknown
# 偏移 11: 1 字节（unknown 0x14=20）
# 偏移 12: 1 字节 unknown 0x01
# 偏移 13: 2 字节 unknown 0xdc 0x00
# 偏移 15: 1 字节 code page (0x01 = ANSI_1252, 0x1e = ANSI_936)
# 偏移 16: 4 字节 Section locators count
# 偏移 20-...: Section locators

# 读取 R2000 文件头（具体解析）
print("R2000 文件头结构:")
print(f"  Version: {data[:6]}")
print(f"  字节 6-10: {data[6:11].hex()}")
print(f"  字节 11: 0x{data[11]:02x} (={data[11]})")
print(f"  字节 12: 0x{data[12]:02x} (={data[12]})")
print(f"  字节 13-14: 0x{data[13]:02x}{data[14]:02x}")

# 检查 R2000 是否压缩
# 偏移 15 是 code page
print(f"  字节 15: 0x{data[15]:02x}")
# 0x15 (21) = ANSI_936
# 0x01 (1) = ANSI_1252
# 0x1e (30) = ANSI_936
if data[15] in [0x15, 0x1e]:
    print(f"    -> 是 ANSI_936 (中文 GBK)")
else:
    print(f"    -> 是其它 code page")

# Section locators 数量（小端 4 字节）
print()
sec_count = struct.unpack_from('<I', data, 16)[0]
print(f"  Section locators 数量: {sec_count}")

# 段号 (R2000 固定 10 个段)
# 0: HEADER
# 1: CLASSES
# 2: OBJECT MAP (R2004+)
# 3: UNKNOWN 0 (R2004+)
# 4: TEMPLATE (R2004+)
# 5: ACDS (R2013+)
# 6: UNKNOWN 1 (R2013+)
# 但 R2000 的顺序: HEADER, CLASSES, OBJECT_MAP?, PADDING?, ...
# 实际上 R2000: 0=HEADER, 1=CLASSES, 2=OBJECT_MAP

# Section locators start at offset 20
# Each locator: 1 byte section number, 4 bytes address, 4 bytes size
loc_start = 20
for i in range(min(sec_count, 10)):
    off = loc_start + i * 9
    if off + 9 > len(data):
        break
    sec_num = data[off]
    addr = struct.unpack_from('<I', data, off + 1)[0]
    size = struct.unpack_from('<I', data, off + 5)[0]
    sec_names = {0: 'HEADER', 1: 'CLASSES', 2: 'OBJECT_MAP'}
    name = sec_names.get(sec_num, f'SEC_{sec_num}')
    print(f"  Section {sec_num:>2} ({name:<12}): addr=0x{addr:08X}  size={size:,}  (end=0x{addr+size:08X})")

# 找到 AC10 0x95 哨兵
print()
print("查找哨兵 (0x95 0xA0 0x4F ...)...")
# R2000 头部还有 0x95 哨兵
print(f"  偏移 0x15 (21): {data[21:30].hex()}")
print(f"  偏移 0x40 (64): {data[64:80].hex()}")

# 看看 HEADER 段起始
# Section 0 通常是 HEADER
for i in range(min(sec_count, 10)):
    off = loc_start + i * 9
    sec_num = data[off]
    if sec_num == 0:
        addr = struct.unpack_from('<I', data, off + 1)[0]
        size = struct.unpack_from('<I', data, off + 5)[0]
        print()
        print(f"=== HEADER 段 (offset 0x{addr:X}, size {size}) ===")
        print(f"  前 64 字节: {data[addr:addr+64].hex()}")
        print(f"  ASCII 视图: {data[addr:addr+200]}")
        break
