"""
测试 ezdxf.addons.dwg 能从 DWG 中提取什么信息
"""
from ezdxf.addons import dwg
from pathlib import Path
import json

# 选 3 个不同类型文件测试
test_files = [
    Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\mov_cavity(3XXXX)\25-8715_3000_动模镶块(已打印).dwg"),
    Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING\25-8715_6520_定模中心管(已打印).dwg"),
    Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\asm\25-8715_总装图(已打印).dwg"),
]

for f in test_files:
    print(f"\n{'='*80}")
    print(f"文件: {f.name}")
    print(f"大小: {f.stat().st_size} 字节")
    print('='*80)

    try:
        doc = dwg.readfile(str(f))
        print(f"DXF 版本: {doc.dxfversion}")
        print(f"ACAD 版本: {doc.acad_release}")
        print(f"编码: {doc.encoding}")
        print(f"兼容: {doc._acad_compatible}")

        # 全部 HEADER 变量
        hdr = doc.header
        all_keys = list(hdr.varnames())
        print(f"\nHEADER 变量数: {len(all_keys)}")
        print("前 30 个:")
        for k in all_keys[:30]:
            try:
                v = hdr.get(k)
                if v is not None and v != "":
                    s = str(v)
                    if len(s) > 80:
                        s = s[:80] + "..."
                    print(f"  {k}: {s}")
            except:
                pass

        # 试读图层（即使报 None，看实际）
        print(f"\ndoc.layers: {doc.layers}")
        print(f"doc.blocks: {doc.blocks}")
        print(f"doc.stored_sections: {doc.stored_sections}")

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
