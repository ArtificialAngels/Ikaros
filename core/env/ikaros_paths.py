#!/usr/bin/env python3
"""Ikaros 便携式路径解析模块 — 基于 core/env/ 的相对值配置。

所有路径最终相对于 IKAROS_ROOT 解析，确保项目可整体迁移到任意目录。
2026-08-18: 移除 hermes/neko 段, 新增 dsh; 锚点只认 IKAROS_ROOT (HERMES_ROOT 兼容已废弃)。

优先级（高→低）:
  1. 显式环境变量 (IKAROS_*)
  2. ikaros-paths.json 中定义的相对路径
  3. 内建默认值

用法:
    from core.env.ikaros_paths import IkarosPaths

    p = IkarosPaths()
    print(p.python)          # ...runtime/portable-python/python.exe
    print(p.llama_server)    # ...runtime/llama/b10000-cuda/llama-server.exe
    print(p.get("ports.embedding"))  # → 8587
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("ikaros.paths")

# ── 内建默认路径模板（相对于 IKAROS_ROOT） ──
# 当 ikaros-paths.json 不可用时使用的兜底
BUILTIN_DEFAULTS: dict[str, Any] = {
    "core": {
        "python": "runtime/portable-python/python.exe",
        "runtime": "runtime",
        "node": "runtime/node/node.exe",
        "npm": "runtime/node/npm.cmd",
        "data": "data",
        "data_models": "data/models",
        "bin": "bin",
        "config": "config",
        "logs": "data/logs",
    },
    "dsh": {
        "root": "runtime/dsh",
        "source": "runtime/deepseek-harness-master",
        "profile": "data/dsh/profiles",
        "web_port": 3080,
        "overlay": "core/ikaros-dsh/cordis.patch.yml",
    },
    "memory": {
        "root": "core/memory_v5",
        "data": "core/memory_v5/data",
        "models": "core/memory_v5/models",
        "db": "core/memory_v5/data/v5/v5.db",
        "chromadb": "core/memory_v5/data/v5/chroma",
    },
    "llama": {
        "dir": "runtime/llama/b10000-cuda",
        "server": "runtime/llama/b10000-cuda/llama-server.exe",
        "cli": "runtime/llama/b10000-cuda/llama-cli.exe",
    },
    "models": {
        "embedding": "core/memory_v5/models/nomic-embed-text-v1.5.Q8_0.gguf",
        "llm": "core/memory_v5/models/Phi-4-mini-instruct-Q4_K_M.gguf",
    },
    "omp": {
        "root": "data/omp",
        "agent": "data/omp/agent",
    },
    "ports": {
        "embedding": 8587,
        "llama": 8080,
        "dsh_web": 3080,
    },
}

# ── 环境变量 → 配置键映射（用于覆盖） ──
ENV_OVERRIDE_MAP: dict[str, str] = {
    "IKAROS_ROOT": "ikaros_root",
    "IKAROS_PYTHON": "core.python",
    "IKAROS_RUNTIME": "core.runtime",
    "IKAROS_NODE": "core.node",
    "IKAROS_DATA": "core.data",
    "IKAROS_BIN": "core.bin",
    "IKAROS_CONFIG": "core.config",
    "IKAROS_LOGS": "core.logs",
    "IKAROS_DSH": "dsh.root",
    "IKAROS_DSH_SOURCE": "dsh.source",
    "IKAROS_DSH_PROFILE": "dsh.profile",
    "IKAROS_DSH_WEB_PORT": "dsh.web_port",
}


def _detect_root() -> Path:
    """3 级优先级探测 IKAROS_ROOT（同 detect-root.ps1 逻辑）。"""
    # 1) 环境变量（只认 IKAROS_ROOT）
    env_root = os.environ.get("IKAROS_ROOT")
    if env_root:
        candidate = Path(env_root).resolve()
        if (candidate / "runtime" / "portable-python" / "python.exe").exists():
            return candidate

    # 2) 从脚本位置推导（core/env/ → 项目根）
    script_dir = Path(__file__).resolve().parent  # core/env/
    ikaros_root = script_dir.parent.parent  # 项目根
    markers = ["runtime/portable-python/python.exe", "core/env", "bin/ikaros-env.bat"]
    if all((ikaros_root / m).exists() for m in markers):
        return ikaros_root

    # 3) 从 CWD 向上遍历
    cwd = Path.cwd().resolve()
    for parent in [cwd] + list(cwd.parents):
        if all((parent / m).exists() for m in markers):
            return parent

    raise RuntimeError(
        "无法自动检测 IKAROS_ROOT。请设置环境变量 IKAROS_ROOT 或 "
        "在项目根目录下运行此脚本。"
    )


def _flatten(d: dict, parent_key: str = "") -> dict:
    """将嵌套 dict 展平为 'a.b.c': value 格式。"""
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        key = f"{parent_key}.{k}" if parent_key else k
        if isinstance(v, dict) and not any(isinstance(v2, dict) for v2 in v.values()):
            # 只有一层嵌套时展平
            for k2, v2 in v.items():
                items.append((f"{key}.{k2}", v2))
        elif isinstance(v, dict):
            items.extend(_flatten(v, key).items())
        else:
            items.append((key, v))
    return dict(items)


class IkarosPaths:
    """便携路径容器——所有路径相对于 IKAROS_ROOT 解析。

    属性可直接点取: p.python, p.llama_server, p.dsh_root 等。
    """

    def __init__(self, root: str | Path | None = None, paths_file: str | Path | None = None):
        # ── 1. 确定根目录 ──
        if root:
            self.root = Path(root).resolve()
        else:
            self.root = _detect_root()

        self.root_str = str(self.root).replace("\\", "/")

        # ── 2. 读取 ikaros-paths.json ──
        if paths_file is None:
            paths_file = self.root / "core" / "env" / "ikaros-paths.json"
        self._raw_config: dict[str, Any] = {}
        self._config_path = Path(paths_file)
        self._load_config(paths_file)

        # ── 3. 解析所有路径 ──
        self._paths: dict[str, Any] = {}
        self._parse_all()

        # ── 4. 应用环境变量覆盖 ──
        self._apply_env_overrides()

    # ── 配置加载 ──

    def _load_config(self, paths_file: str | Path) -> None:
        """加载 ikaros-paths.json，失败时使用内建默认值。"""
        try:
            with open(paths_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("ikaros-paths.json 顶层不是 dict")
            self._raw_config = raw
        except FileNotFoundError:
            log.warning("ikaros-paths.json 未找到 (%s)，使用内建默认值", paths_file)
            self._raw_config = {}
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"ikaros-paths.json 格式错误 ({paths_file}): {e}"
            ) from e

    # ── 路径解析 ──

    def _resolve(self, raw_path: str) -> str:
        """将路径解析为绝对路径。

        支持格式:
          - 绝对路径 (E:\\...):  替换前缀中的 ikaros_root 部分
          - 相对路径 (core/memory_v5/...):  拼接 IKAROS_ROOT
          - 环境变量引用 (%VAR%):  展开
        """
        # 展开环境变量引用
        expanded = os.path.expandvars(raw_path)

        p = Path(expanded)

        # 已经是绝对路径 → 替换旧根为新根
        if p.is_absolute():
            # 尝试从 JSON 中记录的 ikaros_root 替换
            recorded_root = self._raw_config.get("ikaros_root", "")
            if recorded_root:
                recorded_norm = recorded_root.replace("\\", "/").rstrip("/")
                expanded_norm = expanded.replace("\\", "/")
                if expanded_norm.startswith(recorded_norm):
                    relative = expanded_norm[len(recorded_norm):].lstrip("/")
                    return str((self.root / relative).resolve())
            # 无法替换 → 直接返回（可能是外部路径）
            return str(p.resolve())

        # 相对路径 → 拼 IKAROS_ROOT
        return str((self.root / p).resolve())

    def _parse_all(self) -> None:
        """递归解析 ikaros-paths.json 中的所有路径。"""
        config = self._raw_config if self._raw_config else {}
        flat = _flatten(config)

        for key, val in flat.items():
            # 跳过非路径字段
            if key.startswith("_") or key.endswith("command_arg"):
                self._paths[key] = val
                continue
            if isinstance(val, str):
                # 版本号/非路径字段
                if key.endswith("version") or key in (
                    "llama.type", "rust.type", "mcp.gitnexus.pkg",
                    "mcp.context7.pkg", "mcp.playwright.pkg", "mcp.codebase-memory.pkg",
                    "dsh.web_port",
                ):
                    self._paths[key] = val
                else:
                    self._paths[key] = self._resolve(val)
            elif isinstance(val, (int, float)):
                self._paths[key] = val
            elif isinstance(val, dict):
                self._paths[key] = val
            else:
                self._paths[key] = val

        # 补充内建默认值中缺失的项
        self._merge_builtins()

    def _merge_builtins(self) -> None:
        """用内建默认值补充 JSON 中缺失的路径。"""
        flat_builtins = _flatten(BUILTIN_DEFAULTS)
        for key, default_rel in flat_builtins.items():
            if key not in self._paths:
                if isinstance(default_rel, (int, float)):
                    self._paths[key] = default_rel
                else:
                    self._paths[key] = str((self.root / default_rel).resolve())

    def _apply_env_overrides(self) -> None:
        """环境变量覆盖：最高优先级。"""
        for env_var, config_key in ENV_OVERRIDE_MAP.items():
            val = os.environ.get(env_var)
            if val:
                if config_key == "ikaros_root":
                    self.root = Path(val).resolve()
                    self.root_str = str(self.root)
                elif "." in config_key:
                    parts = config_key.split(".")
                    current = self._paths
                    # 建深层结构
                    target = None
                    for p in parts[:-1]:
                        if p not in current or not isinstance(current[p], dict):
                            current[p] = {}
                        current = current[p]
                    current[parts[-1]] = val
                    self._paths[config_key] = val
                else:
                    self._paths[config_key] = val

    # ── 访问接口 ──

    def get(self, key: str, default: Any = None) -> Any:
        """按点分隔键取值: p.get('ports.embedding') → 8587"""
        # _paths 是扁平 dict，先直接精确匹配
        if key in self._paths:
            return self._paths[key]
        # 回退：按点拆分逐级查找（兼容嵌套 dict）
        parts = key.split(".")
        current: Any = self._paths
        for segment in parts:
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            else:
                return default
        return current

    def __getattr__(self, name: str) -> Any:
        """p.llama_server, p.dsh_root 等快捷访问。"""
        # 方式1: 精确匹配
        if name in self._paths:
            return self._paths[name]
        # 方式2: 下划线→点 (llama_server → llama.server)
        dotted = name.replace("_", ".")
        if dotted in self._paths:
            return self._paths[dotted]
        # 方式3: name 是前缀 → 收集所有子项
        prefix_matches = {k: v for k, v in self._paths.items()
                          if k.startswith(f"{name}.") or k.startswith(f"{dotted}.")}
        if prefix_matches:
            # 只有一个子项 → 直接返回值
            if len(prefix_matches) == 1:
                return list(prefix_matches.values())[0]
            # 多个子项 → 构建嵌套 dict
            result = {}
            for k, v in prefix_matches.items():
                parts = k[len(name) + 1:].split(".")
                current = result
                for p in parts[:-1]:
                    current = current.setdefault(p, {})
                current[parts[-1]] = v
            return result
        # 方式4: key 的最后一段匹配
        for key in self._paths:
            if key.endswith(f".{name}") or key.endswith(f".{dotted}"):
                return self._paths[key]
        raise AttributeError(
            f"IkarosPaths 没有 '{name}'。\n"
            f"可用键前 30: {list(self._paths.keys())[:30]}"
        )

    def __repr__(self) -> str:
        return f"IkarosPaths(root={self.root})"

    def to_dict(self, flat: bool = False) -> dict:
        """导出全部路径为 dict。"""
        if flat:
            return dict(self._paths)
        # 还原嵌套结构
        result: dict = {}
        for key, val in self._paths.items():
            parts = key.split(".")
            current = result
            for p in parts[:-1]:
                if p not in current:
                    current[p] = {}
                current = current[p]
            current[parts[-1]] = val
        return result

    def build_env(self) -> dict[str, str]:
        """构建进程环境变量 dict，供 spawn 子进程使用。

        等效于 bin/ikaros-env.bat/ps1 的功能。
        """
        env = dict(os.environ)
        root = str(self.root)

        # IKAROS_* 核心变量
        env["IKAROS_ROOT"] = root
        env["IKAROS_PYTHON"] = self.get("core.python", str(self.root / "runtime/portable-python/python.exe"))
        env["IKAROS_RUNTIME"] = self.get("core.runtime", str(self.root / "runtime"))
        env["IKAROS_NODE"] = self.get("core.node", str(self.root / "runtime/node/node.exe"))
        env["IKAROS_DATA"] = self.get("core.data", str(self.root / "data"))
        env["IKAROS_BIN"] = self.get("core.bin", str(self.root / "bin"))
        env["IKAROS_CONFIG"] = self.get("core.config", str(self.root / "config"))
        env["IKAROS_LOGS"] = self.get("core.logs", str(self.root / "data/logs"))

        # dsh
        env["IKAROS_DSH"] = self.get("dsh.root", str(self.root / "runtime/dsh"))
        env["IKAROS_DSH_SOURCE"] = self.get("dsh.source", str(self.root / "runtime/deepseek-harness-master"))
        env["IKAROS_DSH_PROFILE"] = self.get("dsh.profile", str(self.root / "data/dsh/profiles"))
        env["IKAROS_DSH_WEB_PORT"] = str(self.get("dsh.web_port", 3080))
        env["IKAROS_DSH_OVERLAY"] = self.get("dsh.overlay", str(self.root / "core/ikaros-dsh/cordis.patch.yml"))

        # omp
        env["IKAROS_OMP_AGENT"] = self.get("omp.agent", str(self.root / "data/omp/agent"))
        env["PI_CODING_AGENT_DIR"] = env["IKAROS_OMP_AGENT"]

        # LLM / Embedding
        env["LLAMA_SERVER"] = self.get("llama.server", str(self.root / "runtime/llama/b10000-cuda/llama-server.exe"))
        env["IKAROS_LLAMA_DIR"] = str(Path(env["LLAMA_SERVER"]).parent)
        env["IKAROS_MODEL_EMBEDDING"] = self.get("models.embedding", "")
        env["IKAROS_MODEL_LLM"] = self.get("models.llm", "")
        env["IKAROS_LLAMA_PORT"] = str(self.get("ports.llama", 8080))
        env["IKAROS_EMBEDDING_PORT"] = str(self.get("ports.embedding", 8587))

        # PATH 组装
        path_parts = [
            env["IKAROS_LLAMA_DIR"],
            env["IKAROS_RUNTIME"],
            str(self.root / "runtime" / "node"),
            str(Path(env["IKAROS_PYTHON"]).parent),
            str(Path(env["IKAROS_PYTHON"]).parent.parent / "Scripts"),
        ]
        old_path = os.environ.get("PATH", "")
        if old_path:
            path_parts.append(old_path)
        env["PATH"] = ";".join(path_parts)

        # 防污染
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONSTARTUP", None)

        return env


# ── 快捷入口 ──
def get_paths(root: str | Path | None = None) -> IkarosPaths:
    """获取（或复用）IkarosPaths 实例。"""
    return IkarosPaths(root=root)


# 模块级单例（延迟初始化）
_paths_instance: IkarosPaths | None = None


def ensure_paths(root: str | Path | None = None) -> IkarosPaths:
    """确保路径已初始化（模块级单例）。"""
    global _paths_instance
    if _paths_instance is None:
        _paths_instance = IkarosPaths(root=root)
    return _paths_instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = IkarosPaths()
    print(f"根目录: {p.root}")
    print(f"Python:  {p.python}")
    print(f"llama:   {p.llama_server}")
    print(f"dsh:     {p.dsh_root}")
    print(f"embed:   {p.models_embedding}")
    print(f"llm:     {p.models_llm}")
    print(f"端口:    {p.ports}")
    print("\n全部路径:")
    for k, v in sorted(p.to_dict(flat=True).items()):
        print(f"  {k:35s} = {v}")
