r"""
Hermes Agent - Diagnostic / health-check tool (hermes-doctor).

Inspired by ComfyUI-aki's matsu.exe. Scans the project for common
issues and prints a report with actionable fixes.

Run:
    portable-python\python.exe -m hermes.doctor
    bin\hermes-doctor.bat

Exits 0 if everything OK, 1 if warnings, 2 if errors.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERMES_ROOT = Path(__file__).resolve().parent.parent
RUNTIME = HERMES_ROOT / "runtime"
MODELS_DIR = HERMES_ROOT / "data" / "models"
DATA_DIR = HERMES_ROOT / "hermes" / "data"
LOGS_DIR = DATA_DIR / "logs"
ENV_FILE = HERMES_ROOT / ".env"
ENV_EXAMPLE = HERMES_ROOT / ".env.example"
GOPEED_BASE = "http://127.0.0.1:9999"
DEFAULT_MODEL_PORT = 8080
HERMES_API_PORT = 7860
OW_PORT = 7870


# ---- helpers ----

def run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError:
        return 127, "", "not found"
    except Exception as e:
        return 1, "", str(e)


def http_get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return 0, str(e)


class Report:
    def __init__(self):
        self.sections: list[tuple[str, list[tuple[str, str, str]]]] = []
        # status: "ok" / "warn" / "err"
        self.current_section: Optional[str] = None
        self.current_items: list[tuple[str, str, str]] = []
        self.exit_code = 0

    def section(self, name: str):
        if self.current_section is not None:
            self.sections.append((self.current_section, self.current_items))
        self.current_section = name
        self.current_items = []

    def ok(self, name: str, detail: str = ""):
        self.current_items.append(("ok", name, detail))

    def warn(self, name: str, detail: str):
        self.current_items.append(("warn", name, detail))
        self.exit_code = max(self.exit_code, 1)

    def err(self, name: str, detail: str):
        self.current_items.append(("err", name, detail))
        self.exit_code = max(self.exit_code, 2)

    def render(self) -> str:
        if self.current_section is not None:
            self.sections.append((self.current_section, self.current_items))
        out = []
        out.append("=" * 70)
        out.append("  Hermes Doctor - Project Health Report")
        out.append("=" * 70)
        for sec_name, items in self.sections:
            out.append("")
            out.append(f"  [{sec_name}]")
            for status, name, detail in items:
                mark = {"ok": "[OK]", "warn": "[WARN]", "err": "[ERR]"}[status]
                line = f"    {mark:<7} {name}"
                if detail:
                    line += f"  ({detail})"
                out.append(line)
        out.append("")
        out.append("=" * 70)
        if self.exit_code == 0:
            out.append("  Result: ALL OK")
        elif self.exit_code == 1:
            out.append("  Result: WARNINGS (some optional features missing)")
        else:
            out.append("  Result: ERRORS (core functionality broken)")
        out.append("=" * 70)
        return "\n".join(out)


# ---- checks ----

def check_runtime(r: Report):
    r.section("llama.cpp runtime (E:\\Hermes Agent\\runtime)")
    if not RUNTIME.exists():
        r.err("runtime dir missing", str(RUNTIME))
        return
    server = RUNTIME / "llama-server.exe"
    if server.exists():
        sz = server.stat().st_size
        # b9538 stub = 9728 bytes, full exe is much larger
        r.ok("llama-server.exe", f"{sz} bytes")
    else:
        r.err("llama-server.exe missing", "run bin/setup-runtime.bat")
    # DLLs
    cuda = RUNTIME / "llama-server-cuda-12.4.exe"
    vk = RUNTIME / "llama-server-vulkan.exe"
    cpu = RUNTIME / "llama-server-cuda-11.8.exe"
    found = []
    for n, p in (("cuda-12.4", cuda), ("vulkan", vk), ("cuda-11.8", cpu)):
        if p.exists():
            found.append(n)
    if "cuda-12.4" in found:
        r.ok("CUDA 12.4 binary", cuda.name)
    else:
        r.warn("CUDA 12.4 missing", "RTX 30/40/50 NVIDIA users should run setup-runtime.bat")
    if "vulkan" in found:
        r.ok("Vulkan binary", vk.name)
    if "cuda-11.8" in found:
        r.ok("CUDA 11.8 binary", cpu.name)
    # cudart
    cudart = RUNTIME / "cudart64_12.dll"
    if cudart.exists():
        r.ok("cudart64_12.dll", f"present ({cudart.stat().st_size} bytes)")
    else:
        r.warn("cudart64_12.dll missing",
               "GPU acceleration may not work on machines without system CUDA 12.4")
    # gopeed-web
    gopeed = RUNTIME / "gopeed-web.exe"
    if gopeed.exists():
        r.ok("gopeed-web.exe", f"{gopeed.stat().st_size} bytes (Python comm bridge)")
    else:
        r.warn("gopeed-web.exe missing", "Python download bridge unavailable")


def check_models(r: Report):
    r.section("models (data\\models)")
    if not MODELS_DIR.exists():
        r.err("models dir missing", str(MODELS_DIR))
        return
    ggufs = list(MODELS_DIR.glob("*.gguf"))
    if not ggufs:
        r.warn("no .gguf models", f"add via hermes-models.py download <url>")
    else:
        for g in sorted(ggufs):
            sz_gb = g.stat().st_size / 1e9
            r.ok(g.name, f"{sz_gb:.2f} GB")


def check_gpus(r: Report):
    r.section("GPU detection")
    rc, out, _ = run(["nvidia-smi", "--query-gpu=name,memory.total",
                      "--format=csv,noheader,nounits"])
    if rc == 0 and out.strip():
        line = out.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            r.ok("NVIDIA", f"{parts[0]} ({parts[1]}MB)")
            return
    rc, out, _ = run(["vulkaninfo", "--summary"], timeout=8)
    if rc == 0 and "deviceName" in (out or ""):
        r.ok("Vulkan", "device present")
    else:
        r.warn("no GPU detected", "running pure CPU")


def check_services(r: Report):
    r.section("running services")
    code, body = http_get(f"http://127.0.0.1:{DEFAULT_MODEL_PORT}/health")
    if code == 200:
        try:
            j = json.loads(body)
            mode = j.get("mode", "?")
            model = j.get("model", j.get("status", "?"))
            r.ok(f"llama-server (:{DEFAULT_MODEL_PORT})", f"mode={mode}")
        except Exception:
            r.ok(f"llama-server (:{DEFAULT_MODEL_PORT})", "responding")
    else:
        r.warn(f"llama-server (:{DEFAULT_MODEL_PORT}) not running",
               "start with bin/hermes-all.bat or bin/start-llm-smart.bat")
    code, body = http_get(f"http://127.0.0.1:{HERMES_API_PORT}/health")
    if code == 200:
        r.ok(f"Hermes API (:{HERMES_API_PORT})", "up")
    else:
        r.warn(f"Hermes API (:{HERMES_API_PORT}) not running", "run bin/hermes-web.bat")
    code, body = http_get(f"http://127.0.0.1:{OW_PORT}/")
    if code == 200:
        r.ok(f"Open WebUI (:{OW_PORT})", "up")
    else:
        r.warn(f"Open WebUI (:{OW_PORT}) not running", "run bin/start-openwebui.bat")


def check_gopeed(r: Report):
    r.section("gopeed-web (Python communication bridge)")
    code, body = http_get(f"{GOPEED_BASE}/api/v1/tasks")
    if code == 200:
        try:
            j = json.loads(body)
            n = len(j.get("data", []))
            r.ok("gopeed-web :9999", f"up, {n} active tasks")
        except Exception:
            r.ok("gopeed-web :9999", "responding")
    else:
        r.warn("gopeed-web :9999 unreachable", "Hermes Python bridge will fall back to direct download")


def check_python(r: Report):
    r.section("Python environment")
    py = HERMES_ROOT / "portable-python" / "python.exe"
    if not py.exists():
        r.err("portable-python missing", str(py))
        return
    rc, out, _ = run([str(py), "--version"])
    if rc == 0:
        r.ok("portable-python", out.strip())
    else:
        r.err("portable-python broken", str(out))
    # check key deps
    for mod in ("fastapi", "uvicorn", "httpx", "pydantic"):
        rc, _, _ = run([str(py), "-c", f"import {mod}"])
        if rc == 0:
            r.ok(f"  {mod}", "importable")
        else:
            r.warn(f"  {mod} missing",
                   f"run: {py} -m pip install {mod}")


def check_disk(r: Report):
    r.section("disk space")
    try:
        import shutil
        total, used, free = shutil.disk_usage(HERMES_ROOT)
        free_gb = free / 1e9
        if free_gb < 5:
            r.err("free space < 5GB", f"only {free_gb:.1f}GB free")
        elif free_gb < 20:
            r.warn("free space < 20GB", f"{free_gb:.1f}GB free (large models need more)")
        else:
            r.ok("free space", f"{free_gb:.1f}GB")
    except Exception as e:
        r.warn("disk check failed", str(e))


def check_env(r: Report):
    r.section(".env config")
    if not ENV_FILE.exists():
        r.warn(".env missing", f"copy from {ENV_EXAMPLE.name} and fill in API keys")
    else:
        keys = []
        try:
            for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    keys.append(k.strip())
        except Exception:
            pass
        if "OPENAI_API_KEY" in keys or "MINIMAX_API_KEY" in keys:
            r.ok(".env has at least one LLM key", f"{len(keys)} keys set")
        else:
            r.warn(".env has no LLM API key", "cloud LLM will be unavailable")


# ---- main ----

def main():
    r = Report()
    check_runtime(r)
    check_models(r)
    check_gpus(r)
    check_services(r)
    check_gopeed(r)
    check_python(r)
    check_disk(r)
    check_env(r)
    print(r.render())
    return r.exit_code


if __name__ == "__main__":
    sys.exit(main())
