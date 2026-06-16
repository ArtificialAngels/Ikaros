"""
第一步：探查江铃模具目录结构
"""
import os
from pathlib import Path

ROOT = Path(r"E:\KPSNC模具资料\江铃\江铃缸体GE266 2.3 AN\ge266_2.3an(tac2)压铸模具2D,3D图档数据20250929\ge266_2.3an(tac2)压铸模具2D,3D图档数据20250929\2D")

print(f"根目录: {ROOT}")
print(f"存在: {ROOT.exists()}")
print()

if not ROOT.exists():
    print("❌ 目录不存在")
    raise SystemExit(1)

# 列出顶层子目录
subdirs = sorted([p for p in ROOT.iterdir() if p.is_dir()])
print(f"顶层子目录数: {len(subdirs)}")
for sd in subdirs:
    print(f"  📁 {sd.name}")

print()

# 在每个子目录中取前 10 个文件做样本
samples = []
for sd in subdirs:
    files = [p for p in sd.iterdir() if p.is_file()]
    print(f"\n=== 子目录: {sd.name} ({len(files)} 个文件) ===")
    for f in sorted(files)[:15]:
        print(f"  {f.name}")
        samples.append((sd.name, f.name))
    if len(files) > 15:
        print(f"  ... 还有 {len(files) - 15} 个文件")

# 保存样本到文件供后续分析
with open(r"F:\Hermes Agent\samples.txt", "w", encoding="utf-8") as f:
    for sub, name in samples:
        f.write(f"{sub}\t{name}\n")
print(f"\n样本已保存到 F:\\Hermes Agent\\samples.txt")
