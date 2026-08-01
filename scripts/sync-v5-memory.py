"""sync-v5-memory.py — 把主仓 memory_v5 核心引擎同步到 v5-memory 开源发行版。

策略（2026-08-02）：
1. 主仓 `core/memory_v5/*.py` 是功能来源（08-01 有大量更新：unified_retrieve/
   temporal supersede/项目轨等）
2. v5-memory 是独立 `v5` 包（import 名 `v5`，MEM_ROOT=parent.parent），
   不能整文件覆盖（会把 `memory_v5.` import 和路径结构带过去）
3. 同步 = 主仓文件为基准 + 结构回写：
   - `memory_v5.` → `v5.`（import 前缀）
   - MEM_ROOT = parent → parent.parent（v5/ 包结构）
   - docs 路径 memory_v5 → v5
4. 只同步 v5-memory 里**已存在**的共有文件；主仓私有文件（action_log/
   cogno_5d/goal_contract/hermes_provider/rules_retriever 等）不同步
5. v5-memory 独有的文件（cli.py/llama_launcher.py 等）不动

用法：
    runtime/portable-python/python.exe scripts/sync-v5-memory.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MAIN = Path(r"E:\Ikaros\core\memory_v5")
V5M = Path(r"E:\v5-memory\v5")

# 结构回写规则（主仓 → v5-memory）
REPLACEMENTS = [
    # import 前缀（两种格式都覆盖）
    (r"\bfrom memory_v5\.", "from v5."),
    (r"\bimport memory_v5\.", "import v5."),
    (r"\bfrom memory_v5 import", "from v5 import"),
    (r"\bimport memory_v5\b", "import v5"),
    # MEM_ROOT 路径（v5/ 包在 v5-memory 下是 parent.parent）
    (r"MEM_ROOT = Path\(__file__\)\.resolve\(\)\.parent\b",
     "MEM_ROOT = Path(__file__).resolve().parent.parent"),
    # docs 文档路径引用
    (r"docs/scripts/core/memory_v5/", "docs/scripts/core/v5/"),
]

# 主仓有、v5-memory 缺的 tools 文件（新增模块需同步过去并注册）
TOOLS_TO_SYNC = ["project_tool.py"]


def sync_tools(dry_run: bool) -> list[str]:
    """同步主仓 tools/ 下的新增模块到 v5-memory 的 v5/tools/。"""
    synced = []
    for name in TOOLS_TO_SYNC:
        src = MAIN / "tools" / name
        dst = V5M / "tools" / name
        if src.exists() and not dst.exists():
            text = src.read_text(encoding="utf-8")
            for pattern, repl in REPLACEMENTS:
                text = re.sub(pattern, repl, text)
            if not dry_run:
                dst.write_text(text, encoding="utf-8")
            synced.append(f"tools/{name}")
    return synced

# 两边共有文件（v5-memory 已有、主仓也有）
def shared_py_files() -> list[str]:
    main_files = {p.name for p in MAIN.glob("*.py") if p.name != "__init__.py"}
    v5m_files = {p.name for p in V5M.glob("*.py") if p.name != "__init__.py"}
    return sorted(main_files & v5m_files)


def sync_file(name: str, dry_run: bool) -> str:
    src = MAIN / name
    dst = V5M / name
    text = src.read_text(encoding="utf-8")
    for pattern, repl in REPLACEMENTS:
        text = re.sub(pattern, repl, text)
    if not dry_run:
        dst.write_text(text, encoding="utf-8")
    return name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只列出将同步的文件")
    args = ap.parse_args()

    files = shared_py_files()
    print(f"发现 {len(files)} 个共有文件")
    synced = 0
    for name in files:
        result = sync_file(name, args.dry_run)
        if result:
            synced += 1
            print(f"  {'[DRY]' if args.dry_run else '[sync]'} {name}")
    # 同步主仓新增 tools 模块
    tools = sync_tools(args.dry_run)
    for t in tools:
        print(f"  {'[DRY]' if args.dry_run else '[sync]'} {t}")
    print(f"{'[dry-run] 将同步' if args.dry_run else '已同步'} {synced} 文件 + {len(tools)} tools")
    return 0


if __name__ == "__main__":
    sys.exit(main())
