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

# ---- Multi-instance support (parallel llama-server instances) ----
# Read HERMES_LLAMA_FALLBACKS for additional instances.
# Primary instance is always tried first; fallbacks are tried in order.
_LLAMA_CANDIDATES: list[str] = [LLAMA_BASE]
_fb = os.environ.get("HERMES_LLAMA_FALLBACKS", "").strip()
if _fb:
    for u in _fb.split(","):
        u = u.strip().rstrip("/")
        if u and u not in _LLAMA_CANDIDATES:
            _LLAMA_CANDIDATES.append(u)


# ---- back-compat aliases (used to live in this file) ----
def list_models() -> list[dict]:
    return list_gguf_models(MODELS_DIR)

def current_model_from_bat() -> Optional[str]:
    """Router mode has no default model — llama-server picks via LRU."""
    return None

def parse_gguf_meta(path: Path) -> dict:
    return _gguf.parse_gguf_meta(path)


# ---------- router API (replaces switch-model.bat) ----------

def router_get(path: str, timeout: float = 5.0, base_url: str | None = None) -> Optional[dict]:
    """GET from a llama-server instance. Tries base_url first, then candidates."""
    urls = [base_url] if base_url else _LLAMA_CANDIDATES
    for url in urls:
        if url is None:
            continue
        try:
            with urllib.request.urlopen(f"{url}{path}", timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if len(urls) == 1:
                print(f"[router] GET {url}{path} -> {e}")
    return None

def router_post(path: str, body: dict, timeout: float = 180.0, base_url: str | None = None) -> Optional[dict]:
    """POST to a llama-server instance. Tries base_url first, then candidates."""
    urls = [base_url] if base_url else _LLAMA_CANDIDATES
    last_err = None
    for url in urls:
        if url is None:
            continue
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"{url}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}"
            print(f"[router] POST {url}{path} -> {last_err}")
        except Exception as e:
            last_err = str(e)
    if last_err and len(urls) > 1:
        print(f"[router] all {len(urls)} candidates failed, last error: {last_err}")
    return None


# ---------- model listing ----------

def print_models(models: list[dict], current: Optional[str] = None,
                router_models: Optional[list[str]] = None,
                instance_map: Optional[dict[str, str]] = None) -> None:
    """Print model table. *instance_map* maps model_id → instance URL (multi-instance)."""
    if not models:
        print(f"[!] no .gguf models in {MODELS_DIR}")
        print("    add via: hermes-models.py download <url>")
        return
    # Show instance header(s)
    if instance_map:
        instances = sorted(set(instance_map.values()))
        print(f"\n  Instances: {', '.join(instances)}")
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
    # Persist preferred_model so start.ps1 re-selects it on next launch
    try:
        log_dir = HERMES_ROOT / "data" / "logs"
        launch_path = log_dir / "llm-engine-last-launch.json"
        info = {}
        if launch_path.exists():
            info = json.loads(launch_path.read_text(encoding="utf-8"))
        info["preferred_model"] = model_name
        log_dir.mkdir(parents=True, exist_ok=True)
        launch_path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] preferred_model = {model_name}")
    except Exception as e:
        print(f"[warn] could not persist preferred_model: {e}")
    return 0


# ---------- main ----------

def main():
    p = argparse.ArgumentParser(prog="hermes-models", description="Hermes Agent multi-model manager (router mode)")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="list local GGUF models + router state")

    sw = sub.add_parser("switch", help="preload model via router /v1/models/load (no restart)")
    sw.add_argument("name", help="model filename or partial match")

    # Build router state from all instances
    all_router_models, instance_map = _query_all_instances()

    if len(sys.argv) == 1:
        models = list_models()
        current = current_model_from_bat()
        print_models(models, current, all_router_models, instance_map)
        print()
        print("commands: list | switch <name>")
        return 0

    args = p.parse_args()
    if args.cmd == "list":
        models = list_models()
        current = current_model_from_bat()
        print_models(models, current, all_router_models, instance_map)
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


def _query_all_instances() -> tuple[list[str], dict[str, str]]:
    """Query all llama-server candidates for their loaded models."""
    all_router_models: list[str] = []
    instance_map: dict[str, str] = {}  # model_id → instance URL
    for url in _LLAMA_CANDIDATES:
        rm = router_get("/v1/models", timeout=3.0, base_url=url)
        if rm:
            for m in rm.get("data", []):
                mid = m.get("id", "")
                all_router_models.append(mid)
                instance_map[mid] = url
    return all_router_models, instance_map


if __name__ == "__main__":
    sys.exit(main())

