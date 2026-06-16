"""尝试用 CadImage 完整解析 DWG"""
import aspose.cad as cad
from pathlib import Path
import os

f = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dwg")
print(f"文件: {f.name}  ({f.stat().st_size/1024/1024:.2f} MB)")

# 尝试 1: 转换为 DXF
print("=== 试 1: 转换为 DXF ===")
try:
    image = cad.Image.load(str(f))
    dxf_out = str(f).replace('.dwg', '_aspose.dxf')
    options = cad.imageoptions.DxfOptions()
    options.target_format = cad.FileFormat.DXF
    image.save(dxf_out, options)
    print(f"✅ DXF 已生成: {dxf_out}")
    if os.path.exists(dxf_out):
        print(f"   大小: {os.path.getsize(dxf_out)/1024:.1f} KB")
except Exception as e:
    import traceback
    traceback.print_exc()
