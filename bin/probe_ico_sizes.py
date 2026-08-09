# -*- coding: utf-8 -*-
"""探测 Windows 对 >256 BMP 编码 ICO 条目的真实支持情况"""
import struct
from io import BytesIO
from PIL import Image

SRC = 'E:/Ikaros/Artificialangel.png'
img = Image.open(SRC).convert('RGBA')


def bmp_32(im):
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


def make_ico(entries, out):
    """entries: list of (size, 'bmp'|'png')"""
    dirs = b''
    payloads = []
    offset = 6 + 16 * len(entries)
    for size, kind in entries:
        r = img.resize((size, size), Image.Resampling.LANCZOS)
        if kind == 'bmp':
            payload = bmp_32(r)
        else:
            buf = BytesIO()
            r.save(buf, 'PNG')
            payload = buf.getvalue()
        r.close()
        dim = size if size < 256 else 0
        dirs += struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(payload), offset)
        payloads.append(payload)
        offset += len(payload)
    with open(out, 'wb') as f:
        f.write(struct.pack('<HHH', 0, 1, len(entries)))
        f.write(dirs)
        for p in payloads:
            f.write(p)
    print(f'{out}: {len(entries)} entries, {offset} bytes')


make_ico([(512, 'bmp')], 'E:/Ikaros/probe_bmp512.ico')
make_ico([(512, 'png')], 'E:/Ikaros/probe_png512.ico')
make_ico([(1024, 'bmp')], 'E:/Ikaros/probe_bmp1024.ico')
make_ico([(256, 'bmp'), (512, 'bmp'), (1024, 'bmp')], 'E:/Ikaros/probe_mix_bmp.ico')
make_ico([(256, 'bmp'), (512, 'png'), (1024, 'png')], 'E:/Ikaros/probe_mix_png.ico')
print('done')
