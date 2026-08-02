#!/usr/bin/env python3
"""llama.cpp 二进制版本解析器 — 按设备 CUDA 能力自动选择 llama-server。

背景
----
llama.cpp CUDA build 的 cudart/cublas DLL 与驱动版本强绑定：
CUDA 13.x build 在只支持 CUDA 12.4 的驱动上无法加载，反之亦然。
runtime/llama/ 下可并存多个版本目录（见 docs/ARCHITECTURE.md）：

    b10000-cuda         CUDA 13.x build（主力，随 ikaros-env 默认）
    b10000-cuda-12.4    CUDA 12.4 build（老驱动设备用）
    <任意目录>           用户手动放置的其它 build

选择优先级（高→低）：
  1. 环境变量 IKAROS_LLAMA_VERSION（显式指定目录名，最优先）
  2. 环境变量 IKAROS_LLAMA_SERVER / IKAROS_LLAMA_DIR（已显式指定二进制/目录）
  3. nvidia-smi 探测驱动支持的 CUDA 版本 → 匹配目录
  4. 扫描 runtime/llama/ 下可用目录
  5. CPU 兜底：无 GPU / CUDA < 12 → 返回可用目录 + cpu_fallback=True
     （调用方应追加 -ngl 0；纯 CPU build 可由用户放置后自动命中扫描）

零第三方依赖（stdlib only），watchdog / dashboard / model_config 均可 import。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 已知 CUDA 版本 → 目录名 映射（13.x 默认 b10000-cuda；12.x 用 -12.4 变体）
_CUDA13_DIRS = ("b10000-cuda",)
_CUDA12_DIRS = ("b10000-cuda-12.4", "b10000-cuda")

# 纯 CPU build 目录名提示（用户放置后自动命中扫描；缺失时用 -ngl 0 兜底）
_CPU_DIR_HINTS = ("cpu", "cpu-x64", "b10000-cpu")


def detect_cuda_version() -> Optional[str]:
    """nvidia-smi 探测驱动支持的最高 CUDA runtime 版本（如 '12.4'）。

    返回 None 表示无 NVIDIA GPU 或 nvidia-smi 不可用。
    """
    try:
        raw = subprocess.run(
            ["nvidia-smi"],
            capture_output=True, timeout=10,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
    except Exception:
        return None
    if not raw:
        return None
    # 中文 Windows 下 nvidia-smi 输出可能是 GBK；utf-8/gbk 双编码容错
    try:
        out = raw.decode("utf-8")
    except UnicodeDecodeError:
        out = raw.decode("gbk", errors="replace")
    # 旧格式: "CUDA Version: 12.4"；新驱动: "CUDA UMD Version: 13.3"
    m = re.search(r"CUDA (?:UMD )?Version:\s*(\d+)\.(\d+)", out)
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _llama_root(root: Path) -> Path:
    return root / "runtime" / "llama"


def _has_server(d: Path) -> bool:
    return (d / "llama-server.exe").exists()


def resolve_llama_dir(root: Path | None = None) -> dict:
    """解析 llama 目录，返回 {dir, version, cuda, cpu_fallback, reason}。

    root 缺省时按 ikaros 约定探测（环境变量 IKAROS_ROOT/HERMES_ROOT →
    脚本位置推导）。
    """
    if root is None:
        root = _detect_ikaros_root()

    llama_root = _llama_root(root)
    if not llama_root.is_dir():
        return {
            "dir": llama_root / "b10000-cuda",
            "version": "b10000-cuda", "cuda": None,
            "cpu_fallback": True,
            "reason": f"runtime/llama 目录不存在: {llama_root}",
        }

    # ── 1. 显式环境变量：目录名 ──
    ver = os.environ.get("IKAROS_LLAMA_VERSION")
    if ver:
        d = llama_root / ver
        if _has_server(d):
            # env 指定也要结合设备能力判断 CPU 兜底（无 GPU / CUDA<12 时 -ngl 0）
            cuda = detect_cuda_version()
            try:
                cuda_major = int(cuda.split(".")[0]) if cuda else 0
            except (ValueError, IndexError):
                cuda_major = 0
            return {"dir": d, "version": ver, "cuda": cuda,
                    "cpu_fallback": cuda_major < 12,
                    "reason": f"env:IKAROS_LLAMA_VERSION (cuda={cuda})"}
        return {"dir": d, "version": ver, "cuda": None,
                "cpu_fallback": True,
                "reason": f"IKAROS_LLAMA_VERSION={ver} 但目录缺少 llama-server.exe"}

    # ── 2. 显式环境变量：完整二进制路径 ──
    server_env = os.environ.get("IKAROS_LLAMA_SERVER")
    if server_env:
        p = Path(server_env)
        return {"dir": p.parent, "version": p.parent.name, "cuda": None,
                "cpu_fallback": not p.exists(),
                "reason": f"env:IKAROS_LLAMA_SERVER (exists={p.exists()})"}

    # ── 3. nvidia-smi 探测 ──
    cuda = detect_cuda_version()
    if cuda:
        try:
            major = int(cuda.split(".")[0])
        except (ValueError, IndexError):
            major = 0
        if major >= 13:
            for cand in _CUDA13_DIRS:
                d = llama_root / cand
                if _has_server(d):
                    return {"dir": d, "version": cand, "cuda": cuda,
                            "cpu_fallback": False,
                            "reason": f"cuda-{cuda} → {cand}"}
        elif major >= 12:
            for cand in _CUDA12_DIRS:
                d = llama_root / cand
                if _has_server(d):
                    return {"dir": d, "version": cand, "cuda": cuda,
                            "cpu_fallback": False,
                            "reason": f"cuda-{cuda} → {cand}"}
        # CUDA < 12：无匹配 build → 落扫描 + CPU 兜底

    # ── 4. 扫描可用目录 ──
    cuda_builds: list[Path] = []
    cpu_build: Path | None = None
    for d in sorted(llama_root.iterdir()):
        if not (d.is_dir() and _has_server(d)):
            continue
        name_low = d.name.lower()
        if any(h in name_low for h in _CPU_DIR_HINTS):
            if cpu_build is None:
                cpu_build = d
        else:
            cuda_builds.append(d)
    if cpu_build is not None:
        return {"dir": cpu_build, "version": cpu_build.name, "cuda": cuda,
                "cpu_fallback": False, "reason": f"scan-cpu:{cpu_build.name}"}
    if cuda_builds:
        # 无 CPU build：仅当设备 CUDA >= 12 时才可安全使用 CUDA build。
        # CUDA < 12 / 无 GPU → 返回错误，避免选一个必崩的 build。
        try:
            cuda_major = int(cuda.split(".")[0]) if cuda else 0
        except (ValueError, IndexError):
            cuda_major = 0
        if cuda_major >= 12:
            return {"dir": cuda_builds[0], "version": cuda_builds[0].name,
                    "cuda": cuda, "cpu_fallback": False,
                    "reason": f"scan:{cuda_builds[0].name} (cuda={cuda})"}
        return {"dir": cuda_builds[0], "version": cuda_builds[0].name,
                "cuda": cuda, "cpu_fallback": True,
                "reason": f"设备 CUDA={cuda or '无'}，无 CPU build 可安全使用；"
                          f"现有 {cuda_builds[0].name} 为 CUDA 12+ build 可能无法加载。"
                          f"请放置 CPU build（目录名含 'cpu'）或匹配 CUDA 的 build。"}

    # ── 5. 全部失败 → 报错（调用方应提示下载对应 zip）──
    return {"dir": llama_root / "b10000-cuda", "version": "b10000-cuda",
            "cuda": cuda, "cpu_fallback": True,
            "reason": "runtime/llama 下没有可用 llama-server.exe（需下载 "
                      "llama-b10000-bin-win-cuda-{12.4|13.3}-x64.zip 并解压）"}


def _detect_ikaros_root() -> Path:
    """轻量探测 Ikaros 根（与 ikaros_paths._detect_root 同思路，零依赖）。"""
    env_root = os.environ.get("IKAROS_ROOT") or os.environ.get("HERMES_ROOT")
    if env_root:
        c = Path(env_root)
        if (c / "runtime" / "llama").is_dir():
            return c
    script_dir = Path(__file__).resolve().parent  # core/env/
    for parent in [script_dir, script_dir.parent, script_dir.parent.parent]:
        if (parent / "runtime" / "llama").is_dir():
            return parent
    return Path.cwd()


def main() -> None:
    import json
    res = resolve_llama_dir()
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
