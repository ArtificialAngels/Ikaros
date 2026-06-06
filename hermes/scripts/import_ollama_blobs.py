"""Import Ollama blobs as GGUF files into data/models/.

Ollama stores models as sha256-* blobs in C:/Users/<user>/.ollama/models/blobs/
These are actually GGUF files with no extension.

This script:
  1. Finds all real (non-empty) blobs
  2. Lets you pick which to import
  3. Copies to data/models/ with .gguf extension
  4. Names it after the Ollama model if found, else uses blob hash prefix

Usage:
    python import_ollama_blobs.py
"""
import os
import platform
import shutil
import sys
from pathlib import Path

HERMES_ROOT = Path('E:/Hermes Agent')
DEST = HERMES_ROOT / 'data' / 'models'

# Find Ollama blobs
home = Path(os.environ.get('USERPROFILE', r'C:\Users\PZS0X'))
ollama_blob_dir = home / '.ollama' / 'models' / 'blobs'

if not ollama_blob_dir.exists():
    print(f'ERROR: Ollama blob dir not found: {ollama_blob_dir}')
    sys.exit(1)

# Find Ollama manifest dir (has model names)
ollama_manifest_dir = home / '.ollama' / 'models' / 'manifests'
manifests = {}
if ollama_manifest_dir.exists():
    for mf in ollama_manifest_dir.rglob('*.json'):
        try:
            import json
            data = json.loads(mf.read_text())
            # Manifests reference blobs by digest
            for layer in data.get('layers', []):
                digest = layer.get('digest', '').replace('sha256:', '')
                if digest:
                    manifests[digest] = mf.parent.name + '/' + mf.stem
        except Exception:
            pass

# Find real blobs (skip 0-byte manifests)
blobs = []
for b in ollama_blob_dir.iterdir():
    if b.is_file() and b.stat().st_size > 1_000_000:  # > 1MB
        digest = b.name.replace('sha256-', '')
        size_gb = b.stat().st_size / 1e9
        name = manifests.get(digest, digest[:12])
        blobs.append((b, name, size_gb, digest))

if not blobs:
    print('No real model blobs found (only metadata).')
    sys.exit(0)

print(f'Found {len(blobs)} model blob(s):')
print()
for i, (path, name, size, digest) in enumerate(blobs, 1):
    short = digest[:16]
    print(f'  [{i}] {name}')
    print(f'      blob: sha256-{short}...')
    print(f'      size: {size:.2f}GB')
    print(f'      path: {path}')
    print()

print(f'Destination: {DEST}')
print()

# Auto-import if exactly one blob, else ask
if len(blobs) == 1:
    choices = [1]
else:
    raw = input(f'Import which? (1-{len(blobs)}, comma-separated, or "all"): ').strip()
    if raw.lower() == 'all':
        choices = list(range(1, len(blobs) + 1))
    else:
        choices = [int(x) for x in raw.split(',') if x.strip()]

DEST.mkdir(parents=True, exist_ok=True)

for c in choices:
    src, name, size, digest = blobs[c - 1]
    # Sanitize name
    safe_name = name.replace('/', '_').replace('\\', '_').replace(':', '_')
    if not safe_name.lower().endswith('.gguf'):
        safe_name += '.gguf'
    dest = DEST / safe_name
    print(f'Importing {name} -> {dest.name} ({size:.2f}GB)...')
    shutil.copy2(src, dest)
    print(f'  OK')

print()
print('Done. Update hermes-all.bat MODEL=... line to use one of these,')
print('or use bin\start-llm.bat to test manually.')
