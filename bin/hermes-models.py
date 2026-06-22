#!/usr/bin/env python3
r"""
Hermes Agent - multi-model CLI switcher
=======================================

- List `data/models/*.gguf` (with size, quant, params, context, tensors)
- Switch models via llama-server's router mode: POST /v1/models/load
  (no kill+restart; LRU evicts automatically)

Usage:
    portable-python\python.exe bin\hermes-models.py
    portable-python\python.exe bin\hermes-models.py list
    portable-python\python.exe bin\hermes-models.py switch <name_or_id>
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

# Make hermes package importable when running this script directly
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.model_manager.gguf import (  # noqa: E402
    list_gguf_models, parse_gguf_meta,
)
import modules.model_manager.gguf as _gguf  # for back-compat: list_models / print_models

HERMES_ROOT = _ROOT
MODELS_DIR = HERMES_ROOT / "data" / "models"
LLAMA_BASE = "http://127.0.0.1:8080"


# ---- back-compat aliases (used to live in this file) ----
def list_models() -> list[dict]:
    return list_gguf_models(MODELS_DIR)

def current_model_from_bat() -> Optional[str]:
    """Router mode has no default model — llama-server picks via LRU."""
    return None

def parse_gguf_meta(path: Path) -> dict:
    return _gguf.parse_gguf_meta(path)


# ---------- router API (replaces switch-model.bat) ----------

def router_get(path: str, timeout: float = 5.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"{LLAMA_BASE}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[router] GET {path} -> {e}")
        return None

def router_post(path: str, body: dict, timeout: float = 180.0) -> Optional[dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{LLAMA_BASE}{path}", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"[router] POST {path} -> HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")
        return None
    except Exception as e:
        print(f"[router] POST {path} -> {e}")
        return None


# ---------- model listing ----------

def print_models(models: list[dict], current: Optional[str] = None,
                router_models: Optional[list[str]] = None) -> None:
    if not models:
        print(f"[!] no .gguf models in {MODELS_DIR}")
        print("    add via: hermes-models.py download <url>")
        return
    print()
    print(f"{'#':<3} {'NAME':<40} {'SIZE':>8} {'QUANT':<10} {'ARCH':<14} {'CTX':>8} {'TENSORS':>8}  ROUTER")
    print("-" * 110)
    for i, m in enumerate(models, 1):
        mark = " * " if current and current in m["name"] else "   "
        arch = (m["arch"] or "?")[:14]
        ctx = f"{m['ctx_len'] // 1024}K" if m["ctx_len"] else "?"
        nt = m["n_tensors"] or 0
        in_router = "ok" if (router_models and m["name"] in router_models) else ("--" if router_models is not None else "?")
        print(f"{mark}{i:<3} {m['name']:<40} {m['size_gb']:>6}GB {m['quant'] or '?':<10} {arch:<14} {ctx:>8} {nt:>8}  {in_router}")


# ---------- switching (router mode: just preload via /models/load) ----------

def switch_model(model_name: str) -> int:
    print(f"[*] routing switch to: {model_name}")
    r = router_post("/models/load", {"model": model_name}, timeout=180.0)
    if r is None:
        print("[FAIL] router /models/load did not return OK")
        return 1
    print(f"[OK] preload accepted; next chat request will route to {model_name}")
    return 0


# ---------- main ----------

def main():
    p = argparse.ArgumentParser(prog="hermes-models", description="Hermes Agent multi-model manager (router mode)")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="list local GGUF models + router state")

    sw = sub.add_parser("switch", help="preload model via router /v1/models/load (no restart)")
    sw.add_argument("name", help="model filename or partial match")

    if len(sys.argv) == 1:
        models = list_models()
        current = current_model_from_bat()
        rm = router_get("/v1/models", timeout=3.0)
        router_models = [m["id"] for m in (rm.get("data", []) if rm else [])]
        print_models(models, current, router_models)
        print()
        print("commands: list | switch <name>")
        return 0

    args = p.parse_args()
    if args.cmd == "list":
        models = list_models()
        current = current_model_from_bat()
        rm = router_get("/v1/models", timeout=3.0)
        router_models = [m["id"] for m in (rm.get("data", []) if rm else [])]
        print_models(models, current, router_models)
        return 0
    if args.cmd == "switch":
        models = list_models()
        match = None
        for m in models:
            if args.name in m["name"]:
                match = m["name"]
                break
        if not match:
            print(f"[FAIL] no model matches: {args.name}")
            return 1
        return switch_model(match)

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

