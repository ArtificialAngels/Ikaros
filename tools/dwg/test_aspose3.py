"""用 Aspose.CAD 把 DWG 渲染为 PNG/SVG"""
import aspose.cad as cad
from pathlib import Path
import os

f = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dwg")
print(f"文件: {f.name}  ({f.stat().st_size/1024/1024:.2f} MB)")

# 渲染为 PNG
for ext in ['png', 'svg']:
    out = str(f).replace('.dwg', f'_aspose.{ext}')
    try:
        image = cad.Image.load(str(f))
        options_class = getattr(cad.imageoptions, f'{ext.upper()}Options')
        options = options_class()
        image.save(out, options)
        print(f"✅ {ext.upper()} 已生成: {out}  ({os.path.getsize(out)/1024:.1f} KB)")
    except Exception as e:
        print(f"❌ {ext}: {e}")
