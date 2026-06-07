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

# Optional: mirror presets for display
try:
    from hermes.mirror import MIRROR_PRESETS
except ImportError:
    MIRROR_PRESETS = {}

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


def check_mirrors(r: Report):
    """Check mirror/proxy configuration (inspired by ComfyUI-aki-v3)."""
    r.section("mirrors & proxy (network acceleration)")
    try:
        from hermes.mirror import get_mirror_config
        mc = get_mirror_config()
    except Exception:
        r.warn("mirror config unavailable", "hermes.mirror not importable")
        return

    # Proxy
    if mc.proxy_address:
        r.ok("proxy", mc.proxy_address)
        # Test proxy
        code, _ = http_get("https://www.google.com", timeout=2)
        if code > 0:
            r.ok("  proxy reachable", "google.com via proxy")
        else:
            r.warn("  proxy may not work", "test failed, check proxy is running")
    else:
        r.warn("no proxy configured", "set proxy_address in config/hermes.yaml for slow networks")

    # PyPI mirror
    if mc.mirror_pypi:
        index_url = mc.get_pypi_index_url()
        r.ok("PyPI mirror", index_url)
    else:
        r.warn("PyPI mirror disabled", "enable mirror_pypi in config/hermes.yaml for faster pip installs")

    # HuggingFace mirror
    if mc.mirror_huggingface:
        m = str(MIRROR_PRESETS["huggingface"].get(mc.hf_mirror, "?"))
        r.ok("HuggingFace mirror", m)
        # Quick connectivity check
        code, _ = http_get(m, timeout=3)
        if code > 0:
            r.ok("  HF mirror reachable", m)
        else:
            r.warn("  HF mirror unreachable", "check network / firewall")
    else:
        r.warn("HuggingFace mirror disabled", "enable mirror_huggingface for faster model downloads")

    # Git mirror
    if mc.mirror_git:
        r.ok("Git mirror", mc.git_mirror)
    else:
        r.warn("Git mirror disabled", "enable mirror_git for faster git clones")

    # aria2c
    try:
        from hermes.download import find_aria2c
        a2 = find_aria2c()
        if a2:
            r.ok("aria2c", f"available at {a2}")
        else:
            r.warn("aria2c not found", "install aria2 for faster multi-threaded downloads")
    except Exception:
        r.warn("download module unavailable", "hermes.download not importable")


def check_network(r: Report):
    """Quick network connectivity checks."""
    r.section("network connectivity")
    targets = [
        ("PyPI (direct)", "https://pypi.org", 3),
        ("PyPI (aliyun mirror)", "https://mirrors.aliyun.com/pypi/simple/", 3),
        ("HuggingFace (direct)", "https://huggingface.co", 3),
        ("HF-Mirror", "https://hf-mirror.com", 3),
        ("GitHub", "https://github.com", 3),
    ]
    reachable = 0
    for name, url, timeout in targets:
        code, _ = http_get(url, timeout=timeout)
        if code > 0:
            r.ok(name, "reachable")
            reachable += 1
        else:
            r.warn(name, "unreachable")

    if reachable == 0:
        r.err("NO network", "Hermes requires network for cloud LLM and model downloads")
    elif reachable >= 4:
        r.ok("network OK", f"{reachable}/{len(targets)} services reachable")
    else:
        r.warn("limited connectivity", f"{reachable}/{len(targets)} services reachable")


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
    check_mirrors(r)
    check_network(r)
    print(r.render())
    return r.exit_code


if __name__ == "__main__":
    sys.exit(main())
