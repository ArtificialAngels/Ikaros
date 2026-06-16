#!/usr/bin/env python3
r"""
Hermes Agent - multi-model CLI switcher
=======================================

- List `data/models/*.gguf` (with size, quant, params, context, tensors)
- Switch models via llama-server's router mode: POST /v1/models/load
  (no kill+restart; LRU evicts automatically)
- Talk to gopeed-web API to list/add models (signal-bridge demo)

Usage:
    portable-python\python.exe bin\hermes-models.py
    portable-python\python.exe bin\hermes-models.py list
    portable-python\python.exe bin\hermes-models.py switch <name_or_id>
    portable-python\python.exe bin\hermes-models.py download <url> [--name <out.gguf>]
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
GOPEED_BASE = "http://127.0.0.1:9999"


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


# ---------- gopeed-web bridge ----------

def gopeed_request(method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
    url = f"{GOPEED_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[gopeed] {method} {path} -> {e}")
        return None


def gopeed_list() -> None:
    res = gopeed_request("GET", "/api/v1/tasks")
    if not res or res.get("code") != 0:
        return
    tasks = res.get("data", [])
    if not tasks:
        print("[gopeed] no tasks")
        return
    print()
    print(f"{'ID':<25} {'STATUS':<10} {'PROGRESS':>10}  URL")
    print("-" * 90)
    for t in tasks:
        url = t.get("meta", {}).get("req", {}).get("url", "?")
        if len(url) > 50:
            url = url[:47] + "..."
        prog = (t.get("progress") or {}).get("downloaded", 0)
        used = (t.get("progress") or {}).get("used", 1)
        pct = (prog / max(1, used) * 100) if used else 0
        print(f"{t['id']:<25} {t.get('status', '?'):<10} {pct:>9.1f}%  {url}")


def gopeed_download(url: str, out_name: Optional[str] = None) -> int:
    """Create a gopeed-web task. gopeed-web API: body is {req:{url}, opts:{path,name}}."""
    body: dict = {"req": {"url": url}}
    if out_name:
        body["opts"] = {
            "path": str(MODELS_DIR).replace("\\", "/"),
            "name": out_name,
        }
    res = gopeed_request("POST", "/api/v1/tasks", body)
    if res and res.get("code") == 0:
        task_id = res.get("data", "")
        print(f"[OK] task created: {task_id}")
        print(f"     watch: http://127.0.0.1:9999")
        print(f"     save to: {MODELS_DIR / out_name if out_name else MODELS_DIR}")
        return 0
    print(f"[FAIL] could not create task: {res.get('msg') if res else 'no response'}")
    return 1


# ---------- main ----------

def main():
    p = argparse.ArgumentParser(prog="hermes-models", description="Hermes Agent multi-model manager (router mode)")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="list local GGUF models + router state")
    sub.add_parser("gopeed", help="list active gopeed download tasks")

    sw = sub.add_parser("switch", help="preload model via router /v1/models/load (no restart)")
    sw.add_argument("name", help="model filename or partial match")

    dl = sub.add_parser("download", help="create a gopeed download task")
    dl.add_argument("url", help="http(s):// URL to download")
    dl.add_argument("--name", help="output filename (e.g. Qwen3-8B-Q4_K_M.gguf)")

    if len(sys.argv) == 1:
        models = list_models()
        current = current_model_from_bat()
        # Cross-reference with router's discovered set
        rm = router_get("/v1/models", timeout=3.0)
        router_models = [m["id"] for m in (rm.get("data", []) if rm else [])]
        print_models(models, current, router_models)
        print()
        print("commands: list | switch <name> | download <url> | gopeed")
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
    if args.cmd == "download":
        return gopeed_download(args.url, args.name)
    if args.cmd == "gopeed":
        gopeed_list()
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

