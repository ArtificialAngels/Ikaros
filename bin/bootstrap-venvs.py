#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bin/bootstrap-venvs.py  —  Ikaros Python 环境引导工具（仅脚手架 / 可重复运行）

本脚本用于记录并（按需）重建 Ikaros 使用的 Python 环境，目标是
提升依赖可复现性（dependency reproducibility）。

环境清单：
  1. 托管 Python (managed python)
     - 路径: %USERPROFILE%\\.workbuddy\\binaries\\python\\versions\\3.13.12（动态解析，不再写死用户名）
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
  4. Neko venv (.venv)
     - 路径: apps/neko/.venv
     - 基础解释器: runtime/portable-python311/python.exe（随 IKAROS 树捆绑的 3.11.15，
       保证 neko 不依赖 uv 缓存或系统 Python，满足“U 盘即插即用”）。
     - 角色: 承载 N.E.K.O 桌面宠物三个后端服务（:48911/:48912/:48915）。
     - 源码: apps/neko（editable 安装，.pth 指向该目录）。
     - 重建策略:
         * 换盘符 / home 失效（不强制重装）: 仅重指 pyvenv.cfg 的 home、
           重写 editable .pth 指向 apps/neko、并把 python311.dll + vcruntime
           拷进 Scripts/ —— 已装的 ~200 个 cp311 轮子原样复用，无需联网。
         * venv 完全缺失: 用捆绑 3.11 建 venv 后 `pip install -e .`
           （需联网安装依赖，谨慎）。

本脚本只做“只读说明 + 按需创建/修复 venv”，不会删除/修改任何正在运行的
服务代码；--force 仅重指路径与重建缺失 venv，不会破坏已装依赖。

用法:
    python bin/bootstrap-venvs.py            # 打印环境摘要，缺失/失效则尝试修复
    python bin/bootstrap-venvs.py --force    # 强制重指 neko/hermes venv 路径（谨慎）
"""

from __future__ import annotations

import os
import shutil
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

# 4) Neko venv（桌面宠物三个后端服务）
#    .venv 位于 apps/neko 下；基础解释器用随树捆绑的 3.11.15（runtime/portable-python311）。
#    editable 源码根 = apps/neko；editable .pth 文件名保持 pip 生成的约定名。
NEKO_VENV = REPO_ROOT / "core" / "neko" / ".venv"
NEKO_SRC = REPO_ROOT / "core" / "neko"
NEKO_BUNDLED_PY311 = REPO_ROOT / "runtime" / "portable-python311" / "python.exe"
NEKO_EDITABLE_PTH = NEKO_VENV / "Lib" / "site-packages" / "_editable_impl_n_e_k_o.pth"
# venv 解释器壳依赖的 DLL（拷进 Scripts/ 做双保险，换机器更稳）
NEKO_VENV_DLLS = ("python311.dll", "vcruntime140.dll", "vcruntime140_1.dll")


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


def run(cmd: list[str], cwd: str | None = None) -> int:
    suffix = f"  (cwd={cwd})" if cwd else ""
    print("+ " + " ".join(str(c) for c in cmd) + suffix)
    return subprocess.call(cmd, cwd=cwd)


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


def _fix_neko_venv_paths(venv_path: Path, py311_exe: Path) -> None:
    """重指 neko venv 的 home / editable.pth / 拷 DLL，保留已装 cp311 包。

    用于“换盘符 / home 失效”场景：不重装依赖，仅把绝对路径改到当前
    IKAROS 树内的捆绑 3.11 与 apps/neko 源码。
    """
    # 1) pyvenv.cfg 的 home 改指捆绑 3.11 目录
    cfg = venv_path / "pyvenv.cfg"
    home = str(py311_exe.parent)
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8")
        new_lines: list[str] = []
        replaced = False
        for line in text.splitlines():
            if line.lower().startswith("home"):
                new_lines.append(f"home = {home}")
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f"home = {home}")
        cfg.write_text("\r\n".join(new_lines) + "\r\n", encoding="utf-8")

    # 2) editable .pth 单行指向 apps/neko（覆盖任何失效的旧路径，如已删的 exProject）
    pth = venv_path / "Lib" / "site-packages" / "_editable_impl_n_e_k_o.pth"
    pth.parent.mkdir(parents=True, exist_ok=True)
    pth.write_text(str(NEKO_SRC) + "\n", encoding="utf-8")

    # 3) 解释器 DLL 拷进 Scripts/ 做双保险，避免依赖 home 目录的 DLL 解析
    scripts = venv_path / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for dll in NEKO_VENV_DLLS:
        src = py311_exe.parent / dll
        if src.exists():
            shutil.copy(src, scripts / dll)


def ensure_neko_venv(force: bool = False) -> None:
    if not NEKO_BUNDLED_PY311.exists():
        print(f"[neko venv] 捆绑 3.11 不存在: {_fmt(NEKO_BUNDLED_PY311)}")
        print("          请先将 runtime/portable-python311 随 IKAROS 树一起拷贝。")
        return

    # —— 分支 A：venv 完全缺失 → 完整创建（需联网装 ~200 依赖）——
    if not NEKO_VENV.exists():
        print(f"[neko venv] 不存在，执行完整创建（需要网络安装依赖）: {NEKO_VENV}")
        NEKO_VENV.parent.mkdir(parents=True, exist_ok=True)
        if run([str(NEKO_BUNDLED_PY311), "-m", "venv", str(NEKO_VENV)]) != 0:
            print("[error] 创建 neko venv 失败。")
            return
        # 在 apps/neko 下 editable 安装；会重新生成正确的 editable .pth
        run([str(NEKO_VENV / "Scripts" / "python.exe"),
             "-m", "pip", "install", "-e", "."], cwd=str(NEKO_SRC))
        return

    # —— 分支 B：venv 存在 → 检测路径是否仍指向当前 IKAROS 树（换盘符/拷贝后必漂移）——
    # 用“期望路径 vs 实际路径”逐字比对，确定性地捕获盘符漂移；
    # 不依赖 import（否则会被 Scripts/ 下的本地 DLL 双保险掩盖 home 失效）。
    venv_py = NEKO_VENV / "Scripts" / "python.exe"
    expected_home = str(NEKO_BUNDLED_PY311.parent)
    expected_src = str(NEKO_SRC)

    def _cfg_home() -> str | None:
        cfg = NEKO_VENV / "pyvenv.cfg"
        if not cfg.exists():
            return None
        for line in cfg.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("home"):
                return line.split("=", 1)[1].strip()
        return None

    def _pth_src() -> str | None:
        if not NEKO_EDITABLE_PTH.exists():
            return None
        txt = NEKO_EDITABLE_PTH.read_text(encoding="utf-8").strip()
        return txt.splitlines()[0].strip() if txt else None

    needs_fix = force
    cur_home = _cfg_home()
    cur_src = _pth_src()
    if cur_home != expected_home:
        needs_fix = True
    if cur_src != expected_src:
        needs_fix = True
    # 仍可运行性兜底（Scripts/ 下 DLL 缺失等导致 venv python 都起不来）
    if venv_py.exists():
        try:
            r = subprocess.run([str(venv_py), "--version"],
                               capture_output=True, timeout=20)
            if r.returncode != 0:
                needs_fix = True
        except Exception:
            needs_fix = True
    else:
        needs_fix = True

    if not needs_fix:
        print(f"[neko venv] 已存在且路径正确: {NEKO_VENV}")
        return

    reasons: list[str] = []
    if cur_home != expected_home:
        reasons.append(f"home={cur_home!r} -> {expected_home!r}")
    if cur_src != expected_src:
        reasons.append(f"editable={cur_src!r} -> {expected_src!r}")
    tag = " (--force)" if force else " (路径漂移检测)"
    reason_txt = "; ".join(reasons) if reasons else "强制"
    print(f"[neko venv] 修复路径指向（保留已装 cp311 包）: {NEKO_VENV}{tag} [{reason_txt}]")
    _fix_neko_venv_paths(NEKO_VENV, NEKO_BUNDLED_PY311)

    # 验证修复结果
    try:
        r = subprocess.run([str(venv_py), "-c",
                            "import sys; import app; print('neko venv OK:', sys.version.split()[0])"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            print("          " + r.stdout.strip().splitlines()[-1])
        else:
            print("[warn] 修复后校验失败: " + (r.stderr.strip().splitlines()[-1]
                                              if r.stderr.strip() else "未知错误"))
    except Exception as e:
        print(f"[warn] 修复后校验异常: {e}")


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
    print(f"[4] Neko venv     : {_fmt(NEKO_VENV)}")
    print(f"    存在?         : {'yes' if NEKO_VENV.exists() else 'NO'}")
    print(f"    基础解释器    : {_fmt(NEKO_BUNDLED_PY311)}")
    print(f"    解释器存在?   : {'yes' if NEKO_BUNDLED_PY311.exists() else 'NO (需随树拷贝)'}")
    print(f"    源码(editable): {_fmt(NEKO_SRC)}")
    print(f"    关键约束      : requires-python ==3.11.* (#2516 未修，勿升 3.12)")
    print("=" * 64)


def main() -> int:
    force = "--force" in sys.argv[1:]
    print_summary()
    print()
    ensure_hermes_venv(force=force)
    print()
    ensure_neko_venv(force=force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
