#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/env/config.py — 配置集中加载器（ADDITIVE 脚手架，stdlib 优先）

设计目标：把分散的配置集中到一个分层（layered）加载模型里，提升可复现性
与可维护性。本模块是“安全脚手架”——它不会修改任何已有模块；是否接入由
各服务自行决定。

分层模型（layering）：
    base 层   : config/defaults/<name>.<ext>     # 提交到 git 的静态模板（无密钥）
    override 层: data/config/<name>.<ext>        # 运行时覆盖，gitignored，可含密钥

规则：
    - override 层（data/config）的值会覆盖 base 层（config/defaults）的对应键。
    - 两层都缺失时返回 {} 并打印 warning（不抛异常，方便增量迁移）。
    - 支持的格式：.yaml（需要 pyyaml）与 .json（stdlib 即可）。
      若文件是 .yaml 但环境中没有 yaml 模块，则抛出 RuntimeError 提示安装。

用法示例：
    from core.env.config import load_config
    ports = load_config("ports")            # 合并 config/defaults/ports.yaml
    models = load_config("panel_models")    # 合并 config/defaults/panel_models.json

注意：本文件刻意保持零第三方依赖（除可选的 pyyaml 用于 .yaml）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# 仓库根：core/env/config.py -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULTS_DIR = REPO_ROOT / "config" / "defaults"
OVERRIDE_DIR = REPO_ROOT / "data" / "config"

# 支持的扩展名（按优先级尝试）
EXTENSIONS = (".yaml", ".yml", ".json")


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并：override 的键值覆盖 base；dict 递归，其余直接覆盖。"""
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore  # 优先使用 pyyaml（已在 requirements.txt 固定）

        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except ImportError:
        # 退回 stdlib 最小 YAML 子集解析器（无需第三方依赖）。
        # 支持本仓库模板用到的：映射、缩进嵌套、块序列（含映射项）。
        with path.open("r", encoding="utf-8") as fh:
            return _minimal_yaml_parse(fh.read())


# ---------------------------------------------------------------------------
# 最小 YAML 子集解析器（stdlib only）
# 仅支持本仓库配置模板用到的语法：
#   key: value
#   key:            # 空值 -> None，后续更深的行作为嵌套值
#   - scalar
#   - key: value    # 序列中的映射项，可跨多行
# ---------------------------------------------------------------------------
_MAP_ENTRY_RE = re.compile(r"^[A-Za-z0-9_.\-]+:(\s.*)?$")


def _split_kv(s: str) -> tuple[str, str | None]:
    if ":" not in s:
        return s, None
    idx = s.index(":")
    key = s[:idx].strip()
    val = s[idx + 1:].strip()
    # 去掉行内注释（仅当 '#' 前有空白，避免误伤 URL 中的 '#'）
    if " #" in val:
        val = val.split(" #", 1)[0].strip()
    return key, val


def _is_map_entry(s: str) -> bool:
    return bool(_MAP_ENTRY_RE.match(s))


def _scalar(s: str) -> Any:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _minimal_yaml_parse(text: str) -> dict:
    items: list[tuple[int, str]] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        items.append((indent, raw.rstrip()))

    value, _ = _parse_node(items, 0, 0)
    return value if isinstance(value, dict) else (value if value is not None else {})


def _parse_node(items, i, indent):
    if i >= len(items):
        return None, i
    _, content = items[i]
    cs = content.lstrip()
    if cs.startswith("-") and (len(cs) == 1 or cs[1] == " "):
        return _parse_seq(items, i, items[i][0])
    return _parse_map(items, i, items[i][0])


def _parse_map(items, i, indent):
    result: dict = {}
    while i < len(items):
        ind, content = items[i]
        if ind < indent:
            break
        if ind > indent:
            break
        cs = content.lstrip()
        if cs.startswith("-") and (len(cs) == 1 or cs[1] == " "):
            break
        key, val = _split_kv(content.strip())
        if val is None:
            i += 1
            continue
        if val == "":
            i += 1
            if i < len(items) and items[i][0] > indent:
                nested, i = _parse_node(items, i, items[i][0])
                result[key] = nested
            else:
                result[key] = None
            continue
        result[key] = _scalar(val)
        i += 1
    return result, i


def _parse_seq(items, i, indent):
    seq: list = []
    while i < len(items):
        ind, content = items[i]
        if ind < indent:
            break
        if ind > indent:
            break
        cs = content.lstrip()
        if not (cs.startswith("-") and (len(cs) == 1 or cs[1] == " ")):
            break
        after = cs[1:].lstrip()
        item_indent = ind + 2
        if after == "":
            i += 1
            if i < len(items) and items[i][0] > indent:
                val, i = _parse_node(items, i, items[i][0])
                seq.append(val)
            else:
                seq.append(None)
            continue
        if _is_map_entry(after):
            m: dict = {}
            key, val = _split_kv(after)
            if val == "":
                i += 1
                if i < len(items) and items[i][0] > item_indent:
                    nested, i = _parse_node(items, i, items[i][0])
                    m[key] = nested
                else:
                    m[key] = None
            else:
                m[key] = _scalar(val)
                i += 1
            # 继续消费该映射项的后续键（缩进 == item_indent）
            while i < len(items):
                ni, nc = items[i]
                if ni != item_indent:
                    break
                ncs = nc.lstrip()
                if ncs.startswith("-") and (len(ncs) == 1 or ncs[1] == " "):
                    break
                k2, v2 = _split_kv(nc.strip())
                if v2 is None:
                    i += 1
                    continue
                if v2 == "":
                    i += 1
                    if i < len(items) and items[i][0] > item_indent:
                        nested2, i = _parse_node(items, i, items[i][0])
                        m[k2] = nested2
                    else:
                        m[k2] = None
                else:
                    m[k2] = _scalar(v2)
                    i += 1
            seq.append(m)
        else:
            seq.append(_scalar(after))
            i += 1
    return seq, i


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def _load_file(path: Path) -> dict:
    if path.suffix in (".yaml", ".yml"):
        return _load_yaml(path)
    if path.suffix == ".json":
        return _load_json(path)
    # 未知扩展名：尝试 json，再尝试 yaml
    try:
        return _load_json(path)
    except Exception:
        return _load_yaml(path)


def load_config(name: str) -> dict[str, Any]:
    """
    加载名为 <name> 的配置，返回合并后的 dict。

    合并顺序：config/defaults/<name> 作为 base，
    data/config/<name> 作为 override（override 优先）。

    仅当两层都不存在时才返回 {}。
    """
    base_path = _resolve(DEFAULTS_DIR, name)
    override_path = _resolve(OVERRIDE_DIR, name)

    base = _load_file(base_path) if base_path else {}
    override = _load_file(override_path) if override_path else {}

    if not base and not override:
        print(
            f"[config] warning: 未在以下任一位置找到配置 '{name}':\n"
            f"    base    : {DEFAULTS_DIR / name}.<yaml|json>\n"
            f"    override: {OVERRIDE_DIR / name}.<yaml|json>\n"
            f"    返回空 dict。"
        )
        return {}

    merged = _deep_merge(base, override)
    return merged


def _resolve(directory: Path, name: str) -> Path | None:
    """在 directory 下按 name + 扩展名查找已存在的文件。"""
    if not directory.exists():
        return None
    # 精确匹配 name（含扩展名）
    candidate = directory / name
    if candidate.exists() and candidate.is_file():
        return candidate
    # 按扩展名尝试
    for ext in EXTENSIONS:
        candidate = directory / (name + ext)
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


if __name__ == "__main__":
    import pprint

    for _name in ("ports", "panel_models"):
        print(f"=== load_config({_name!r}) ===")
        pprint.pprint(load_config(_name))
