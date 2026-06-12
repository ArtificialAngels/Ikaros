"""
Hermes - Unified GPU detection and runtime bootstrap.

Merged from hermes/gpu.py + hermes/firstrun.py (GPU detection parts).
Detects NVIDIA / AMD / Intel / Vulkan GPUs and recommends llama.cpp binary.
Also handles CUDA runtime installation via pip.

Multi-version CUDA support (Phase 8):
  - runtime/cuda/11.8/   -- legacy drivers (470.x-525.x)
  - runtime/cuda/12.4/   -- modern drivers (525.x-555.x) [bundled]
  - runtime/cuda/13.0/   -- newest drivers (555.x+) [download on demand]

The bootstrapper picks the correct version based on nvidia-smi driver version
and auto-downloads the matching pip packages if missing.
"""
from __future__ import annotations
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("hermes.gpu_detect")

HERMES_ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME = HERMES_ROOT / "runtime"
CUDA_BASE = RUNTIME / "cuda"

STATUS_CPU = "cpu"
STATUS_CUDA = "cuda"
STATUS_VULKAN = "vulkan"
STATUS_HIP = "hip"

# CUDA versions supported by this bootstrapper, in preference order
# (newest first). Each entry: (dir_name, pip_tag, binary_basename, min_driver).
CUDA_VERSIONS: list[tuple[str, str, str, str]] = [
    ("13.0", "cu126", "llama-server-cuda-13.0.exe", "555.0"),
    ("12.4", "cu124", "llama-server-cuda-12.4.exe", "525.0"),
    ("11.8", "cu118", "llama-server-cuda-11.8.exe", "470.0"),
]  # pip tag 'cu126' = CUDA 12.6 runtime which is what NVIDIA ships for CUDA 13.0.


# ---- subprocess helpers ----

def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
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

def detect_nvidia_smi() -> dict | None:
    """Try nvidia-smi. Returns GPU info dict or None."""
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0:
            return None
        gpus = []
        for line in r.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                gpus.append({
                    "vendor": "nvidia",
                    "name": parts[0],
                    "vram_total_mb": int(parts[1]) if parts[1].isdigit() else 0,
                    "vram_free_mb": int(parts[2]) if parts[2].isdigit() else 0,
                    "driver": parts[3],
                    "compute_cap": parts[4],
                })
        return {"backend": "cuda", "available": len(gpus) > 0, "gpus": gpus}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception as e:
        logger.debug(f"nvidia-smi failed: {e}")
        return None


def detect_vulkan() -> dict | None:
    """Check for Vulkan runtime (works on any GPU)."""
    try:
        r = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and "deviceName" in r.stdout.lower():
            return {"backend": "vulkan", "available": True, "info": r.stdout[:500]}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    vulkan_dll_paths = [Path("C:/Windows/System32/vulkan-1.dll")]
    if any(p.exists() for p in vulkan_dll_paths):
        return {"backend": "vulkan", "available": True, "info": "vulkan-1.dll found in System32"}
    return None


def detect_amd() -> dict:
    """Try rocm-smi for AMD."""
    rc, out, _ = _run(["rocm-smi", "--csv"])
    if rc == 0 and out.strip():
        return {"present": True, "backend": "rocm", "raw": out.strip()[:500]}
    return {"present": False}


def detect_wmi() -> dict:
    """Fallback: detect GPU via WMI (Windows)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-WmiObject Win32_VideoController | Select-Object Name, AdapterRAM, VideoProcessor | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            if isinstance(data, dict):
                data = [data]
            gpus = []
            for g in data:
                gpus.append({
                    "vendor": "unknown",
                    "name": g.get("Name", "?"),
                    "vram_total_mb": int(g.get("AdapterRAM", 0)) // (1024*1024),
                })
            return {"backend": "wmi", "available": len(gpus) > 0, "gpus": gpus}
    except Exception as e:
        logger.debug(f"WMI detection failed: {e}")
    return {"backend": "none", "available": False, "gpus": []}


def detect_all_gpus() -> dict:
    """Detect all GPU backends in priority order. Returns unified result."""
    result = {
        "primary": STATUS_CPU,
        "backend": "cpu",
        "nvidia": None,
        "amd": None,
        "vulkan": None,
        "wmi": None,
        "best_gpu": None,
        "recommendation": "No GPU detected. Will use CPU inference.",
    }

    # Try NVIDIA first
    nv = detect_nvidia_smi()
    if nv and nv["available"]:
        result["nvidia"] = nv
        result["primary"] = STATUS_CUDA
        result["backend"] = "cuda"
        result["best_gpu"] = nv["gpus"][0]
        result["recommendation"] = (
            f"NVIDIA GPU: {nv['gpus'][0]['name']} "
            f"({nv['gpus'][0]['vram_total_mb']}MB VRAM). Use CUDA build."
        )
        return result

    # Try AMD
    amd = detect_amd()
    result["amd"] = amd
    if amd["present"]:
        result["primary"] = STATUS_HIP
        result["backend"] = "hip"
        result["recommendation"] = "AMD GPU detected. Use ROCm build."
        return result

    # Try Vulkan
    vk = detect_vulkan()
    if vk and vk["available"]:
        result["vulkan"] = vk
        result["backend"] = "vulkan"
        result["recommendation"] = "Vulkan runtime detected. Use Vulkan build."
        return result

    # Fallback WMI
    wmi = detect_wmi()
    result["wmi"] = wmi
    if wmi["available"]:
        result["best_gpu"] = wmi["gpus"][0]
        result["recommendation"] = (
            f"GPU detected: {wmi['gpus'][0]['name']} "
            f"({wmi['gpus'][0]['vram_total_mb']}MB VRAM). "
            f"Install Vulkan or CUDA runtime for acceleration."
        )
    return result


# ---- CUDA multi-version detection ----

def detect_driver_version() -> Optional[str]:
    """Return installed NVIDIA driver version (e.g. '555.85') or None."""
    rc, out, _ = _run(["nvidia-smi", "--query-gpu=driver_version",
                        "--format=csv,noheader"])
    if rc == 0 and out.strip():
        # Multiple GPUs => take the first driver version reported.
        return out.strip().split("\n")[0].strip()
    return None


def parse_driver_major(driver_str: str) -> Optional[float]:
    """Parse the major.minor from '555.85.05' / '555.85' / '555'."""
    try:
        parts = driver_str.split(".")
        return float(f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else parts[0])
    except (ValueError, IndexError):
        return None


def driver_to_cuda_version(driver_str: Optional[str]) -> Optional[str]:
    """Map a driver version to the highest compatible CUDA version string.

    Reference (NVIDIA CUDA compatibility table):
      >= 555.x  -> CUDA 13.0
      >= 525.x  -> CUDA 12.4
      >= 470.x  -> CUDA 11.8
      >= 450.x  -> CUDA 11.0  (legacy, no built-in runtime)
      <  450.x  -> None        (driver too old; user must upgrade)

    Returns None when the driver is too old to be supported.
    """
    major = parse_driver_major(driver_str) if driver_str else None
    if major is None:
        return None
    if major >= 555.0:
        return "13.0"
    if major >= 525.0:
        return "12.4"
    if major >= 470.0:
        return "11.8"
    if major >= 450.0:
        return "11.0"
    return None


def cuda_dir(version: str) -> Path:
    """Return the runtime/cuda/<version> directory."""
    return CUDA_BASE / version


def cuda_dll_list(version: str) -> list[str]:
    """Return the list of DLL basenames that must be present in runtime/cuda/<version>/."""
    # The DLL basenames differ between CUDA 11 and 12/13.
    if version.startswith("11"):
        return ["cudart64_110.dll", "cublas64_11.dll", "cublasLt64_11.dll"]
    # 12.x and 13.0 both use the _12 DLL naming per NVIDIA convention.
    return ["cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"]


def has_cuda_runtime_version(version: str) -> bool:
    """Check whether runtime/cuda/<version>/ has the required DLLs."""
    d = cuda_dir(version)
    if not d.is_dir():
        return False
    return all((d / dll).exists() for dll in cuda_dll_list(version))


def list_available_cuda_versions() -> list[str]:
    """Return CUDA versions that have a complete runtime installed on disk."""
    return [v for v, *_ in CUDA_VERSIONS if has_cuda_runtime_version(v)]


def recommend_cuda_version() -> str:
    """Pick the best CUDA version based on driver + available runtimes.

    Returns one of:
      - "13.0" / "12.4" / "11.8" -- a usable CUDA version
      - "cpu"  -- no GPU detected or driver too old; CPU fallback
    """
    nv = detect_nvidia_smi()
    if not nv or not nv["available"]:
        return "cpu"
    driver = nv["gpus"][0].get("driver")
    preferred = driver_to_cuda_version(driver)
    available = list_available_cuda_versions()

    if preferred and preferred in available:
        return preferred
    # Fall back to the newest available runtime that's compatible.
    # CUDA 13 runtime DLLs (_12) can usually satisfy 12.4 builds too;
    # however, the llama-server binary is the binding constraint, so we
    # only fall back within the supported version list.
    for v, *_ in CUDA_VERSIONS:
        if v in available:
            # Only use a version whose minimum driver we actually meet.
            min_driver = next(t[3] for t in CUDA_VERSIONS if t[0] == v)
            if driver and parse_driver_major(driver) and \
               parse_driver_major(driver) >= parse_driver_major(min_driver):
                return v
    return "cpu"


# ---- Legacy single-version CUDA runtime checks (kept for back-compat) ----

def has_cudart() -> bool:
    """Legacy: check if cudart64_12.dll or cublas64_12.dll is in the runtime root."""
    candidates = [RUNTIME / "cudart64_12.dll", RUNTIME / "cublas64_12.dll"]
    for c in candidates:
        if c.exists():
            return True
    # Also check the multi-version dirs.
    for v in list_available_cuda_versions():
        for dll in cuda_dll_list(v):
            if (cuda_dir(v) / dll).exists():
                return True
    return False


def has_cuda_runtime() -> bool:
    """Stronger check: do we have the DLLs llama.cpp actually needs?

    Checks both the legacy runtime/ location and runtime/cuda/<ver>/ locations.
    Returns True if ANY version's runtime is complete.
    """
    # Legacy location.
    legacy_needed = ["cublas64_12.dll", "cudart64_12.dll", "cublasLt64_12.dll"]
    if all((RUNTIME / n).exists() for n in legacy_needed):
        return True
    # Multi-version location.
    return bool(list_available_cuda_versions())


def install_cudart(force: bool = False, cuda_version: str = "12.4") -> bool:
    """Install CUDA runtime DLLs via pip (~100MB download).

    Default: CUDA 12.4 (back-compat). Pass cuda_version="11.8" or "13.0" for
    the multi-version runtime locations under runtime/cuda/<ver>/.
    """
    # If a multi-version CUDA dir already has a complete runtime, skip.
    if has_cuda_runtime_version(cuda_version) and not force:
        logger.info(f"runtime/cuda/{cuda_version}/ already complete, skip")
        return True

    target = cuda_dir(cuda_version) if cuda_version else RUNTIME
    target.mkdir(parents=True, exist_ok=True)

    # Map version -> pip package suffix.
    pip_tag = next((t for v, t, *_ in CUDA_VERSIONS if v == cuda_version), None)
    if pip_tag is None:
        logger.error(f"Unknown CUDA version: {cuda_version}")
        return False
    pkg_runtime = f"nvidia-cuda-runtime-{pip_tag}"
    pkg_cublas = f"nvidia-cublas-{pip_tag}"

    logger.info(f"Installing CUDA {cuda_version} runtime ({pkg_runtime}) via pip...")
    python = str(HERMES_ROOT / "portable-python" / "python.exe")
    pip_args = [python, "-m", "pip", "install", "-q"]

    # Check for mirror config
    try:
        from modules.model_manager.mirror import get_mirror_config
        mirror_cfg = get_mirror_config()
        if mirror_cfg.mirror_pypi:
            index_url = mirror_cfg.get_pypi_index_url()
            pip_args.extend(["--index-url", index_url])
            logger.info(f"Using PyPI mirror: {index_url}")
    except Exception:
        pass

    pip_args.extend([
        pkg_runtime, pkg_cublas,
        "--target", str(target), "--upgrade", "--no-deps",
    ])

    try:
        rc = subprocess.run(pip_args, capture_output=True, text=True, timeout=600)
        if rc.returncode != 0:
            logger.error(f"pip install failed: {rc.stderr}")
            return False
    except Exception as e:
        logger.error(f"pip install exception: {e}")
        return False

    # Copy DLLs from nvidia subdirs into the runtime/cuda/<ver>/ folder.
    copied = 0
    for dll_path in target.glob("nvidia/*/bin/*.dll"):
        dest = target / dll_path.name
        if not dest.exists() or force:
            shutil.copy2(dll_path, dest)
            copied += 1
    logger.info(f"Copied {copied} CUDA {cuda_version} runtime DLLs to {target}")
    return has_cuda_runtime_version(cuda_version)


# ---- llama.cpp binary recommendation ----

def find_llamacpp_binary(cuda_version: str | None = None) -> Path | None:
    """Find the best llama-server binary for the given CUDA version.

    Lookup order:
      1. runtime/cuda/<ver>/llama-server-cuda-<ver>.exe
      2. runtime/cuda/<ver>/llama-server.exe (version-pinned CPU fallback)
      3. runtime/llama-server-cuda-<ver>.exe (legacy location)
      4. runtime/llama-server.exe (generic CPU binary)

    Returns the first existing match, or None if nothing is found.
    """
    candidates: list[Path] = []
    if cuda_version and cuda_version != "cpu":
        cv_dir = cuda_dir(cuda_version)
        binary_name = next(
            (b for v, _, b, _ in CUDA_VERSIONS if v == cuda_version),
            f"llama-server-cuda-{cuda_version}.exe",
        )
        candidates.append(cv_dir / binary_name)
        candidates.append(cv_dir / "llama-server.exe")
        candidates.append(RUNTIME / binary_name)
    candidates.append(RUNTIME / "llama-server.exe")
    for c in candidates:
        if c.exists():
            return c
    return None


def recommend_llamacpp_binary() -> Path | None:
    """Find the best llama-server binary based on detected GPU + driver."""
    gpu = detect_all_gpus()
    backend = gpu["backend"]
    logger.info(f"GPU detection: {gpu['recommendation']}")

    if backend == "cuda":
        cuda_v = recommend_cuda_version()
        bin_path = find_llamacpp_binary(cuda_v)
        if bin_path:
            logger.info(f"Selected CUDA {cuda_v} binary: {bin_path}")
            return bin_path
    elif backend == "vulkan":
        b = RUNTIME / "llama-server-vulkan.exe"
        if b.exists():
            return b
    else:
        # CPU-only fallback
        b = RUNTIME / "llama-server.exe"
        if b.exists():
            return b
    return None


def recommend_gpu_layers() -> int:
    """Recommend --n-gpu-layers based on VRAM."""
    gpu = detect_all_gpus()
    if not gpu["best_gpu"]:
        return 0
    vram = gpu["best_gpu"].get("vram_total_mb", 0)
    if vram >= 8000:
        return 99
    elif vram >= 6000:
        return 35
    elif vram >= 4000:
        return 20
    else:
        return 0


# ---- CLI ----

def main():
    if len(sys.argv) < 2:
        gpu = detect_all_gpus()
        print(json.dumps(gpu, indent=2))
        return 0 if gpu["best_gpu"] else 1
    cmd = sys.argv[1]

    # Sub-command helper: --cuda <ver> for commands that take a version.
    cuda_arg = None
    args_tail = sys.argv[2:]
    if "--cuda" in args_tail:
        idx = args_tail.index("--cuda")
        if idx + 1 < len(args_tail):
            cuda_arg = args_tail[idx + 1]
            args_tail = args_tail[:idx] + args_tail[idx + 2:]

    if cmd == "status":
        gpu = detect_all_gpus()
        cuda_rt = has_cuda_runtime()
        driver = detect_driver_version()
        cuda_v = recommend_cuda_version()
        print("=" * 60)
        print("  Hermes - GPU / Runtime Status")
        print("=" * 60)
        print(f"  Primary backend:    {gpu['primary'].upper()}")
        if gpu["nvidia"] and gpu["nvidia"]["available"]:
            nv = gpu["nvidia"]["gpus"][0]
            print(f"  NVIDIA GPU:         {nv['name']}  ({nv['vram_total_mb']}MB VRAM)")
        else:
            print("  NVIDIA GPU:         not detected")
        print(f"  Driver version:     {driver or 'unknown'}")
        print(f"  Recommended CUDA:   {cuda_v}")
        print(f"  CUDA runtime:       {'YES' if cuda_rt else 'NO'}")
        available = list_available_cuda_versions()
        print(f"  Available runtimes: {available or '(none)'}")
        print(f"  runtime path:       {RUNTIME}")
        print()
        return 0
    if cmd == "check":
        gpu = detect_all_gpus()
        if gpu["primary"] == STATUS_CPU:
            print("[check] no GPU detected - will run pure CPU")
            return 1
        if gpu["primary"] == STATUS_CUDA:
            cuda_v = recommend_cuda_version()
            if cuda_v == "cpu":
                print("[check] CUDA detected but driver too old / no compatible runtime")
                return 1
            if not has_cuda_runtime_version(cuda_v):
                print(f"[check] CUDA {cuda_v} detected but runtime DLLs missing")
                print(f"[check]   run: python -m modules.env_bootstrap.gpu_detect install --cuda {cuda_v}")
                return 1
            print(f"[check] OK: CUDA {cuda_v} ready")
            return 0
        print(f"[check] OK: {gpu['primary'].upper()} ready")
        return 0
    if cmd == "install":
        if cuda_arg:
            if not has_cuda_runtime_version(cuda_arg):
                ok = install_cudart(cuda_version=cuda_arg)
                return 0 if ok else 1
            print(f"[install] runtime/cuda/{cuda_arg}/ already complete")
            return 0
        gpu = detect_all_gpus()
        print(f"[install] primary backend: {gpu['primary'].upper()}")
        if gpu["primary"] == STATUS_CPU:
            print("[install] no GPU detected, will run pure CPU")
            return 0
        if gpu["primary"] == STATUS_CUDA:
            cuda_v = recommend_cuda_version()
            if cuda_v == "cpu":
                print("[install] CUDA detected but driver too old for any bundled runtime")
                print("[install]   please update NVIDIA driver")
                return 1
            if has_cuda_runtime_version(cuda_v):
                print(f"[install] runtime/cuda/{cuda_v}/ already complete")
                return 0
            ok = install_cudart(cuda_version=cuda_v)
            return 0 if ok else 1
        return 0
    if cmd == "recommend":
        # Print the recommended CUDA version to stdout (one line).
        # start.ps1 captures this to pick the right runtime dir.
        cuda_v = recommend_cuda_version()
        print(cuda_v)
        return 0
    print(f"usage: {sys.argv[0]} [status|check|install|recommend] [--cuda <11.8|12.4|13.0>]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
