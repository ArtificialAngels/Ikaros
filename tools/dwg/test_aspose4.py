"""用 Aspose.CAD 渲染 DWG 为 PNG"""
import aspose.cad as cad
from pathlib import Path
import os

f = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dwg")
print(f"文件: {f.name}  ({f.stat().st_size/1024/1024:.2f} MB)")

# 渲染为 PNG
out_png = str(f).replace('.dwg', '_aspose.png')
try:
    image = cad.Image.load(str(f))
    ras = cad.imageoptions.CadRasterizationOptions()
    ras.page_width = 2970.0
    ras.page_height = 4200.0
    ras.zoom = 0.5

    options = cad.imageoptions.PngOptions()
    options.vector_rasterization_options = ras
    image.save(out_png, options)
    print(f"✅ PNG 已生成: {out_png}  ({os.path.getsize(out_png)/1024:.1f} KB)")
except Exception as e:
    import traceback
    traceback.print_exc()

