"""调试：看件号 (10666, -4593) 附近的 LINE"""
import ezdxf
from pathlib import Path

DXF_PATH = Path(r'E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dxf')
doc = ezdxf.readfile(str(DXF_PATH))
msp = doc.modelspace()
entities = list(msp)

# 找件号 (10666, -4593) 附近的横线
x0, y0 = 10666, -4593
print(f"目标: ({x0}, {y0})")
print()

# 收集 LINE
lines = []
for e in entities:
    if e.dxftype() == 'LINE':
        s = e.dxf.start
        end = e.dxf.end
        x1, y1 = float(s[0]), float(s[1])
        x2, y2 = float(end[0]), float(end[1])
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1
        is_h = abs(x1 - x2) > abs(y1 - y2)
        if is_h:
            lines.append({'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2})

# 找"上方最近"的横线：y1 >= y0，且 X 包含 x0
print("=== 上方最近的横线 (Y >= -4593) ===")
above = [l for l in lines if l['y1'] >= y0 - 1 and l['x1'] - 50 <= x0 <= l['x2'] + 50]
above.sort(key=lambda l: -l['y1'])
for l in above[:20]:
    print(f"  Y={l['y1']:.2f}  X=[{l['x1']:.1f}, {l['x2']:.1f}]  长={l['x2']-l['x1']:.1f}")

# 找"下方最近"的横线
print()
print("=== 下方最近的横线 (Y < -4593) ===")
below = [l for l in lines if l['y1'] < y0 - 1 and l['x1'] - 50 <= x0 <= l['x2'] + 50]
below.sort(key=lambda l: -l['y1'])
for l in below[:20]:
    print(f"  Y={l['y1']:.2f}  X=[{l['x1']:.1f}, {l['x2']:.1f}]  长={l['x2']-l['x1']:.1f}")
