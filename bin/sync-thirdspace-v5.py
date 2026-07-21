#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ikaros V5 -> ThirdSpace Vault 同步脚本

功能: 将 V5 最新反思 (latest_thought.json) 单向推送到 ThirdSpace vault 的
      02-日记/反思/ 目录，生成符合 ThirdSpace frontmatter 规范 (9 字段) 的卡片。

路径解析 (遵循项目"不硬编码盘符"约定):
  - THIRDSPACE_VAULT 环境变量 -> 否则 IKAROS_ROOT/data/thirdspace-vault
  - IKAROS_MEMORY     环境变量 -> 否则 IKAROS_ROOT/Ikaros-memory
  - 兜底默认 E:/Ikaros

用法:
  python bin/sync-thirdspace-v5.py            # 同步最新一条
  python bin/sync-thirdspace-v5.py --latest   # 同上 (默认即最新)
  python bin/sync-thirdspace-v5.py --dry-run  # 预览，不写入

依赖: 仅标准库 (json / pathlib / datetime / argparse)
"""

import os
import sys
import json
import argparse
import datetime
from pathlib import Path


def resolve_env(name: str, *fallbacks: str) -> str:
    """逐个尝试环境变量与兜底值，返回第一个存在的路径字符串。"""
    val = os.environ.get(name)
    if val:
        return val
    for fb in fallbacks:
        if fb:
            return fb
    return ""


def get_root() -> Path:
    ikaros_root = resolve_env("IKAROS_ROOT", "E:/Ikaros")
    return Path(ikaros_root)


def get_vault_root() -> Path:
    ikaros_root = get_root()
    return Path(resolve_env("THIRDSPACE_VAULT", str(ikaros_root / "data" / "thirdspace-vault")))


def get_v5_thought() -> dict | None:
    """读取 V5 最新反思 (latest_thought.json)。"""
    ikaros_root = get_root()
    memory_root = Path(resolve_env("IKAROS_MEMORY", str(ikaros_root / "Ikaros-memory")))
    thought_path = memory_root / "data" / "v5" / "latest_thought.json"
    if not thought_path.exists():
        print(f"[WARN] 未找到 V5 状态文件: {thought_path}")
        return None
    try:
        with open(thought_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("text"):
            print("[WARN] latest_thought.json 中没有 text 字段")
            return None
        return data
    except Exception as e:
        print(f"[ERROR] 读取 {thought_path} 失败: {e}")
        return None


def fmt_ts(ts) -> str:
    """把 Unix 时间戳或 ISO 字符串转成 YYYY-MM-DD HH:MM:SS（本地时间）。"""
    if isinstance(ts, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    if isinstance(ts, str):
        try:
            # 兼容 "2026-07-20 21:00:00" 或 ISO 8601
            s = ts.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(s)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_reflection(thought: dict, vault_root: Path) -> Path:
    reflect_dir = vault_root / "02-日记" / "反思"
    ts_raw = thought.get("ts")
    ts_str = fmt_ts(ts_raw)
    date_str = ts_str[:10].replace("-", "")
    kind = thought.get("kind", "philosophy")
    theme = thought.get("theme", "ikaros")

    title = f"{date_str}_metacog反思_{kind}"
    filepath = reflect_dir / f"{title}.md"

    frontmatter = f"""---
title: "Metacog 反思 - {date_str}"
type: card
topic: ikaros
workspace: "02-日记"
created: "{ts_str}"
modified: "{ts_str}"
tags: ["metacog", "self-reflection", "ikaros", "{kind}", "{theme}"]
source: mcp
status: active
---

"""

    body = thought.get("text", "").strip() + "\n"

    reflect_dir.mkdir(parents=True, exist_ok=True)
    # 避免同日重复覆盖：若已存在则跳过（同步是幂等的，按 ts 去重由调用方负责）
    if filepath.exists():
        print(f"[SKIP] 已存在，跳过: {filepath}")
        return filepath

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(frontmatter + body)

    print(f"[OK] 已写入: {filepath}")
    return filepath


def main() -> int:
    parser = argparse.ArgumentParser(description="Ikaros V5 -> ThirdSpace Vault 同步")
    parser.add_argument("--latest", action="store_true", help="只同步最新一条 (默认行为)")
    parser.add_argument("--dry-run", action="store_true", help="预览，不写入文件")
    args = parser.parse_args()

    vault_root = get_vault_root()
    if not (vault_root / ".thirdspace" / "workspace-index.yaml").exists():
        print(f"[ERROR] 不是有效的 ThirdSpace vault 根: {vault_root}")
        return 1

    thought = get_v5_thought()
    if not thought:
        print("[WARN] 没有可同步的 V5 反思内容")
        return 1

    preview = thought.get("text", "")[:120]
    print(f"最新反思 (前120字): {preview}...")
    print(f"目标 vault: {vault_root}")

    if args.dry_run:
        print("[DRY-RUN] 预览模式，未写入任何文件")
        return 0

    write_reflection(thought, vault_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
