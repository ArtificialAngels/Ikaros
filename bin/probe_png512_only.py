# -*- coding: utf-8 -*-
"""再生成一个纯 PNG 512 单帧的 ico，用于对照 WIC 解码行为"""
import struct
from io import BytesIO
from PIL import Image

SRC = 'E:/Ikaros/Artificialangel.png'
img = Image.open(SRC).convert('RGBA')
r = img.resize((512, 512), Image.Resampling.LANCZOS)
buf = BytesIO()
r.save(buf, 'PNG')
payload = buf.getvalue()
r.close()

# 单条目：目录项写 0（=256），数据为 512 PNG
dir_entry = struct.pack('<BBBBHHII', 0, 0, 0, 0, 1, 32, len(payload), 6 + 16)
with open('E:/Ikaros/probe_png512_only.ico', 'wb') as f:
    f.write(struct.pack('<HHH', 0, 1, 1))
    f.write(dir_entry)
    f.write(payload)
print('written probe_png512_only.ico')
