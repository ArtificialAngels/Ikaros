"""
完整提取 DXF 中的"非 dwg 表格形式"内容
包括：
1. TabularNote 块（标题栏/签字栏/BOM 表）
2. 按图层聚类的文字
3. 表格区域的几何关联
4. 输出为结构化 JSON + Markdown
"""
import ezdxf
from pathlib import Path
from collections import Counter, defaultdict
import re
import json

f = Path(r'E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dxf')

doc = ezdxf.readfile(str(f))
msp = doc.modelspace()
entities = list(msp)

# 提取所有文字（含格式控制符）
def clean_mtext(text: str) -> str:
    """清理 MTEXT 的格式控制符"""
    # 移除 {...} 控制块
    text = re.sub(r'\{[^}]*\}', '', text)
    # 移除单字符控制符
    text = re.sub(r'\\[WwFfTtCcPpQqAaLlKk][^;]*;', '', text)
    text = re.sub(r'\\[WwFfTtCcPpQqAaLlKk]', '', text)
    # 多余空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# 1. 提取所有块内文字（标题栏/签字栏/BOM）
print("=" * 70)
print("1. 块内'表格'内容（标题栏/签字栏/BOM表）")
print("=" * 70)

block_tables = {}
for block_name in ['TabularNote2', 'TabularNote3', 'TabularNote4']:
    if block_name in doc.blocks:
        block = doc.blocks[block_name]
        items = []
        for e in block:
            if e.dxftype() in ('TEXT', 'MTEXT'):
                try:
                    raw = e.dxf.text if e.dxftype() == 'TEXT' else e.text
                    clean = clean_mtext(raw)
                    if clean:
                        items.append({
                            'type': e.dxftype(),
                            'raw': raw,
                            'clean': clean
                        })
                except:
                    pass
        block_tables[block_name] = items

# 渲染成结构化表格
for name, items in block_tables.items():
    print(f"\n--- {name} ({len(items)} 个文字) ---")
    for it in items:
        print(f"  {it['clean']}")

# 2. 按图层聚类的中文文字
print("\n" + "=" * 70)
print("2. 主要文字图层（疑似表格内容）")
print("=" * 70)

# 中文模式
chinese_pattern = re.compile(r'[\u4e00-\u9fff]')
text_by_layer = defaultdict(list)
for e in entities:
    if e.dxftype() in ('TEXT', 'MTEXT'):
        try:
            raw = e.dxf.text if e.dxftype() == 'TEXT' else e.text
            clean = clean_mtext(raw)
            if clean and chinese_pattern.search(clean):
                text_by_layer[e.dxf.layer].append(clean)
        except:
            pass

# 找含表头的图层
table_keywords = ['表', 'BOM', '编号', '型号', '材质', '数量', '件号', '冷却', '运水', '使用', '重量', '处理', '轮廓']
for layer in sorted(text_by_layer.keys(), key=lambda x: -len(text_by_layer[x])):
    if any(kw in str(text_by_layer[layer]) for kw in table_keywords):
        texts = text_by_layer[layer]
        unique = list(dict.fromkeys(texts))  # 去重保序
        print(f"\n[图层 '{layer}'] {len(texts)} 个文字（{len(unique)} 个唯一）")
        for t in unique[:25]:
            print(f"  • {t}")
        if len(unique) > 25:
            print(f"  ... 还有 {len(unique)-25} 个")

# 3. INSERT 块插入位置
print("\n" + "=" * 70)
print("3. 块插入位置 (BOM 表插入位置)")
print("=" * 70)
for e in entities:
    if e.dxftype() == 'INSERT' and 'TabularNote' in e.dxf.name:
        loc = e.dxf.insert
        print(f"  块: {e.dxf.name}  插入点: ({loc[0]:.1f}, {loc[1]:.1f}, {loc[2]:.1f})  图层: {e.dxf.layer}")

# 4. 保存到 JSON
output = {
    'file': f.name,
    'size_mb': round(f.stat().st_size / 1024 / 1024, 2),
    'dxf_version': doc.dxfversion,
    'summary': {
        'total_entities': len(entities),
        'layers': len(list(doc.layers)),
        'blocks': len(list(doc.blocks)),
        'inserts': len([e for e in entities if e.dxftype() == 'INSERT']),
        'mtext_count': len([e for e in entities if e.dxftype() == 'MTEXT']),
        'text_count': len([e for e in entities if e.dxftype() == 'TEXT']),
        'ole2frame_count': len([e for e in entities if e.dxftype() == 'OLE2FRAME']),
    },
    'block_tables': block_tables,
    'layer_text_summary': {k: list(dict.fromkeys(v)) for k, v in text_by_layer.items()},
}

out_json = Path(r'F:\Hermes Agent\dxf_extract.json')
with open(out_json, 'w', encoding='utf-8') as fp:
    json.dump(output, fp, ensure_ascii=False, indent=2)
print(f"\n\n✅ JSON 已保存: {out_json}")
print(f"   大小: {out_json.stat().st_size / 1024:.1f} KB")
