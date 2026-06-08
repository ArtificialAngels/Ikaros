#!/usr/bin/env python3
"""Hermes Model Manager - model inspection and switching (router mode).

llama-server b9538+ runs a SINGLE process in router mode that hosts all
GGUFs in data/models/. Switching "models" no longer needs a kill+restart
cycle — it just POSTs /v1/models/load to preload the chosen model
into VRAM, after which the next chat request routes to it. LRU evicts
whatever was previously resident.

This CLI is now a thin wrapper around that API:

  python model_manager.py list              # list discovered models
  python model_manager.py load <filename>   # preload via /v1/models/load
  python model_manager.py info <filename>   # show NGL/ctx from preset INI
  python model_manager.py gpu               # show GPU info
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional


class ModelInfo:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.size_mb = path.stat().st_size / (1024 * 1024)
        self.size_gb = self.size_mb / 1024
        self.params = self._estimate_params()
        self.vram_required = self._estimate_vram()

    def _estimate_params(self) -> str:
        name = self.name.lower()
        if "35b" in name:
            return "35B"
        elif "1.8b" in name or "1_8b" in name:
            return "1.8B"
        elif "3b" in name:
            return "3B"
        elif "7b" in name:
            return "7B"
        else:
            return "Unknown"

    def _estimate_vram(self) -> str:
        params = self.params
        if params == "1.8B":
            return "2-4 GB"
        elif params == "3B":
            return "4-6 GB"
        elif params == "7B":
            return "8-12 GB"
        elif params == "35B":
            return "20-24 GB"
        else:
            return "Unknown"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "size_mb": round(self.size_mb, 2),
            "size_gb": round(self.size_gb, 2),
            "params": self.params,
            "vram_required": self.vram_required,
        }


class ModelManager:
    """Read-only model info + a thin POST /v1/models/load wrapper.

    The actual LLM lifecycle is owned by llama-server in router mode —
    this class does not (and must not) start or stop llama-server
    itself. Use `bin\hermes-all.bat` for that.
    """

    def __init__(self, hermes_root: Path, llama_port: int = 8080):
        self.hermes_root = hermes_root
        self.models_dir = hermes_root / "data" / "models"
        self.preset_path = self.models_dir / "router-preset.ini"
        self.llama_base = f"http://127.0.0.1:{llama_port}"
        self.llama_port = llama_port
        self.models: Dict[str, ModelInfo] = {}

    # ---- model discovery ----

    def scan_models(self) -> List[ModelInfo]:
        self.models = {}
        models = []
        for gguf in self.models_dir.glob("*.gguf"):
            info = ModelInfo(gguf)
            self.models[info.name] = info
            models.append(info)
        return sorted(models, key=lambda m: m.size_mb)

    # ---- preset INI parsing (data\models\router-preset.ini) ----

    def preset_for(self, gguf_name: str) -> Dict[str, str]:
        """Return the INI section for this model, or empty dict if no preset."""
        if not self.preset_path.exists():
            return {}
        section = None
        out: Dict[str, str] = {}
        with open(self.preset_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                m = re.match(r"^\[(.+)\]$", line)
                if m:
                    section = m.group(1).strip()
                    continue
                if section == gguf_name and "=" in line:
                    k, _, v = line.partition("=")
                    out[k.strip()] = v.strip()
        return out

    # ---- GPU info ----

    def get_gpu_info(self) -> dict:
        import subprocess
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(", ")
                return {
                    "name": parts[0],
                    "total_mb": int(parts[1]),
                    "free_mb": int(parts[2]),
                    "total_gb": round(int(parts[1]) / 1024, 2),
                    "free_gb": round(int(parts[2]) / 1024, 2),
                }
        except Exception:
            pass
        return {"name": "Not detected", "total_mb": 0, "free_mb": 0, "total_gb": 0, "free_gb": 0}

    # ---- router API ----

    def list_routing_models(self) -> List[str]:
        """GET /v1/models — what's actually live in the router right now."""
        try:
            with urllib.request.urlopen(f"{self.llama_base}/v1/models", timeout=3) as r:
                data = json.loads(r.read())
                return [m["id"] for m in data.get("data", [])]
        except Exception as e:
            raise RuntimeError(f"Could not reach llama-server at {self.llama_base}/v1/models: {e}")

    def load_model(self, gguf_name: str, timeout: int = 180) -> bool:
        """POST /v1/models/load — preload a model into VRAM.

        With --models-max 1, this evicts whatever was previously loaded
        via LRU and warm-loads the requested one. The next chat
        request will route to it.
        """
        body = json.dumps({"model": gguf_name}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.llama_base}/models/load",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return 200 <= r.status < 300
        except urllib.error.HTTPError as e:
            print(f"[ERROR] /models/load returned HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")
            return False
        except Exception as e:
            print(f"[ERROR] /models/load failed: {e}")
            return False

    def unload_model(self, gguf_name: str) -> bool:
        """POST /v1/models/unload — evict a model from VRAM."""
        body = json.dumps({"model": gguf_name}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.llama_base}/models/unload",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return 200 <= r.status < 300
        except Exception as e:
            print(f"[ERROR] /models/unload failed: {e}")
            return False

    # ---- display ----

    def list_models(self):
        gpu = self.get_gpu_info()

        print()
        print("=" * 60)
        print("  Hermes Model List (router mode)")
        print("=" * 60)
        print()
        print(f"  GPU:        {gpu['name']}")
        print(f"  Free VRAM:  {gpu['free_gb']:.2f} GB / {gpu['total_gb']:.2f} GB")
        print(f"  Models dir: {self.models_dir}")
        print(f"  Preset:     {self.preset_path} ({'present' if self.preset_path.exists() else 'absent'})")
        print()

        # What does the router actually have registered?
        try:
            live = self.list_routing_models()
            live_set = set(live)
            print(f"  Router registered: {len(live)} model(s) — {', '.join(live)}")
        except RuntimeError as e:
            print(f"  Router: NOT REACHABLE ({e})")
            live_set = set()
        print()

        # Local GGUF list
        models = self.scan_models()
        print(f"  Local GGUFs: {len(models)} file(s)")
        print()
        for i, m in enumerate(models, 1):
            preset = self.preset_for(m.name)
            ngl = preset.get("n-gpu-layers", "auto")
            ctx = preset.get("ctx-size", "auto")
            in_router = "OK " if m.name in live_set else "-- "
            in_router = "RT " if m.name in live_set else "   "
            print(f"  [{i}] {m.name}")
            print(f"      size {m.size_gb:>6.2f} GB  |  params {m.params}  |  NGL={ngl}  |  ctx={ctx}  |  router={in_router}")
        print()
        print("=" * 60)
        print("  Switch via WebUI dropdown, or:")
        print(f"    python model_manager.py load <filename>")
        print("=" * 60)
        print()

    def show_info(self, gguf_name: str):
        if gguf_name not in self.models:
            print(f"[ERROR] model not found locally: {gguf_name}")
            sys.exit(1)
        m = self.models[gguf_name]
        preset = self.preset_for(gguf_name)
        print()
        print("=" * 60)
        print(f"  {gguf_name}")
        print("=" * 60)
        print(f"  Path:   {m.path}")
        print(f"  Size:   {m.size_gb:.2f} GB")
        print(f"  Params: {m.params}")
        print(f"  VRAM:   {m.vram_required}")
        if preset:
            print()
            print("  Preset overrides (data\\models\\router-preset.ini):")
            for k, v in preset.items():
                print(f"    {k} = {v}")
        else:
            print()
            print("  No preset entry; using global defaults.")
        print("=" * 60)
        print()


def main():
    hermes_root = Path(__file__).resolve().parent.parent.parent
    manager = ModelManager(hermes_root)

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "list":
            manager.list_models()
        elif cmd == "load" and len(sys.argv) > 2:
            gguf = sys.argv[2]
            manager.scan_models()
            if gguf not in manager.models:
                print(f"[ERROR] not in data/models/: {gguf}")
                sys.exit(1)
            print(f"Loading {gguf} via POST /v1/models/load ...")
            t0 = time.time()
            ok = manager.load_model(gguf)
            dt = time.time() - t0
            if ok:
                print(f"OK in {dt:.1f}s. Next chat request will route to {gguf}.")
            else:
                print("FAILED. The model is still selectable, but the first chat request will be slower (cold start).")
                sys.exit(1)
        elif cmd == "unload" and len(sys.argv) > 2:
            gguf = sys.argv[2]
            if manager.unload_model(gguf):
                print(f"OK — {gguf} evicted from VRAM.")
            else:
                sys.exit(1)
        elif cmd == "info" and len(sys.argv) > 2:
            manager.scan_models()
            manager.show_info(sys.argv[2])
        elif cmd == "gpu":
            gpu = manager.get_gpu_info()
            print(json.dumps(gpu, indent=2, ensure_ascii=False))
        else:
            print("Usage:")
            print("  python model_manager.py list              # list models + router state")
            print("  python model_manager.py load <filename>   # POST /v1/models/load")
            print("  python model_manager.py unload <filename> # POST /v1/models/unload")
            print("  python model_manager.py info <filename>   # show preset overrides")
            print("  python model_manager.py gpu               # show GPU info")
    else:
        manager.list_models()


if __name__ == "__main__":
    main()
