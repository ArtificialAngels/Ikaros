"""
完整提取 DXF 中的：
1. 所有表格（TABLE 实体）
2. 所有文字（MTEXT/TEXT）
3. 所有 OLE 嵌入对象（OLE2FRAME，通常是 Excel 表格）
4. 所有 INSERT 块插入
5. 提取 MTEXT 中的"内嵌表格"格式
"""
import ezdxf
from pathlib import Path
from collections import Counter
import struct

f = Path(r'E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dxf')
print(f"文件: {f.name}  ({f.stat().st_size/1024/1024:.2f} MB)")

doc = ezdxf.readfile(str(f))
msp = doc.modelspace()
entities = list(msp)

# 1. 找 ACAD_TABLE 实体（真正的 dwg 表格）
print("\n" + "=" * 70)
print("1. ACAD_TABLE 实体（真正的 dwg 表格）")
print("=" * 70)
tables = [e for e in entities if e.dxftype() == 'ACAD_TABLE' or e.dxftype() == 'TABLE']
print(f"  ACAD_TABLE 数: {len(tables)}")
for t in tables:
    try:
        print(f"    - 行数={t.dxf.get('row_count', '?')} 列数={t.dxf.get('col_count', '?')}")
    except:
        pass

# 2. MTEXT 中的"假表格" - 用 \\p (paragraph break) 和 \\t (tab) 的格式
print("\n" + "=" * 70)
print("2. MTEXT 多行文字分析（看是否含表格格式 \\p \\t）")
print("=" * 70)
mtexts = [e for e in entities if e.dxftype() == 'MTEXT']
print(f"  MTEXT 总数: {len(mtexts)}")
# 找含 \\p 或 \\t 格式控制符的
pseudo_tables = []
for m in mtexts:
    try:
        text = m.text
        if '\\p' in text or '\\t' in text or text.count(';') > 5:
            pseudo_tables.append((m, text))
    except:
        pass
print(f"  含表格格式控制符的: {len(pseudo_tables)}")
for m, text in pseudo_tables[:5]:
    layer = m.dxf.layer
    print(f"\n    [图层={layer}] 字符数={len(text)}")
    # 显示带格式的预览
    preview = text[:200].replace('\n', '\\n')
    print(f"    预览: {preview}")

# 3. TEXT 实体
print("\n" + "=" * 70)
print("3. TEXT 单行文字")
print("=" * 70)
texts = [e for e in entities if e.dxftype() == 'TEXT']
print(f"  TEXT 总数: {len(texts)}")
# 抽几个有中文的
import re
chinese_pattern = re.compile(r'[\u4e00-\u9fff]')
chinese_texts = [e for e in texts if chinese_pattern.search(e.dxf.text)]
print(f"  含中文的: {len(chinese_texts)}")
for t in chinese_texts[:10]:
    print(f"    [图层={t.dxf.layer}] {t.dxf.text[:60]}")

# 4. OLE2FRAME - 嵌入的 Excel/OLE 对象
print("\n" + "=" * 70)
print("4. OLE2FRAME 嵌入对象（通常是 Excel 表格）")
print("=" * 70)
ole_frames = [e for e in entities if e.dxftype() == 'OLE2FRAME']
print(f"  OLE2FRAME 总数: {len(ole_frames)}")
for i, ole in enumerate(ole_frames):
    layer = ole.dxf.layer
    print(f"\n  [{i+1}] 图层={layer}")
    print(f"      类型: {type(ole).__name__}")
    print(f"      DXF 属性:")
    for attr in ole.dxf.all_existing_dxf_keys() if hasattr(ole.dxf, 'all_existing_dxf_keys') else dir(ole.dxf):
        if not attr.startswith('_'):
            try:
                v = ole.dxf.get(attr)
                if v is not None and v != '' and v != 0:
                    s = str(v)
                    if len(s) > 100:
                        s = s[:100] + "..."
                    print(f"        {attr}: {s}")
            except:
                pass

# 5. INSERT 块插入
print("\n" + "=" * 70)
print("5. INSERT 块插入")
print("=" * 70)
inserts = [e for e in entities if e.dxftype() == 'INSERT']
print(f"  INSERT 总数: {len(inserts)}")
# 按块名统计
block_counter = Counter(i.dxf.name for i in inserts if hasattr(i.dxf, 'name'))
print(f"  使用的不同块: {len(block_counter)}")
for name, n in block_counter.most_common(15):
    print(f"    {name}: {n} 次")
