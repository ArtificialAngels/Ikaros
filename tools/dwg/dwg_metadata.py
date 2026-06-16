"""
DWG 元数据提取 - 简洁版
"""
from ezdxf.addons import dwg
from pathlib import Path
import json
import time

# 关键 HEADER 变量（图纸"身份证"）
KEY_VARS = [
    # 基本信息
    "$ACADVER", "$ACADMAINTVER", "$DWGCODEPAGE",
    # 责任人
    "$LASTSAVEDBY", "$LOGINNAME",
    # 时间（AutoCAD 日期数字）
    "$TDCREATE", "$TDUPDATE", "$TDINDWG", "$TDUSRTIMER",
    # 项目
    "$PROJECTNAME", "$COMMENTS", "$COMPANY",
    # 几何
    "$INSBASE", "$EXTMIN", "$EXTMAX", "$LIMMIN", "$LIMMAX",
    # 单位
    "$MEASUREMENT", "$INSUNITS", "$LUNITS", "$LUPREC",
    # 标题块（可能不存在）
    "$TITLED",
]


def extract_metadata(dwg_path: Path) -> dict:
    """从 DWG 提取元数据"""
    result = {
        "file": dwg_path.name,
        "size_kb": round(dwg_path.stat().st_size / 1024, 1),
    }
    try:
        doc = dwg.readfile(str(dwg_path))
        result["dxfversion"] = doc.dxfversion
        result["acad_release"] = doc.acad_release
        result["encoding"] = doc.encoding
        result["compatible"] = doc._acad_compatible

        hdr = doc.header
        for key in KEY_VARS:
            try:
                v = hdr.get(key)
                if v is not None and v != "":
                    result[key] = v
            except Exception:
                pass

        # 转换 AutoCAD 时间：$TDCREATE/$TDUPDATE 是儒略日(JDN)浮点数
        # JDN 0 = -4713-11-24 (儒略历) / -4712-01-01 (Gregorian proleptic)
        # 2458532 → 2019-02-21 左右
        for k in ["$TDCREATE", "$TDUPDATE"]:
            if k in result:
                try:
                    jdn = float(result[k])
                    from datetime import datetime, timedelta
                    # JDN 转换为 Python datetime
                    # 算法：JDN + 32044 -> 转换成 Gregorian, 然后用 datetime
                    # 简化：使用 jd2date
                    # JDN 2440587.5 = 1970-01-01 00:00:00 UTC
                    dt = datetime(1970, 1, 1) + timedelta(days=(jdn - 2440587.5))
                    result[f"{k}_iso"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
    except Exception as e:
        result["error"] = str(e)

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        # 默认测试
        test_dir = Path(r"E:\KPSNC模具资料\GEELY_BHE20_ICE\2026-04-08_25-8715_Geely BHE20-ICE\1.2D3DMOLd\2D\COOLING")
        files = sorted(test_dir.glob("*.dwg"))[:3]
    else:
        files = [Path(f) for f in sys.argv[1:]]

    for f in files:
        print(f"\n{'='*70}")
        print(f"📄 {f.name}  ({f.stat().st_size/1024:.1f} KB)")
        print('='*70)
        start = time.time()
        meta = extract_metadata(f)
        elapsed = time.time() - start
        print(f"  ⏱️  解析耗时: {elapsed*1000:.0f} ms")
        for k, v in meta.items():
            s = str(v)
            if len(s) > 100:
                s = s[:100] + "..."
            print(f"  {k}: {s}")
