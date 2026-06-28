"""Extract SVG from character.html and create character.svg"""
import re, os
os.chdir(r"E:\Hermes Agent")

with open('bin/ikaros-desktop-pet/character.html', encoding='utf-8') as f:
    html = f.read()

m = re.search(r'<svg viewBox="0 0 200 280"[^>]*>.*?</svg>', html, re.DOTALL)
if m:
    svg = m.group()
    svg = svg.replace('</svg>', '''
  <style>
    .eye { animation: blink 4s infinite; transform-origin: center; }
    @keyframes blink {
      0%, 95%, 100% { transform: scaleY(1); }
      97% { transform: scaleY(0.1); }
    }
  </style>
</svg>''')
    with open('bin/ikaros-desktop-pet/character.svg', 'w', encoding='utf-8') as f:
        f.write(svg)
    print(f'✓ character.svg created ({len(svg)} bytes)')
else:
    print('✗ SVG not found in HTML')
