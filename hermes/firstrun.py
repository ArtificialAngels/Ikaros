r"""
Hermes Agent - First-run / startup environment bootstrap.

Called by bin/hermes-firstrun.bat at boot time. Detects GPU + runtime
dependencies, downloads what's missing (via gopeed-web if available),
and reports back so the launcher knows what backend to use.

Goals:
- Self-contained: detect NVIDIA / AMD / Intel / none
- Self-healing: download missing GPU runtime (cudart, rocm, vulkan extpack)
- Self-graceful: if download fails, just report "no GPU acceleration"
                  and let the user run pure CPU
- Idempotent: re-runs are no-ops; downloaded bits are kept forever

Run modes:
    portable-python\python.exe -m hermes.firstrun check
        -> returns exit code 0=ok, 1=warn (cpu fallback), 2=err
    portable-python\python.exe -m hermes.firstrun install
        -> actively downloads missing components (long-running)
    portable-python\python.exe -m hermes.firstrun status
        -> human-readable report
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

HERMES_ROOT = Path(__file__).resolve().parent.parent
RUNTIME = HERMES_ROOT / "runtime"
GOPEED_BASE = "http://127.0.0.1:9999"

# What we consider the canonical "GPU present" status.
STATUS_CPU = "cpu"
STATUS_CUDA = "cuda"
STATUS_VULKAN = "vulkan"
STATUS_HIP = "hip"  # AMD


# ---- subprocess helpers ----

def run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    """Run a command, return (rc, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError:
        return 127, "", "not found"
    except Exception as e:
        return 1, "", str(e)


# ---- GPU detection ----

def detect_nvidia() -> dict:
    """Try nvidia-smi. Returns {present, name, vram_mb, cuda_runtime}."""
    rc, out, _ = run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                      "--format=csv,noheader,nounits"])
    if rc != 0 or not out.strip():
        return {"present": False}
    line = out.strip().splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 3:
        return {"present": False}
    return {
        "present": True,
        "name": parts[0],
        "vram_mb": int(parts[1]),
        "driver": parts[2],
    }


def detect_amd() -> dict:
    """Try rocm-smi / vulkaninfo for AMD."""
    rc, out, _ = run(["rocm-smi", "--csv"])
    if rc == 0 and out.strip():
        return {"present": True, "backend": "rocm", "raw": out.strip()[:500]}
    return {"present": False}


def detect_vulkan() -> dict:
    """Try vulkaninfo. Returns whether any Vulkan device is present."""
    rc, out, _ = run(["vulkaninfo", "--summary"], timeout=10)
    if rc != 0 or not out.strip():
        return {"present": False}
    # crude: look for "deviceName"
    return {"present": "deviceName" in out, "raw": out.strip()[:500]}


def detect_all_gpus() -> dict:
    """Detect all GPU backends in priority order."""
    nv = detect_nvidia()
    if nv["present"]:
        nv["backend"] = STATUS_CUDA
    amd = detect_amd()
    vk = detect_vulkan()
    if vk["present"]:
        vk["backend"] = STATUS_VULKAN
    # Pick primary
    if nv["present"]:
        primary = nv["backend"]
    elif amd["present"]:
        primary = STATUS_HIP
    elif vk["present"]:
        primary = vk["backend"]
    else:
        primary = STATUS_CPU
    return {
        "primary": primary,
        "nvidia": nv,
        "amd": amd,
        "vulkan": vk,
    }


# ---- component checks ----

def has_cudart() -> bool:
    """Check if cudart64_12.dll is accessible (system PATH or runtime/)."""
    candidates = [
        RUNTIME / "cudart64_12.dll",
        RUNTIME / "cublas64_12.dll",
    ]
    for c in candidates:
        if c.exists():
            return True
    # system PATH
    if shutil.which("cudart64_12.dll"):
        return True
    return False


def has_cuda_runtime() -> bool:
    """Stronger check: do we have the DLLs b9538 actually needs to run?"""
    # b9538 with cuda-12.4 zip needs cublas64_12.dll, cudart64_12.dll, etc.
    needed = ["cublas64_12.dll", "cudart64_12.dll", "cublasLt64_12.dll"]
    for n in needed:
        p = RUNTIME / n
        if not p.exists():
            return False
    return True


# ---- gopeed-web bridge ----

def gopeed_alive() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(GOPEED_BASE + "/api/v1/tasks", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_gopeed_web() -> bool:
    """Make sure gopeed-web.exe is running on :9999. Spawn if not."""
    if gopeed_alive():
        return True
    exe = RUNTIME / "gopeed-web.exe"
    if not exe.exists():
        print(f"[firstrun] gopeed-web.exe not found at {exe}")
        return False
    print(f"[firstrun] starting gopeed-web (port 9999)...")
    log = HERMES_ROOT / "hermes" / "data" / "logs" / "gopeed-web.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        subprocess.Popen(
            [str(exe), "-A", "127.0.0.1", "-P", "9999"],
            stdout=f, stderr=subprocess.STDOUT, close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    # wait up to 10s for HTTP API to come up
    for _ in range(20):
        time.sleep(0.5)
        if gopeed_alive():
            print(f"[firstrun] gopeed-web up")
            return True
    print(f"[firstrun] gopeed-web failed to start within 10s")
    return False


def gopeed_create(url: str, out_dir: Path, name: Optional[str] = None) -> Optional[str]:
    """Create a gopeed-web download task. Returns task id or None.

    gopeed-web API format:
        POST /api/v1/tasks
        body = {"req": {"url": ...}, "opts": {"path": dir, "name": name?, "extra": {connections}}}
    response: {"code":0, "data": "<task_id>"}
    """
    import urllib.request, json
    body: dict = {"req": {"url": url}, "opts": {"path": str(out_dir)}}
    if name:
        body["opts"]["name"] = name
    req = urllib.request.Request(GOPEED_BASE + "/api/v1/tasks",
                                  data=json.dumps(body).encode("utf-8"),
                                  method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
            if data.get("code") == 0:
                task_id = data.get("data")
                if isinstance(task_id, str):
                    return task_id
                # older API: data is a dict with .id
                if isinstance(task_id, dict):
                    return task_id.get("id")
    except Exception as e:
        print(f"[firstrun] gopeed POST failed: {e}")
    return None


def gopeed_wait(task_id: str, timeout: float = 1800.0,
                poll: float = 10.0) -> str:
    """Poll a gopeed task until terminal. Returns final state.

    gopeed-web task object: status is at top-level, progress is at top-level
    (not under meta). opts is under meta.opts.
    """
    import urllib.request, json
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{GOPEED_BASE}/api/v1/tasks/{task_id}", timeout=5) as r:
                t = json.loads(r.read().decode("utf-8")).get("data", {})
        except Exception:
            time.sleep(poll)
            continue
        state = (t.get("status") or "").lower()
        prog = t.get("progress", {})
        if state in ("done", "succeed", "success"):
            return "done"
        if state in ("error", "failed", "canceled", "cancelled"):
            return state
        # still running, report progress every 30 polls
        if int(time.time()) % 30 == 0:
            pct = (prog.get("downloaded", 0) / max(1, prog.get("used", 1)) * 100) if prog.get("used") else 0
            print(f"[firstrun] gopeed task {task_id}: {state} {pct:.1f}%")
        time.sleep(poll)
    return "timeout"


# ---- component installers ----

# URLs to download (sourced from llama.cpp b9538 release page, 2026-06-06)
CUDA_12_4_RUNTIME = "https://github.com/ggml-org/llama.cpp/releases/download/b9538/cudart-llama-bin-win-cuda-12.4-x64.zip"
VULKAN_RUNTIME = "https://github.com/ggml-org/llama.cpp/releases/download/b9538/llama-b9538-bin-win-vulkan-x64.zip"
ROCM_RUNTIME = "https://github.com/ggml-org/llama.cpp/releases/download/b9538/llama-b9538-bin-win-hip-radeon-x64.zip"


def install_cudart(force: bool = False) -> bool:
    """Download cudart zip via gopeed-web, extract to runtime/."""
    if has_cuda_runtime() and not force:
        print(f"[firstrun] cudart/cublas already present, skip")
        return True
    if not ensure_gopeed_web():
        print(f"[firstrun] cannot install cudart: gopeed-web unavailable")
        return False
    print(f"[firstrun] downloading cudart zip (391MB) via gopeed-web...")
    task = gopeed_create(CUDA_12_4_RUNTIME, RUNTIME)
    if not task:
        return False
    final = gopeed_wait(task)
    if final != "done":
        print(f"[firstrun] cudart download ended in {final}")
        return False
    # find downloaded zip
    zips = list(RUNTIME.glob("cudart*.zip"))
    if not zips:
        print(f"[firstrun] cudart zip not found after download")
        return False
    z = zips[0]
    print(f"[firstrun] extracting {z.name}...")
    import zipfile
    with zipfile.ZipFile(z) as zf:
        zf.extractall(RUNTIME)
    z.unlink()
    print(f"[firstrun] cudart installed, {len(list(RUNTIME.glob('*cudart*.dll')))} DLLs")
    return True


# ---- public entry points ----

def cmd_status() -> int:
    gpus = detect_all_gpus()
    cuda_rt = has_cuda_runtime()
    print()
    print("=" * 60)
    print("  Hermes - GPU / Runtime Status")
    print("=" * 60)
    print(f"  Primary backend:    {gpus['primary'].upper()}")
    if gpus["nvidia"]["present"]:
        nv = gpus["nvidia"]
        print(f"  NVIDIA GPU:         {nv['name']}  ({nv['vram_mb']}MB VRAM, driver {nv['driver']})")
    else:
        print(f"  NVIDIA GPU:         not detected")
    if gpus["amd"]["present"]:
        print(f"  AMD GPU:            present (ROCm)")
    if gpus["vulkan"]["present"]:
        print(f"  Vulkan device:      present")
    print(f"  cudart/cublas:      {'YES' if cuda_rt else 'NO  (will fall back to CPU or download)'}")
    print(f"  gopeed-web (:9999): {'YES' if gopeed_alive() else 'NO'}")
    print(f"  runtime path:       {RUNTIME}")
    print()
    return 0


def cmd_check() -> int:
    """Non-destructive: report what would be needed. Exit 0=ok, 1=cpu-fallback, 2=err."""
    gpus = detect_all_gpus()
    if gpus["primary"] == STATUS_CPU:
        print("[check] no GPU detected - will run pure CPU")
        return 1
    if gpus["primary"] == STATUS_CUDA and not has_cuda_runtime():
        print("[check] CUDA detected but cudart/cublas missing - need to download")
        return 1  # not an error, but flag for user
    print(f"[check] OK: {gpus['primary'].upper()} ready")
    return 0


def cmd_install() -> int:
    """Run environment bootstrap. Downloads missing GPU runtime."""
    print("=" * 60)
    print("  Hermes - First-run Environment Bootstrap")
    print("=" * 60)
    gpus = detect_all_gpus()
    print(f"[install] primary backend: {gpus['primary'].upper()}")
    if gpus["primary"] == STATUS_CPU:
        print("[install] no GPU detected, will run pure CPU")
        return 0  # not an error
    if gpus["primary"] == STATUS_CUDA:
        if has_cuda_runtime():
            print("[install] cudart/cublas already present")
            return 0
        print("[install] CUDA detected but runtime missing - downloading via gopeed-web...")
        ok = install_cudart()
        if not ok:
            print("[install] WARN: cudart download failed. Falling back to CPU.")
            return 1
        print("[install] OK: cudart installed")
        return 0
    if gpus["primary"] == STATUS_VULKAN:
        # vulkan runtime is in OS, not bundled - just check
        print("[install] Vulkan runtime must be installed on OS (e.g. vulkan-1.dll)")
        print("[install] Hermes bundles llama-server-vulkan.exe - no extra download needed")
        return 0
    return 0


def main():
    if len(sys.argv) < 2:
        cmd_status()
        return 0
    cmd = sys.argv[1]
    if cmd == "status":
        return cmd_status()
    if cmd == "check":
        return cmd_check()
    if cmd == "install":
        return cmd_install()
    print(f"usage: {sys.argv[0]} [status|check|install]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
