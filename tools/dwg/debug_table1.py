"""
调试：输出图框1区域的所有文字（含坐标）
"""
import ezdxf
from pathlib import Path

DXF_PATH = Path(r'E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dxf')
doc = ezdxf.readfile(str(DXF_PATH))
msp = doc.modelspace()
entities = list(msp)

def clean_mtext(text):
    import re
    text = re.sub(r'\{[^}]*\}', '', text)
    text = re.sub(r'\\[WwFfTtCcPpQqAaLlKk][^;]*;', '', text)
    text = re.sub(r'\\[WwFfTtCcPpQqAaLlKk]', '', text)
    text = text.replace('%%c', 'Ø')
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# 收集文字
texts = []
for e in entities:
    if e.dxftype() in ('TEXT', 'MTEXT'):
        try:
            raw = e.dxf.text if e.dxftype() == 'TEXT' else e.text
            if not raw or not raw.strip():
                continue
            clean = clean_mtext(raw)
            if not clean:
                continue
            pos = e.dxf.insert
            texts.append({
                'x': float(pos[0]), 'y': float(pos[1]),
                'text': clean, 'layer': e.dxf.layer
            })
        except:
            pass

# 找图框1 (Y=-4588 ~ -4890) 范围内所有 AM_4 文字
print("=== 图框1 区域所有 AM_4 文字（Y=-4888 ~ -4590）===")
am4_in_range = [t for t in texts if -4890 <= t['y'] <= -4588 and t['layer'] == 'AM_4']
# 按 Y 排序，Y 内的按 X 排序
am4_in_range.sort(key=lambda t: (-t['y'], t['x']))
for t in am4_in_range:
    print(f"  ({t['x']:8.1f}, {t['y']:8.1f})  {t['text']}")

print(f"\n共 {len(am4_in_range)} 条")
