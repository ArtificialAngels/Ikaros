"""
调试：看 6500-1 那个表（表15）所有相关文字的 Y 坐标
"""
import ezdxf
from pathlib import Path
import re
import importlib.util

# 复用 v12 的 clean_mtext
spec = importlib.util.spec_from_file_location('m', 'F:/Hermes Agent/extract_dxf_batch.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

DXF_PATH = Path(r'E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dxf')
doc = ezdxf.readfile(str(DXF_PATH))
msp = doc.modelspace()
entities = list(msp)

# 收集文字
text_items = []
for e in entities:
    if e.dxftype() in ('TEXT', 'MTEXT'):
        try:
            raw = e.dxf.text if e.dxftype() == 'TEXT' else e.text
            if not raw or not raw.strip():
                continue
            clean = m.clean_mtext(raw)
            if not clean:
                continue
            pos = e.dxf.insert
            text_items.append({
                'x': float(pos[0]), 'y': float(pos[1]),
                'text': clean, 'raw': raw, 'layer': e.dxf.layer,
            })
        except:
            pass

# 找 6500-1 的所有位置
print("=== 含 6500 / 1101 / HNC / Ø1.8 的所有文字（按 Y 排序）===")
target = [t for t in text_items if any(kw in t['text'] for kw in ['6500', '1101', 'HNC', 'Ø1.8', '486', '定模型芯1101'])]
target.sort(key=lambda t: (-t['y'], t['x']))
for t in target:
    print(f"  ({t['x']:8.1f}, {t['y']:8.1f})  [{t['layer']:<15}]  {t['text'][:40]}")

# 6500-1 周围 X=10900~11000 范围
print()
print("=== 6500-1 周围 (X=10850~11050) 所有 AM_4 文字 ===")
target2 = [t for t in text_items if 10850 <= t['x'] <= 11050 and t['layer'] == 'AM_4']
target2.sort(key=lambda t: (-t['y'], t['x']))
for t in target2:
    print(f"  ({t['x']:8.1f}, {t['y']:8.1f})  {t['text'][:40]}")
