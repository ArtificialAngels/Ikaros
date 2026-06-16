"""
用 Aspose.CAD 重新解析 DWG
"""
import aspose.cad as cad
from pathlib import Path

f = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_点冷却器（已打印）.dwg")
print(f"文件: {f.name}  ({f.stat().st_size/1024/1024:.2f} MB)")

try:
    image = cad.Image.load(str(f))
    print(f"✅ Aspose 加载成功")
    print(f"  类型: {type(image).__name__}")
    print(f"  属性: {[a for a in dir(image) if not a.startswith('_')][:30]}")
    print()

    # 尝试获取图像信息
    if hasattr(image, 'width') and hasattr(image, 'height'):
        print(f"  尺寸: {image.width} x {image.height}")

    # 试 cadoptions
    print()
    print("=== 转换为 DXF ===")
    options = cad.imageoptions.CadRasterizationOptions()
    print(f"  options dir: {[a for a in dir(options) if not a.startswith('_')][:20]}")

    # 试保存为 DXF
    out_path = str(f).replace('.dwg', '_aspose.dxf')
    dxf_options = cad.imageoptions.DxfOptions()
    print(f"  dxf_options dir: {[a for a in dir(dxf_options) if not a.startswith('_')][:20]}")

    # 试 cad.fileformats.cad
    print()
    print("=== 试 cad.CadImage ===")
    if hasattr(cad, 'CadImage'):
        cad_img = cad.CadImage
        print(f"  CadImage: {cad_img}")
    if hasattr(cad, 'fileformats'):
        print(f"  fileformats: {cad.fileformats}")

except Exception as e:
    import traceback
    traceback.print_exc()
