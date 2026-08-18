# -*- coding: utf-8 -*-
"""用 Artificialangel.png 生成复合 ICO：<=256 BMP 编码，>256 PNG 编码"""
import struct
from io import BytesIO
from PIL import Image

SIZES = [16, 20, 24, 28, 32, 40, 48, 56, 60, 72, 80, 84, 86, 120, 128, 144, 256, 512, 768, 1024]
SRC = 'E:/Ikaros/assets/Artificialangel.png'
OUT = 'E:/Ikaros/Artificialangel.ico'

img = Image.open(SRC).convert('RGBA')


def bmp_32(im):
    """32 位 BGRA BMP（自下而上 + AND 掩码），与 ico-generator 项目同款逻辑"""
    w, h = im.size
    header = struct.pack('<IiiHHIIiiII', 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    px = im.tobytes()
    bgra = bytearray()
    row_size = w * 4
    pad = (4 - row_size % 4) % 4
    for y in range(h - 1, -1, -1):
        row_start = y * row_size
        for x in range(w):
            idx = row_start + x * 4
            bgra.extend([px[idx + 2], px[idx + 1], px[idx], px[idx + 3]])
        bgra.extend([0] * pad)
    and_mask = bytes(h * ((w + 31) // 32) * 4)
    return header + bytes(bgra) + and_mask


entries = b''
payloads = []
offset = 6 + 16 * len(SIZES)
for s in sorted(SIZES, reverse=True):
    r = img.resize((s, s), Image.Resampling.LANCZOS)
    if s <= 256:
        payload = bmp_32(r)
        dim = s if s < 256 else 0
    else:
        buf = BytesIO()
        r.save(buf, 'PNG')
        payload = buf.getvalue()
        dim = 0  # ICO 目录项无法表达 >256，Vista+ 从 PNG 实际尺寸读取
    entries += struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(payload), offset)
    payloads.append(payload)
    offset += len(payload)
    r.close()

with open(OUT, 'wb') as f:
    f.write(struct.pack('<HHH', 0, 1, len(SIZES)))
    f.write(entries)
    for p in payloads:
        f.write(p)

print(f'written: {OUT} ({offset} bytes, {len(SIZES)} entries)')

# 验证
with Image.open(OUT) as chk:
    print('PIL sizes:', sorted(chk.info.get('sizes', [])))


# ---------- ICNS（macOS 标准类型码，16/32/64/128/256/512/1024） ----------
ICNS_OUT = 'E:/Ikaros/Artificialangel.icns'
ICNS_TYPES = [(16, b'icp4'), (32, b'icp5'), (64, b'icp6'), (128, b'ic07'),
              (256, b'ic08'), (512, b'ic09'), (1024, b'ic10')]

entries = bytearray()
for size, code in ICNS_TYPES:
    r = img.resize((size, size), Image.Resampling.LANCZOS)
    buf = BytesIO()
    r.save(buf, 'PNG')
    png = buf.getvalue()
    r.close()
    # ICNS 条目必须连续：type(4) + length(4) + data —— 无 offset 字段，解析器按 length 跳转
    entries += code + struct.pack('>I', 8 + len(png)) + png

total = 8 + len(entries)
with open(ICNS_OUT, 'wb') as f:
    f.write(b'icns' + struct.pack('>I', total))
    f.write(bytes(entries))

print(f'written: {ICNS_OUT} ({total} bytes, {len(ICNS_TYPES)} entries)')

with Image.open(ICNS_OUT) as chk:
    print('PIL icns sizes:', sorted(chk.info.get('sizes', [])))
