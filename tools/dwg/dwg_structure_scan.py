"""
尝试用 ezdxf 解压 DWG 数据段
"""
from ezdxf.addons.dwg import loader, header_section
from pathlib import Path
import struct

f = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dwg")

with open(f, 'rb') as fp:
    data = fp.read()

print(f"文件大小: {len(data):,} 字节")
print(f"文件头: {data[:6]}")

# 看一下 ezdxf 的 loader 怎么用
print()
print("=== ezdxf.addons.dwg.loader ===")
print(dir(loader))
print()

# 看 FileHeader
print("=== FileHeader ===")
print(dir(header_section))
print()

# 直接看 DWG 文件结构（R2000 格式）
# 头部: AC1015 + 5 bytes + 各种 section 偏移
# R2000 DWG 头部结构（OdaFileConverter 文档）
# offset 0: 6 bytes 版本字符串 (AC1015)
# offset 6: 5 bytes 维护版本等
# offset 11: 1 byte 图像字节数 (1)
# offset 12: 1 byte unknown
# offset 13: 1 byte codepage (ANSI_936=44? but 前面说 ANSI_936)
# offset 13: 4 bytes 维护版本 (0x14 = 20)
# offset 17: 1 byte 0
# offset 18: 4 bytes 搜索地址 (seek address for 0x95)
# offset 22: 0x95 byte
# offset 23: ...
# 实际上 R2000 DWG 头很复杂

# 简化: 找 "ENTITIES" 段标记
print("=" * 70)
print("扫描 DWG 段标记")
print("=" * 70)
# 找特定字符串（应该是 ASCII 出现）
import re
for marker in [b'ENTITIES', b'BLOCKS', b'LAYER', b'TABLES', b'OBJECTS', b'CLASSES', b'TU', b'AC10']:
    positions = []
    pos = 0
    while True:
        p = data.find(marker, pos)
        if p < 0:
            break
        positions.append(p)
        pos = p + 1
    print(f"  {marker.decode()}: 出现 {len(positions)} 次, 前 5 个位置: {positions[:5]}")

# 找文件头中"0x95"标记 (R13/R14/R2000 标志)
# 文件头偏移 13 字节的字节
print()
print(f"字节 11: 0x{data[11]:02x}")
print(f"字节 12: 0x{data[12]:02x}")
print(f"字节 13: 0x{data[13]:02x}")
print(f"字节 14: 0x{data[14]:02x}")

# 找 0x95 在头部的位置
print()
print("找 0x95 magic")
pos = 0
positions = []
for i in range(min(100, len(data))):
    if data[i] == 0x95:
        positions.append(i)
        if len(positions) > 5:
            break
print(f"  前 10 个 0x95 位置: {positions}")
