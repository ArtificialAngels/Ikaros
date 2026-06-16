"""
深入提取：
1. TabularNote 块的实体结构（伪表格）
2. 所有 OLE2FRAME 的二进制内容（用 ezdxf 提取 OLE 数据）
3. 表格附近的 MTEXT/TEXT 文字（按位置关联）
4. 按图层聚类的文字
"""
import ezdxf
from pathlib import Path
from collections import Counter, defaultdict
import re

f = Path(r'E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dxf')
print(f"文件: {f.name}  ({f.stat().st_size/1024/1024:.2f} MB)")

doc = ezdxf.readfile(str(f))
msp = doc.modelspace()
entities = list(msp)

# 1. 看 TabularNote 块
print("\n" + "=" * 70)
print("1. TabularNote 块内部结构")
print("=" * 70)
for block_name in ['TabularNote2', 'TabularNote3', 'TabularNote4']:
    if block_name in doc.blocks:
        block = doc.blocks[block_name]
        print(f"\n--- 块: {block_name} ---")
        block_entities = list(block)
        print(f"  实体数: {len(block_entities)}")
        type_counter = Counter(e.dxftype() for e in block_entities)
        for t, n in type_counter.most_common():
            print(f"    {t}: {n}")
        # 抽取所有文字
        print("  文字内容:")
        for e in block_entities:
            if e.dxftype() in ('TEXT', 'MTEXT'):
                try:
                    txt = e.dxf.text if e.dxftype() == 'TEXT' else e.text
                    if txt and txt.strip():
                        print(f"    [{e.dxftype()}] {txt[:80]}")
                except:
                    pass

# 2. OLE2FRAME 二进制内容
print("\n" + "=" * 70)
print("2. OLE2FRAME 提取（尝试提取 OLE 数据）")
print("=" * 70)
ole_frames = [e for e in entities if e.dxftype() == 'OLE2FRAME']
print(f"OLE2FRAME 总数: {len(ole_frames)}")

# 试各种方法提取
import struct
for i, ole in enumerate(ole_frames[:3]):  # 只看前 3 个
    print(f"\n  [{i+1}] handle={ole.dxf.handle}")
    # ezdxf OLE2FRAME 内部数据
    if hasattr(ole, 'ole_data'):
        data = ole.ole_data
        print(f"    ole_data: {len(data) if data else 0} bytes")
        if data:
            # OLE 头是 0xD0CF11E0A1B11AE1
            print(f"    前 8 字节: {data[:8].hex()}")
    # 试 secondary
    if hasattr(ole, 'ole_format'):
        print(f"    ole_format: {ole.ole_format}")
    # 试属性
    for attr in dir(ole):
        if attr.startswith('_') or callable(getattr(ole, attr, None)):
            continue
        try:
            v = getattr(ole, attr)
            if v is not None and not isinstance(v, str) and not isinstance(v, (int, float, bool)):
                if isinstance(v, (bytes, bytearray)):
                    print(f"    {attr}: <bytes len={len(v)}>")
                    print(f"        前16字节: {v[:16].hex()}")
                elif hasattr(v, '__len__') and 0 < len(v) < 50:
                    print(f"    {attr}: {v}")
        except:
            pass

# 3. 按图层聚类的文字（找"表格"图层）
print("\n" + "=" * 70)
print("3. 按图层聚类的文字")
print("=" * 70)
text_by_layer = defaultdict(list)
for e in entities:
    if e.dxftype() in ('TEXT', 'MTEXT'):
        try:
            txt = e.dxf.text if e.dxftype() == 'TEXT' else e.text
            if txt and txt.strip():
                text_by_layer[e.dxf.layer].append(txt)
        except:
            pass

# 找含'表'、'K'、'编号'的图层
for layer, texts in sorted(text_by_layer.items(), key=lambda x: -len(x[1])):
    if any(kw in str(texts) for kw in ['表', '冷却', '编号', 'BOM', '型号', '运水', '零件']):
        print(f"\n  图层 '{layer}': {len(texts)} 个文字")
        for t in texts[:8]:
            print(f"    - {t[:80]}")
        if len(texts) > 8:
            print(f"    ... 还有 {len(texts)-8} 个")
