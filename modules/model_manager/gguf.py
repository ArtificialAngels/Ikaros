r"""
Hermes Agent - GGUF model metadata parser.

Pure-Python GGUF v2/v3 header parser. No external dependencies.

Returns dict per model with: name, size_bytes, size_gb, arch, ctx_len,
n_tensors, quant (inferred from filename).

Usage:
    from modules.model_manager.gguf import list_gguf_models, parse_gguf_meta
"""
from __future__ import annotations
import re
import struct
from pathlib import Path
from typing import Optional


# GGUF v3 type table (per llama.cpp gguf.h)
# v2 had: STRING=7, ARRAY=8, UINT64=9, ...
# v3 is:  BLOB=7, STRING=8, ARRAY=9, UINT64=10, INT64=11, FLOAT64=12, BOOL=13
# v2 and v3 share: UINT8=0, INT8=1, UINT16=2, INT16=3, UINT32=4, INT32=5, FLOAT32=6


def parse_gguf_meta(path: Path) -> dict:
    """Parse GGUF v2/v3 header and return metadata dict."""
    info: dict = {
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "size_gb": round(path.stat().st_size / 1e9, 2),
        "arch": None,
        "ctx_len": None,
        "n_tensors": None,
        "quant": None,
    }
    qmatch = re.search(
        r"[-_](Q\d_K_[MSL]|Q\d_\d|Q\d|UD-[\w_-]+|F16|BF16|F32)\.gguf$",
        path.name, re.IGNORECASE,
    )
    if qmatch:
        info["quant"] = qmatch.group(1)
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"GGUF":
                return info
            ver = struct.unpack("<I", f.read(4))[0]
            if ver not in (2, 3):
                return info
            n_tensors = struct.unpack("<Q", f.read(8))[0]
            info["n_tensors"] = n_tensors
            n_kv = struct.unpack("<Q", f.read(8))[0]
            for _ in range(n_kv):
                kl = struct.unpack("<Q", f.read(8))[0]
                key = f.read(kl).decode("utf-8", errors="ignore")
                vtype = struct.unpack("<I", f.read(4))[0]
                _skip_value(f, vtype, ver, key, info)
    except Exception:
        pass
    return info


def _skip_value(f, vtype: int, ver: int, key: str, info: dict) -> None:
    """Skip (or capture) a GGUF key-value pair. Mutates f position + info."""
    # v2: STRING=7, ARRAY=8, UINT64=9, INT64=10, FLOAT64=11, BOOL=12
    # v3: BLOB=7, STRING=8, ARRAY=9, UINT64=10, INT64=11, FLOAT64=12, BOOL=13
    # We treat: v3 STRING (=8), v2 STRING (=7), v3 ARRAY (=9), v2 ARRAY (=8)
    is_v3 = ver == 3
    string_type = 8 if is_v3 else 7
    array_type = 9 if is_v3 else 8
    uint64_type = 10 if is_v3 else 9
    int64_type = 11 if is_v3 else 10
    float64_type = 12 if is_v3 else 11
    bool_type = 13 if is_v3 else 12
    blob_type = 7 if is_v3 else None  # no BLOB in v2

    # capture known keys
    if key == "general.architecture" and vtype == string_type:
        slen = struct.unpack("<Q", f.read(8))[0]
        info["arch"] = f.read(slen).decode("utf-8", errors="ignore")
        return
    if key.endswith(".context_length") and vtype in (4, 5, uint64_type):
        if vtype in (4, 5):
            info["ctx_len"] = struct.unpack("<I", f.read(4))[0]
        else:
            info["ctx_len"] = struct.unpack("<Q", f.read(8))[0]
        return
    if key == "general.parameter_count" and vtype == uint64_type:
        # not used in display but skip properly
        f.read(8)
        return

    # skip
    if vtype in (0, 1):                       # UINT8 / INT8
        f.read(1)
    elif vtype in (2, 3):                     # UINT16 / INT16
        f.read(2)
    elif vtype in (4, 5, 6):                  # UINT32 / INT32 / FLOAT32
        f.read(4)
    elif blob_type is not None and vtype == blob_type:
        slen = struct.unpack("<Q", f.read(8))[0]
        f.read(slen)
    elif vtype == string_type:
        slen = struct.unpack("<Q", f.read(8))[0]
        f.read(slen)
    elif vtype in (uint64_type, int64_type, float64_type):
        f.read(8)
    elif vtype == bool_type:
        f.read(1)
    elif vtype == array_type:
        etype = struct.unpack("<I", f.read(4))[0]
        alen = struct.unpack("<Q", f.read(8))[0]
        # recursively skip element values
        for _ in range(alen):
            _skip_value(f, etype, ver, "<arr-elem>", info)
    # else: unknown type, leave cursor as-is (best effort)


def list_gguf_models(models_dir: Path) -> list[dict]:
    """Scan models_dir for *.gguf and return parsed metadata for each."""
    if not models_dir.exists():
        return []
    out: list[dict] = []
    for f in sorted(models_dir.glob("*.gguf")):
        out.append(parse_gguf_meta(f))
    return out


def current_model_from_bat(hermes_root: Path) -> Optional[str]:
    """Parse bin/hermes-all.bat to find the default MODEL setting."""
    bat = hermes_root / "bin" / "hermes-all.bat"
    if not bat.exists():
        return None
    try:
        txt = bat.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    m = re.search(r'set\s+"MODEL=[^"]*\\([^"\\]+\.gguf)"', txt)
    return m.group(1) if m else None
