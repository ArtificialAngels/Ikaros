"""一次性存量数据回填: 给带 node:/branch: 标签但缺 session: 标签的 V5 记忆补上会话标签.

背景 (H1 会话隔离): 2026-08-01 起新写入的记忆都带 session:<persist_key> 标签,
tree_scoped_retrieve 靠它做会话边界过滤. 但存量记忆 (7-28 ~ 8-01 期间写入) 只有
node:/branch: 标签, 没有 session: 标签 —— 它们在检索时走 "legacy 无标签放行" 分支,
旧会话记忆可能串台.

回填逻辑:
1. 遍历 core/memory_v5/data/v5/*.json 拓扑文件, 建立 node_id → persist_key 映射;
2. 对 memory 表中 tags 含 "node:" 但不含 "session:" 的行, 查 node_id 归属的
   persist_key, 补上 "session:<persist_key>";
3. 无法归属 (node id 不在任何拓扑中) 的行跳过, 保持原样.

用法: python bin/backfill-session-tags.py [--dry-run]
安全: 只更新 tags 字段 (追加标签, 不动内容/权重/时间戳); --dry-run 只看不改.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "core" / "memory_v5" / "data" / "v5"
DB = DATA_DIR / "v5.db"

# 排除这些文件 (非拓扑)
_SKIP = {"sessions.json", "ui_conversation_tree_memories.json"}


def build_node_session_map() -> dict[str, str]:
    """遍历拓扑 JSON, 返回 {node_id: persist_key}."""
    mapping: dict[str, str] = {}
    for f in sorted(DATA_DIR.glob("*.json")):
        if f.name in _SKIP:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 兼容顶层 list (如 ui_conversation_tree_memories.json) 与 dict 拓扑
        if isinstance(data, dict):
            persist_key = data.get("persist_key") or f.stem
            nodes = data.get("nodes", [])
        elif isinstance(data, list):
            # 列表文件可能是 {persist_key, nodes} 列表或纯记忆列表 —— 只认 dict 项
            persist_key = f.stem
            nodes = []
            for item in data:
                if isinstance(item, dict) and "nodes" in item:
                    persist_key = item.get("persist_key") or persist_key
                    nodes.extend(item.get("nodes", []))
        else:
            continue
        for n in nodes:
            if isinstance(n, dict):
                nid = n.get("id")
                if nid:
                    mapping[nid] = persist_key
    return mapping


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不修改")
    args = ap.parse_args()

    if not DB.exists():
        print(f"[bf] DB not found: {DB}")
        sys.exit(1)

    mapping = build_node_session_map()
    print(f"[bf] node→session 映射: {len(mapping)} 节点, "
          f"覆盖 {len(set(mapping.values()))} 个会话")

    db = sqlite3.connect(DB)
    db.execute("PRAGMA journal_mode=WAL")
    cur = db.cursor()

    # 找缺 session 标签但带 node: 标签的行
    cur.execute(
        "SELECT id, tags FROM memory "
        "WHERE tags LIKE '%node:%' AND tags NOT LIKE '%session:%'"
    )
    rows = cur.fetchall()
    print(f"[bf] 缺 session 标签的 node 记忆: {len(rows)} 条")

    updated = 0
    orphaned = 0
    skipped = 0
    for mid, tags in rows:
        # 取第一个 node:<id> 标签
        nid = None
        for t in (tags or "").split():
            if t.startswith("node:") and len(t) > 5:
                nid = t[5:]
                break
        if not nid:
            skipped += 1
            continue
        persist_key = mapping.get(nid)
        if persist_key:
            new_tags = f"{tags} session:{persist_key}".strip()
        else:
            # 孤儿: 节点已不在任何现存拓扑 (旧树已重建/删除) → 打 session:orphan
            # 隔离出树域检索 (tree_scoped_retrieve 会把非本会话的 session 标签排除),
            # 避免旧对话记忆跨会话串台; 记忆本体保留在 V5 长期库中.
            new_tags = f"{tags} session:orphan".strip()
            orphaned += 1
        if not args.dry_run:
            cur.execute("UPDATE memory SET tags=? WHERE id=?", (new_tags, mid))
        updated += 1

    if not args.dry_run:
        db.commit()
    db.close()

    print(f"[bf] 完成: 回填 {updated} 条 (其中孤儿打标 {orphaned} 条), "
          f"跳过 {skipped} 条"
          f"{' (dry-run, 未修改)' if args.dry_run else ''}")
    if updated:
        print(f"[bf] 提示: 若之后要撤消, 可手动去掉这些行 tags 中的 'session:' 后缀")


if __name__ == "__main__":
    main()
