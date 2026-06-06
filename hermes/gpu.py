"""
GPU detection and acceleration for Hermes.

Detects available GPUs (NVIDIA, AMD, Intel) and provides info
for auto-selecting the right llama.cpp binary (CPU / CUDA / Vulkan).
"""
from __future__ import annotations
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes.gpu")


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
        # Try vulkaninfo
        r = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and "deviceName" in r.stdout.lower():
            return {"backend": "vulkan", "available": True, "info": r.stdout[:500]}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try checking for vulkan DLLs in system
    vulkan_dll_paths = [
        Path("C:/Windows/System32/vulkan-1.dll"),
    ]
    if any(p.exists() for p in vulkan_dll_paths):
        return {"backend": "vulkan", "available": True, "info": "vulkan-1.dll found in System32"}
    return None


def detect_wmi() -> dict:
    """Fallback: detect GPU via WMI (Windows)."""
    try:
        import subprocess
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-WmiObject Win32_VideoController | Select-Object Name, AdapterRAM, VideoProcessor | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            try:
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
            except json.JSONDecodeError:
                pass
    except Exception as e:
        logger.debug(f"WMI detection failed: {e}")
    return {"backend": "none", "available": False, "gpus": []}


def detect_gpu() -> dict:
    """
    Detect all available GPUs. Returns:
    {
        "primary": "nvidia" | "amd" | "intel" | "none",
        "backend": "cuda" | "vulkan" | "cpu",
        "nvidia": {...} | None,
        "vulkan": {...} | None,
        "wmi": {...},
        "best_gpu": {...} | None,
        "recommendation": "...",
    }
    """
    result = {
        "primary": "none",
        "backend": "cpu",
        "nvidia": None,
        "vulkan": None,
        "wmi": None,
        "best_gpu": None,
        "recommendation": "No GPU detected. Will use CPU inference.",
    }

    # Try NVIDIA first (best CUDA support)
    nv = detect_nvidia_smi()
    if nv and nv["available"]:
        result["nvidia"] = nv
        result["primary"] = "nvidia"
        result["backend"] = "cuda"
        result["best_gpu"] = nv["gpus"][0]
        result["recommendation"] = (
            f"NVIDIA GPU detected: {nv['gpus'][0]['name']} "
            f"({nv['gpus'][0]['vram_total_mb']}MB VRAM). "
            f"Use CUDA build of llama.cpp for best performance."
        )
        return result

    # Try Vulkan
    vk = detect_vulkan()
    if vk and vk["available"]:
        result["vulkan"] = vk
        result["backend"] = "vulkan"
        result["recommendation"] = (
            "Vulkan runtime detected. Use Vulkan build of llama.cpp for GPU acceleration."
        )
        return result

    # Fallback to WMI (basic detection)
    wmi = detect_wmi()
    result["wmi"] = wmi
    if wmi["available"]:
        result["best_gpu"] = wmi["gpus"][0]
        result["recommendation"] = (
            f"GPU detected: {wmi['gpus'][0]['name']} "
            f"({wmi['gpus'][0]['vram_total_mb']}MB VRAM). "
            f"For best acceleration, install Vulkan or CUDA runtime and matching llama.cpp build."
        )
    return result


def recommend_llamacpp_binary(runtime_dir: Path) -> Path | None:
    """
    Find the best llama.cpp binary based on detected GPU.

    Looks in runtime_dir for:
      - llama-server.exe (CPU)
      - llama-server-cuda.exe (CUDA)
      - llama-server-vulkan.exe (Vulkan)
    """
    gpu = detect_gpu()
    backend = gpu["backend"]
    logger.info(f"GPU detection: {gpu['recommendation']}")

    candidates = []
    if backend == "cuda":
        # Prefer CUDA
        candidates = [
            runtime_dir / "llama-server-cuda.exe",
            runtime_dir / "llama-server.exe",  # Fallback to CPU
        ]
    elif backend == "vulkan":
        candidates = [
            runtime_dir / "llama-server-vulkan.exe",
            runtime_dir / "llama-server.exe",
        ]
    else:
        candidates = [runtime_dir / "llama-server.exe"]

    for c in candidates:
        if c.exists():
            return c
    return None


def recommend_gpu_layers() -> int:
    """Recommend --n-gpu-layers based on VRAM."""
    gpu = detect_gpu()
    if not gpu["best_gpu"]:
        return 0
    vram = gpu["best_gpu"].get("vram_total_mb", 0)
    if vram >= 8000:
        return 99  # Full offload for 8GB+
    elif vram >= 6000:
        return 35  # ~3B model fully offloaded
    elif vram >= 4000:
        return 20  # Partial offload
    else:
        return 0  # CPU only


# ---- CLI usage ----

def main():
    import sys
    gpu = detect_gpu()
    print(json.dumps(gpu, indent=2))
    return 0 if gpu["best_gpu"] else 1


if __name__ == "__main__":
    sys.exit(main())
