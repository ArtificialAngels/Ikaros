"""
深度探测 DWG 文件 - 试图读取所有可能的信息
"""
from ezdxf.addons import dwg
from pathlib import Path

f = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dwg")

print(f"文件: {f.name}")
print(f"大小: {f.stat().st_size:,} 字节 ({f.stat().st_size/1024/1024:.2f} MB)")
print()

doc = dwg.readfile(str(f))

# 1. 所有 HEADER 变量
hdr = doc.header
all_keys = list(hdr.varnames())
print(f"=== 全部 HEADER 变量 ({len(all_keys)} 个) ===")
non_empty = 0
for k in all_keys:
    try:
        v = hdr.get(k)
        if v is not None and v != "" and v != 0 and v != (0, 0, 0):
            non_empty += 1
            s = str(v)
            if len(s) > 80:
                s = s[:80] + "..."
            print(f"  {k} = {s}")
    except Exception as e:
        pass
print(f"\n非空变量: {non_empty} / {len(all_keys)}")

# 2. 探测 doc 全部非 None 属性
print()
print("=== doc 内部状态 ===")
for attr in dir(doc):
    if attr.startswith('_') or callable(getattr(doc, attr, None)):
        continue
    try:
        v = getattr(doc, attr)
        if v is not None and v != "" and v != [] and v != {}:
            tname = type(v).__name__
            extra = ""
            if hasattr(v, '__len__'):
                extra = f" (len={len(v)})"
            print(f"  {attr}: {tname}{extra} = {str(v)[:80]}")
    except Exception:
        pass

# 3. 试 entitydb
print()
print("=== EntityDB ===")
edb = doc.entitydb
print(f"  type: {type(edb)}")
print(f"  attrs: {[a for a in dir(edb) if not a.startswith('_')][:15]}")
try:
    print(f"  len: {len(edb)}")
except:
    pass

# 4. 探测 stored_sections
print()
print(f"=== stored_sections ===")
print(f"  {doc.stored_sections}")
