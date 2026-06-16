"""调试：显示图框1的 Y 范围全部 AM_4 文字，按 Y 分组"""
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

# 找"件号" X=10666 (第一个图框)
# 显示 X 10500~10800, Y -4900~-4500 全部 AM_4 文字
print("=== X=10500~10800, Y=-4900~-4500 全部 AM_4 ===")
target = [t for t in texts if 10500 <= t['x'] <= 10800 and -4900 <= t['y'] <= -4500 and t['layer'] == 'AM_4']
target.sort(key=lambda t: (-t['y'], t['x']))
for t in target:
    print(f"  ({t['x']:8.1f}, {t['y']:8.1f})  {t['text']}")
