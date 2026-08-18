"""扫描 E:/Ikaros/tmp 最近 24h 修改过的文件，按时间新→旧排序"""
import os
import time
from pathlib import Path

SCAN_DIR = Path(r"E:\Ikaros\tmp")
CUTOFF = time.time() - 86400  # 24h ago

if not SCAN_DIR.is_dir():
    print(f"[!] 目录不存在: {SCAN_DIR}")
    exit(1)

results = []
for fp in SCAN_DIR.rglob("*"):
    if not fp.is_file():
        continue
    mtime = fp.stat().st_mtime
    if mtime >= CUTOFF:
        results.append((mtime, fp))

results.sort(key=lambda x: x[0], reverse=True)

print(f"最近 24h 修改的文件 (共 {len(results)} 个):\n")
for mtime, fp in results:
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
    size = fp.stat().st_size
    print(f"{t}  {size:>8} B  {fp}")
