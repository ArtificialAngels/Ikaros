"""详细看第一表的所有数据 X"""
import ezdxf
from pathlib import Path
import re

DXF_PATH = Path(r'E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dxf')
doc = ezdxf.readfile(str(DXF_PATH))
msp = doc.modelspace()
entities = list(msp)

def clean_mtext(text):
    text = re.sub(r'\{[^}]*\}', '', text)
    text = re.sub(r'\\[WwFfTtCcPpQqAaLlKk][^;]*;', '', text)
    text = re.sub(r'\\[WwFfTtCcPpQqAaLlKk]', '', text)
    text = text.replace('%%c', 'Ø')
    return re.sub(r'\s+', ' ', text).strip()

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
            texts.append({'x': float(pos[0]), 'y': float(pos[1]), 'text': clean, 'layer': e.dxf.layer})
        except:
            pass

# 找包含"6500"的所有文字
print("=== 包含 '6500' 的所有文字 ===")
for t in texts:
    if '6500' in t['text'] or '1101' in t['text'] or 'HNC' in t['text'] or '1104' in t['text']:
        print(f"  ({t['x']:8.1f}, {t['y']:8.1f})  [{t['layer']}]  {t['text']}")
