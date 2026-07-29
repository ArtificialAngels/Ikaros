"""
用 ezdxf 的低级 FileHeader 解析 DWG 结构
"""
from ezdxf.addons.dwg.fileheader import FileHeader
from ezdxf.addons.dwg.const import ACAD_2000
from pathlib import Path

f = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dwg")

with open(f, 'rb') as fp:
    data = fp.read()

# FileHeader 需要 R2004+ 用 sentinel 格式，R2000 用另一种
# 试用 FileHeader
print("=== FileHeader 解析尝试 ===")
# 看可用的方法
for name in ['from_stream', 'read', 'parse', 'load', 'from_bytes', 'read_dwg', 'parse_r2000']:
    if hasattr(FileHeader, name):
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}")
print(f"  FileHeader mro: {[c.__name__ for c in FileHeader.__mro__]}")

# 找 header_section
from ezdxf.addons.dwg import header_section as hs
print(f"\n=== header_section ===")
print(f"  Module dir: {[a for a in dir(hs) if not a.startswith('_')]}")
# 找具体类
for name in dir(hs):
    obj = getattr(hs, name)
    if isinstance(obj, type):
        print(f"  class: {name}")

# 直接试 load_header_section
from ezdxf.addons.dwg import loader
try:
    header = loader.load_header_section(ACAD_2000, data, 0)
    print(f"\n=== load_header_section ===")
    print(f"  type: {type(header)}")
    print(f"  result: {header}")
except Exception as e:
    print(f"\nload_header_section 错误: {e}")
