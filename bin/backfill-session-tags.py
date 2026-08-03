#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填对话树记忆的 session: 标签 (R3 隐患清理).

问题背景
--------
tree_adapter.tree_scoped_retrieve 仅按 tag 过滤来做多会话隔离: 带 session: 标签但
不属于本会话的记忆会被排除。本次修复已让 add_turn / add_memory 对 *新* 记忆打
session:{persist_key} 标签; 但 **存量** 记忆(legacy 旧行, 只有 node:/branch: 标签
而无 session:) 仍会被 tree_scoped_retrieve 当作"无 session 标签的 legacy"放行,
可能跨会话/跨树串台。

本脚本扫描所有对话树拓扑 JSON, 建立 node_id -> persist_key 映射, 对 v5 store 中带
node:/branch: 标签但缺 session: 的记忆补打 session:{persist_key}。

安全策略
--------
- 默认 dry-run: 只统计 + 打印样例, 不写库。
- 需显式 --apply 才执行 UPDATE 并 commit。
- 每条记忆只补打一次, 已含 session: 的跳过。
- 找不到 node 所属 persist_key 的(孤儿 node 引用)跳过并计数, 不误打。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys

# 路径: 让脚本能 import memory_v5
_HERE = os.path.dirname(os.path.abspath(__file__))
_IKAROS = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_IKAROS, "core"))
sys.path.insert(0, _IKAROS)

NODE_RE = re.compile(r"node:([^\s]+)")


def build_node_session_map(v5_data_dir: str) -> dict:
    """扫描 V5_DATA_DIR 下所有 ui_conversation_tree*.json, 返回 {node_id: persist_key}。"""
    node_to_session: dict = {}
    skipped_orphan = 0
    if not os.path.isdir(v5_data_dir):
        return node_to_session
    for fname in os.listdir(v5_data_dir):
        if not fname.startswith("ui_conversation_tree") or not fname.endswith(".json"):
            continue
        persist_key = fname[: -len(".json")]  # 文件名即 persist_key
        path = os.path.join(v5_data_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                topo = json.load(f)
        except Exception as e:
            print(f"  [warn] 解析拓扑失败 {fname}: {e}")
            continue
        nodes = (topo.get("nodes") or []) if isinstance(topo, dict) else []
        # 拓扑 nodes 可能是 dict(keyed by id) 或 list(每项含 id); 两种都兼容.
        if isinstance(nodes, dict):
            node_ids = nodes.keys()
        else:
            node_ids = [n.get("id") for n in nodes if isinstance(n, dict) and n.get("id")]
        for nid in node_ids:
            node_to_session[nid] = persist_key
    return node_to_session


def main() -> int:
    ap = argparse.ArgumentParser(description="回填对话树记忆的 session: 标签")
    ap.add_argument("--apply", action="store_true",
                    help="真正写库; 省略则 dry-run (只统计+打印样例)")
    ap.add_argument("--limit", type=int, default=20,
                    help="dry-run 时打印的样例条数 (默认 20)")
    args = ap.parse_args()

    try:
        from memory_v5 import store as v5s
    except Exception as e:
        print(f"[error] 无法 import memory_v5.store: {e}")
        return 2

    v5_data_dir = str(v5s.V5_DATA_DIR)
    print(f"V5 data dir : {v5_data_dir}")
    node_to_session = build_node_session_map(v5_data_dir)
    print(f"节点->会话映射: {len(node_to_session)} 个节点")

    db_path = str(v5s.V5_DB_PATH)

    def _open_db():
        # 维护脚本用裸 sqlite3 连接, 避开 store.conn() 在长扫描事务里的 WAL 读快照
        # 陈旧问题(同一连接内 SELECT 可能读到 checkpoint 前的旧快照). 这里显式设
        # WAL + busy_timeout + TRUNCATE checkpoint, 保证读到最新已提交数据.
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA journal_mode=WAL")
        try:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        return c

    # 取所有带 node: 标签的记忆
    candidates = []
    with _open_db() as c:
        cur = c.execute(
            "SELECT id, tags FROM memory WHERE tags LIKE ?",
            ("%node:%",),
        )
        for row in cur.fetchall():
            mid = row["id"]
            tags = row["tags"] or ""
            # 注意: 实际 tag 是带后缀的 `session:<persist_key>` (如 session:ui_conversation_tree),
            # 不是裸 `session:`, 故必须用 startswith 判定, 不能用 `"session:" in tags.split()`
            # (后者是列表精确成员判定, 永远不会命中带后缀的 tag, 会导致:
            #   1) dry-run 误报"待补打"; 2) 重复 --apply 时追加第二个同名 tag, 污染数据).
            if any(tok.startswith("session:") for tok in tags.split()):
                continue  # 已带 session 标签, 跳过
            m = NODE_RE.search(tags)
            if not m:
                continue
            nid = m.group(1)
            pk = node_to_session.get(nid)
            if not pk:
                candidates.append((mid, tags, nid, None))
            else:
                candidates.append((mid, tags, nid, pk))

    need = [x for x in candidates if x[3] is not None]
    orphan = [x for x in candidates if x[3] is None]

    print(f"\n扫描结果:")
    print(f"  待补打(可定位会话): {len(need)}")
    print(f"  孤儿 node 引用(跳过): {len(orphan)}")

    if not need:
        print("无需回填。")
        return 0

    print(f"\n样例(最多 {args.limit} 条):")
    for mid, tags, nid, pk in need[: args.limit]:
        print(f"  id={mid} node={nid} -> session={pk}")
        print(f"    tags: {tags}")

    if not args.apply:
        print("\n[dry-run] 未写库。加 --apply 执行回填。")
        return 0

    # 执行回填 (裸 sqlite3 连接, 显式 commit + checkpoint)
    updated = 0
    with _open_db() as c:
        for mid, tags, nid, pk in need:
            tagset = tags.split()
            new_sess = f"session:{pk}"
            if new_sess in tagset:
                continue  # 防御: 已含本会话标签, 不重复追加 (幂等)
            tagset.append(new_sess)
            new_tags = " ".join(tagset)
            c.execute("UPDATE memory SET tags=? WHERE id=?", (new_tags, mid))
            updated += 1
        c.commit()
        try:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
    print(f"\n[apply] 已补打 {updated} 条记忆的 session: 标签。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
