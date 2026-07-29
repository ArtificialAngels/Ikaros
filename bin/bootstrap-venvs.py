#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bin/bootstrap-venvs.py  —  Ikaros Python 环境引导工具（仅脚手架 / 可重复运行）

本脚本用于记录并（按需）重建 Ikaros 使用的三个 Python 环境，目标是
提升依赖可复现性（dependency reproducibility）。

三个 Python 环境：
  1. 托管 Python (managed python)
     - 路径: %USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12（动态解析，不再写死用户名）
     - 角色: WorkBuddy 自带的嵌入式 Python，用于运行本仓库的辅助脚本。
             注意：路径不含 python.exe，脚本会自动补上。
  2. Hermes venv
     - 优先: core/hermes/venv
     - 回退: core/hermes/venv（防止 core/hermes 尚未完成迁移时找不到）
     - 角色: 承载 hermes dashboard / fastapi 服务的独立虚拟环境。
     - !! 关键固定依赖: pydantic-core==2.46.4
        执行 `pip install -U` 或重建 venv 时，如果没有精确固定此版本，
        hermes dashboard 在 `import fastapi` 时会因 pydantic-core 不兼容而崩溃。
  3. 运行时 Python (runtime python)
     - 路径: runtime/portable-python/python.exe（若存在）
     - 角色: 运行中实际拉起模型服务（llama.cpp 等）的 portable 解释器。

本脚本只做“只读说明 + 按需创建 hermes venv”，不会删除/修改任何正在运行的
服务代码，也不会强制重装已存在的依赖（除非 venv 缺失）。

用法:
    python bin/bootstrap-venvs.py            # 打印环境摘要，缺失则尝试创建
    python bin/bootstrap-venvs.py --force    # 强制重建 hermes venv（谨慎）
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# ---- 路径常量 ----------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent

# 1) 托管 Python（WorkBuddy 自带）
MANAGED_PYTHON_DIR = Path(
    os.path.expandvars(r"%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12")
)
MANAGED_PYTHON = MANAGED_PYTHON_DIR / "python.exe"

# 3) 运行时 portable Python（可选）
RUNTIME_PYTHON = REPO_ROOT / "runtime" / "portable-python" / "python.exe"

# Hermes venv 候选位置（优先 core/hermes/venv，回退 core/hermes/venv）
HERMES_VENV_CANDIDATES = [
    REPO_ROOT / "core" / "hermes" / "venv",
    REPO_ROOT / "core/hermes" / "venv",
]

# Hermes 的 requirements.txt 候选位置（与 venv 候选一一对应顺序）
HERMES_REQUIREMENTS_CANDIDATES = [
    REPO_ROOT / "core" / "hermes" / "requirements.txt",
    REPO_ROOT / "core/hermes" / "requirements.txt",
]


def _fmt(path: Path | None) -> str:
    return str(path) if path else "(未找到)"


def locate_hermes_venv() -> tuple[Path | None, Path | None]:
    """返回 (venv_path, requirements_path)，找不到则为 None。"""
    for venv, req in zip(HERMES_VENV_CANDIDATES, HERMES_REQUIREMENTS_CANDIDATES):
        if venv.exists():
            return venv, (req if req.exists() else None)
    # 都不存在：返回第一个候选作为“将创建的位置”
    return HERMES_VENV_CANDIDATES[0], (
        HERMES_REQUIREMENTS_CANDIDATES[0]
        if HERMES_REQUIREMENTS_CANDIDATES[0].exists()
        else None
    )


def run(cmd: list[str]) -> int:
    print("+ " + " ".join(str(c) for c in cmd))
    return subprocess.call(cmd)


def ensure_hermes_venv(force: bool = False) -> None:
    venv_path, req_path = locate_hermes_venv()
    venv_py = venv_path / "Scripts" / "python.exe"

    if venv_path.exists() and not force:
        print(f"[hermes venv] 已存在: {venv_path}")
    else:
        if not MANAGED_PYTHON.exists():
            # 回退到当前解释器
            base_python = sys.executable
            print(
                f"[warn] 托管 Python 不存在: {MANAGED_PYTHON}\n"
                f"       回退使用当前解释器: {base_python}"
            )
        else:
            base_python = str(MANAGED_PYTHON)

        print(f"[hermes venv] 创建虚拟环境: {venv_path}")
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        if run([base_python, "-m", "venv", str(venv_path)]) != 0:
            print("[error] 创建 venv 失败，请检查上面的输出。")
            return

    if req_path and req_path.exists():
        print(f"[hermes venv] 安装依赖: {req_path}")
        # 注意：requirements.txt 内必须固定 pydantic-core==2.46.4
        if run([str(venv_py), "-m", "pip", "install", "-r", str(req_path)]) != 0:
            print("[error] pip install 失败。请确认 requirements.txt 包含 "
                  "pydantic-core==2.46.4 固定行。")
    else:
        print(f"[warn] 未找到 hermes requirements.txt，跳过 pip install。\n"
              f"       期望位置之一:\n"
              + "\n".join(f"         - {_fmt(p)}"
                          for p in HERMES_REQUIREMENTS_CANDIDATES))


def print_summary() -> None:
    venv_path, req_path = locate_hermes_venv()
    print("=" * 64)
    print("Ikaros Python 环境摘要")
    print("=" * 64)
    print(f"[1] 托管 Python   : {_fmt(MANAGED_PYTHON)}")
    print(f"    存在?         : {'yes' if MANAGED_PYTHON.exists() else 'NO'}")
    print(f"[2] Hermes venv   : {_fmt(venv_path)}")
    print(f"    存在?         : {'yes' if (venv_path and venv_path.exists()) else 'NO'}")
    print(f"    requirements  : {_fmt(req_path)}")
    print(f"    关键固定依赖  : pydantic-core==2.46.4 (缺失会破坏 hermes dashboard)")
    print(f"[3] 运行时 Python : {_fmt(RUNTIME_PYTHON)}")
    print(f"    存在?         : {'yes' if RUNTIME_PYTHON.exists() else 'NO (可选)'}")
    print("=" * 64)


def main() -> int:
    force = "--force" in sys.argv[1:]
    print_summary()
    print()
    ensure_hermes_venv(force=force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
