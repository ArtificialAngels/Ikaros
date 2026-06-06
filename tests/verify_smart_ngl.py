"""Verify start-llm-smart.bat NGL calculation logic.

Runs the bat's calculation steps in Python (no cmd parser) to verify the
NGL math is right for each model. Doesn't actually start llama-server.
"""
import os
import subprocess
import sys
import re
from pathlib import Path

HERMES_ROOT = Path(r'E:\Hermes Agent')
MODELS = [
    HERMES_ROOT / 'data' / 'models' / 'Qwen1.5-1.8B-Chat-Q4_K_M.gguf',
    HERMES_ROOT / 'data' / 'models' / 'Qwen2.5-3B-Instruct-Q4_K_M.gguf',
    HERMES_ROOT / 'data' / 'models' / 'Qwen2.5-7B-Instruct-Q4_K_M.gguf',
    HERMES_ROOT / 'data' / 'models' / 'f5ee307a2982.gguf',
]

# Get free VRAM via nvidia-smi
r = subprocess.run(
    ['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
    capture_output=True, text=True,
)
free_mb = int(r.stdout.strip().split('\n')[0])
print(f'Free VRAM: {free_mb} MB')
print()

for model in MODELS:
    if not model.exists():
        print(f'  [SKIP] {model.name} (not found)')
        continue
    size_mb = model.stat().st_size // (1024 * 1024)
    name = model.name

    if size_mb <= free_mb:
        ngl = 99
        mode = 'GPU (full offload)'
    else:
        vram_for_model = free_mb * 7 // 10
        avg_layer_mb = max(1, size_mb // 80)
        ngl_calc = vram_for_model // avg_layer_mb
        if ngl_calc <= 5:
            ngl = 0
            mode = 'CPU (too big for hybrid offload)'
        else:
            ngl = ngl_calc
            mode = f'Hybrid (~{ngl} layers on GPU, rest on CPU)'

    print(f'  {name:40s} {size_mb:>6} MB  NGL={ngl:>3}  {mode}')
