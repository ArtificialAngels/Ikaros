"""
尝试读取 DWG 中的所有实体 - 多种方式
"""
from ezdxf.addons import dwg
from pathlib import Path

f = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dwg")

print(f"文件: {f.name} ({f.stat().st_size/1024/1024:.2f} MB)")
print()

# 1. ezdxf.addons.dwg
print("=" * 70)
print("方法 1: ezdxf.addons.dwg")
print("=" * 70)
doc = dwg.readfile(str(f))
print(f"  EntityDB 长度: {len(doc.entitydb)}")
print(f"  entitydb 内容:")
for e in list(doc.entitydb)[:5]:
    print(f"    {e}")

# 2. 尝试所有可能访问实体的接口
print()
print("=" * 70)
print("方法 2: 各种属性探测")
print("=" * 70)
attrs = [
    'modelspace', 'paperspace', 'layout', 'layout_names', 'layouts',
    'entities', 'query', 'all_entities',
    'block_records', 'blocks', 'layers', 'styles',
    'tables', 'objects', 'image_defs', 'underlay_defs',
    'rootdict', 'appids', 'dimstyles', 'linetypes', 'ucss',
    'views', 'viewports', 'xrec_dicts',
]
for a in attrs:
    try:
        v = getattr(doc, a, None)
        if v is None:
            print(f"  {a}: None")
        else:
            try:
                ln = len(v)
                print(f"  {a}: {type(v).__name__} (len={ln})")
            except TypeError:
                print(f"  {a}: {type(v).__name__}")
    except Exception as e:
        print(f"  {a}: ERROR {e}")

# 3. 探测 entitydb 内部
print()
print("=" * 70)
print("方法 3: EntityDB 内部")
print("=" * 70)
edb = doc.entitydb
print(f"  dir: {[a for a in dir(edb) if not a.startswith('_')][:20]}")
# 试一些方法
for method in ['items', 'values', 'keys', '__iter__', '__len__']:
    try:
        v = getattr(edb, method, None)
        if callable(v):
            r = v()
            print(f"  {method}: {type(r).__name__}, count={len(list(r)) if hasattr(r, '__iter__') else 'N/A'}")
    except Exception as e:
        print(f"  {method}: ERROR {e}")
