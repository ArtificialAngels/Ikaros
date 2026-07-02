#!/usr/bin/env python3
"""sync_soul.py — 从 axiom.md 重建 SOUL.md，保留架构文档部分。

SOUL.md = axiom.md (权威源) + 架构灵魂文档 (SOUL 独有)

用法:
    python bin/sync_soul.py              # HERMES_ROOT 自动解析
    python bin/sync_soul.py --dry-run    # 只打印差异，不写文件

设计:
    - axiom.md 是唯一权威 (哥哥编辑 axiom.md → 跑此脚本 → SOUL.md 更新)
    - SOUL.md 的 "## 7 层身心架构" 及以下部分是独有内容，原样保留
    - 分界标记: 以 "## 7 层身心架构" 或 "<!-- ARCHITECTURE_BELOW -->" 开头
"""

import argparse
import sys
from pathlib import Path

# ── 路径解析 ──────────────────────────────────────────────────────

def resolve_hermes_data() -> Path:
    """找到 data/hermes-agent 目录 (HERMES_HOME)."""
    # 1. 环境变量
    import os
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        p = Path(env_home)
        if p.is_dir():
            return p

    # 2. 从脚本位置回推: bin/sync_soul.py → 项目根 → data/hermes-agent
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    candidate = project_root / "data" / "hermes-agent"
    if candidate.is_dir():
        return candidate

    # 3. 回退: 当前目录
    cwd = Path.cwd()
    candidate = cwd / "data" / "hermes-agent"
    if candidate.is_dir():
        return candidate

    print("[FATAL] 无法解析 HERMES_HOME (data/hermes-agent 目录)", file=sys.stderr)
    sys.exit(1)


# ── 分界检测 ──────────────────────────────────────────────────────

ARCHITECTURE_MARKERS = [
    "<!-- ARCHITECTURE_BELOW -->",
    "## 7 层身心架构",
    "## 7层身心架构",
]

def find_architecture_start(lines: list[str]) -> int:
    """找到架构文档的起始行号 (0-based). 找不到返回 -1."""
    for i, line in enumerate(lines):
        stripped = line.strip()
        for marker in ARCHITECTURE_MARKERS:
            if stripped == marker or stripped.startswith(marker):
                return i
    return -1


# ── 主逻辑 ────────────────────────────────────────────────────────

SOUL_HEADER = """\
# 灵魂 (SOUL) — 伊卡洛斯（Ikaros）

> **自动同步**: 本文件由 `bin/sync_soul.py` 从 `axiom.md` 自动生成。
> **权威源**: `data/hermes-agent/ikaros-identity/axiom.md` — 哥哥编辑 axiom.md 后跑 `python bin/sync_soul.py` 即可同步。
> **本文件**: webui MemoryView 灵魂面板 + hermes-agent system prompt 注入源。
> **不要手动编辑上半部分** (公理区域) — 下半部分 (架构文档) 可以自由编辑。

---

"""


def sync(dry_run: bool = False) -> bool:
    hermes_data = resolve_hermes_data()
    axiom_path = hermes_data / "ikaros-identity" / "axiom.md"
    soul_path = hermes_data / "SOUL.md"

    if not axiom_path.is_file():
        print(f"[FATAL] axiom.md 不存在: {axiom_path}", file=sys.stderr)
        return False

    # 1. 读 axiom.md (权威源)
    axiom_content = axiom_path.read_text(encoding="utf-8").strip()

    # 2. 读现有 SOUL.md，提取架构文档部分
    architecture_section = ""
    if soul_path.is_file():
        soul_lines = soul_path.read_text(encoding="utf-8").splitlines(keepends=True)
        arch_start = find_architecture_start(soul_lines)
        if arch_start >= 0:
            architecture_section = "".join(soul_lines[arch_start:]).rstrip() + "\n"
        else:
            print("[WARN] SOUL.md 中未找到架构文档分界标记，将只包含 axiom 内容。")

    # 3. 组装新 SOUL.md
    new_content = SOUL_HEADER + axiom_content + "\n\n---\n\n" + architecture_section

    if dry_run:
        print("=== DRY RUN — 不写文件 ===")
        print(f"axiom.md: {len(axiom_content)} chars")
        print(f"architecture section: {len(architecture_section)} chars")
        print(f"new SOUL.md: {len(new_content)} chars")
        if soul_path.is_file():
            old = soul_path.read_text(encoding="utf-8")
            if old == new_content:
                print("SOUL.md 已是最新，无需更新。")
            else:
                print("SOUL.md 需要更新。")
        else:
            print("SOUL.md 不存在，将创建。")
        return True

    # 4. 写入
    soul_path.write_text(new_content, encoding="utf-8")
    print(f"[OK] SOUL.md 已从 axiom.md 同步 ({len(new_content)} chars)")
    print(f"     axiom: {axiom_path}")
    print(f"     soul:  {soul_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="从 axiom.md 同步 SOUL.md")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不写文件")
    args = parser.parse_args()
    ok = sync(dry_run=args.dry_run)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
