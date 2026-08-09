#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ani2apng.py — TinyHand .ani 动画光标 → APNG（浏览器可用的动画光标）

用法: python ani2apng.py <ani文件或目录> -o <输出目录>
说明: .ani 是 Windows 动画光标 (RIFF/ACON)，内含多帧 ICO。
      现代 Chrome/Firefox 支持 APNG 作为 cursor 图片源，可播放动画。
      帧延迟从 anih chunk 读取 (dwRate/dwDelay)，取不到默认 1/60s。
"""
import struct
import sys
import argparse
import io
from pathlib import Path

from PIL import Image, ImageSequence


def parse_ani(path: Path) -> list[Image.Image]:
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"ACON":
        raise ValueError(f"{path.name}: not an .ani (RIFF/ACON)")
    icons: list[bytes] = []
    delay_ms: int | None = None
    i = 12
    while i + 8 <= len(data):
        ck_id = data[i:i + 4]
        ck_size = struct.unpack("<I", data[i + 4:i + 8])[0]
        body = data[i + 8:i + 8 + ck_size]
        if ck_id == b"anih" and len(body) >= 32:
            n_frames = struct.unpack("<I", body[4:8])[0]
            dw_rate = struct.unpack("<I", body[24:28])[0]
            dw_delay = struct.unpack("<I", body[28:32])[0]
            if dw_delay:
                delay_ms = dw_delay
            elif dw_rate:
                delay_ms = int(1000 / dw_rate)
            else:
                delay_ms = 1000 // 60
            print(f"  anih: {n_frames} 帧, 延迟 {delay_ms}ms/帧")
        elif ck_id == b"LIST":
            j = 4  # LIST body: [type(4)][subchunks...]
            end = len(body)
            while j + 8 <= end:
                sub_id = body[j:j + 4]
                sub_size = struct.unpack("<I", body[j + 4:j + 8])[0]
                if sub_id in (b"icon", b"fram"):
                    icons.append(body[j + 8:j + 8 + sub_size])
                j += 8 + sub_size
        i += 8 + ck_size
    if not icons:
        raise ValueError(f"{path.name}: 无帧数据")
    images = []
    for raw in icons:
        try:
            im = Image.open(io.BytesIO(raw)).convert("RGBA")
            images.append(im.copy())
        except Exception as e:
            print(f"  帧解析失败: {e}")
    return images, delay_ms or 1000 // 60


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help=".ani 文件或目录")
    ap.add_argument("-o", "--out", default=".", help="输出目录")
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    files = sorted(src.glob("*.ani")) if src.is_dir() else [src]
    for f in files:
        try:
            frames, delay = parse_ani(f)
            if not frames:
                continue
            # 统一尺寸: 取第一帧尺寸，其余帧 resize 对齐
            w, h = frames[0].size
            frames = [im.resize((w, h), Image.NEAREST) if im.size != (w, h) else im
                      for im in frames]
            dst = out / (f.stem + ".png")
            frames[0].save(
                dst, format="PNG", save_all=True, append_images=frames[1:],
                duration=delay, loop=0, disposal=2,
            )
            print(f"✅ {f.name} → {dst.name} ({len(frames)} 帧, {delay}ms, {w}x{h})")
        except Exception as e:
            print(f"❌ {f.name}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
