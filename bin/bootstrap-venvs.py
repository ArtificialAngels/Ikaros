#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bin/bootstrap-venvs.py  —  Ikaros Python 环境引导工具（仅脚手架 / 可重复运行）

本脚本用于记录并（按需）重建 Ikaros 使用的 Python 环境，目标是
提升依赖可复现性（dependency reproducibility）。

环境清单（2026-08-18 精简：hermes / neko venv 引导已随底座退役移除）：
  1. 托管 Python (managed python)
     - 路径: %USERPROFILE%\\.workbuddy\\binaries\\python\\versions\\3.13.12（动态解析，不再写死用户名）
     - 角色: WorkBuddy 自带的嵌入式 Python，用于运行本仓库的辅助脚本。
             注意：路径不含 python.exe，脚本会自动补上。
  2. 运行时 Python (runtime python)
     - 路径: runtime/portable-python/python.exe（若存在）
     - 角色: 运行中实际拉起模型服务（llama.cpp 等）的 portable 解释器。

本脚本只做“只读说明 + 按需创建/修复 venv”，不会删除/修改任何正在运行的
服务代码。

用法:
    python bin/bootstrap-venvs.py            # 打印环境摘要
"""

from __future__ import annotations

import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 1) 托管 Python（WorkBuddy 嵌入式，动态解析）
MANAGED_PYTHON_DIR = (
    pathlib.Path(os.environ.get("USERPROFILE", "~")) / ".workbuddy" / "binaries"
    / "python" / "versions" / "3.13.12"
)
MANAGED_PYTHON = MANAGED_PYTHON_DIR / "python.exe"

# 2) 运行时 portable Python（可选）
RUNTIME_PYTHON = REPO_ROOT / "runtime" / "portable-python" / "python.exe"


def _fmt(path: pathlib.Path | None) -> str:
    return str(path) if path else "(未找到)"


def print_summary() -> None:
    print("=" * 64)
    print("Ikaros Python 环境摘要")
    print("=" * 64)
    print(f"[1] 托管 Python   : {_fmt(MANAGED_PYTHON)}")
    print(f"    存在?         : {'yes' if MANAGED_PYTHON.exists() else 'NO'}")
    print(f"[2] 运行时 Python : {_fmt(RUNTIME_PYTHON)}")
    print(f"    存在?         : {'yes' if RUNTIME_PYTHON.exists() else 'NO (可选)'}")
    print("=" * 64)
    print("(hermes / neko venv 引导已于 2026-08-18 随底座退役移除)")


def main() -> int:
    print_summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
