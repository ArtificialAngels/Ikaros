"""检查 Y=-4821 的所有标题文字"""
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

# Y=-4821 簇所有文字
print("=== Y=-4821 簇所有 AM_4 文字 ===")
target = [t for t in texts if abs(t['y'] - (-4821.319)) < 2 and t['layer'] == 'AM_4']
target.sort(key=lambda t: t['x'])
for t in target:
    print(f"  ({t['x']:8.1f}, {t['y']:8.1f})  {t['text']}")

print()
print("=== Y=-4593 簇所有 AM_4 文字 ===")
target = [t for t in texts if abs(t['y'] - (-4593.9)) < 2 and t['layer'] == 'AM_4']
target.sort(key=lambda t: t['x'])
for t in target:
    print(f"  ({t['x']:8.1f}, {t['y']:8.1f})  {t['text']}")
