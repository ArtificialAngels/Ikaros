#!/usr/bin/env python3
"""加载 bin/ikaros-memory-watchdog.py 为可导入模块。

文件名含连字符 (ikaros-memory-watchdog.py), 无法用 `import ikaros_memory_watchdog`
直接导入。本模块用 importlib 按路径加载, 并注册到 sys.modules (同名去重),
供 llm_client (热载入) 与 llama-help (配置/控制) 复用, 避免重复定义启动逻辑。
"""
from __future__ import annotations

import importlib.util as _ilu
import os
import sys
from pathlib import Path

_WD_MODULE_NAME = "ikaros_memory_watchdog"


def load_watchdog():
    """返回已加载的看门狗模块 (单例, 同进程只执行一次)。"""
    mod = sys.modules.get(_WD_MODULE_NAME)
    if mod is not None:
        return mod
    root = Path(os.environ.get("IKAROS_ROOT", r"E:\Ikaros"))
    path = root / "bin" / "ikaros-memory-watchdog.py"
    spec = _ilu.spec_from_file_location(_WD_MODULE_NAME, str(path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[_WD_MODULE_NAME] = mod  # 先注册, 防止模块内相互引用时重复执行
    spec.loader.exec_module(mod)
    return mod


# 便捷: 直接暴露 ensure_local_llm / 端口工具
def ensure_local_llm(timeout: int = 180) -> bool:
    return load_watchdog().ensure_local_llm(timeout=timeout)
