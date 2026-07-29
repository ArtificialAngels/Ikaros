#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 Hermes 单一会话 (.hermes_history) 导入对话树 (conversation-tree :48920).

- 解析 `data/hermes-agent/.hermes_history` 中的 `# 时间戳` + `+用户消息` 行。
- 该文件仅含用户侧消息 (无助手回复), 故每条 `+` 行作为树中一个 user 节点。
- 构建一棵线性对话树: root(系统导入说明) -> m1 -> m2 -> ... (沿创建序)。
- 写入同一个 `ui_conversation_tree.json` (persist_key 一致) + V5 store; 备份旧 JSON。
- 运行后需重启 conversation-tree 服务以重新加载内存中的树。
"""
from __future__ import annotations
import sys
import json
import shutil
from pathlib import Path
from datetime import datetime

IKAROS_ROOT = Path(__file__).resolve().parent.parent
CORE = IKAROS_ROOT / "core"
sys.path.insert(0, str(CORE))

from memory_v5 import conversation_tree as ct  # noqa: E402
from memory_v5 import store as v5s  # noqa: E402

HERMES_HISTORY = IKAROS_ROOT / "data" / "hermes-agent" / ".hermes_history"
DATA_DIR = CORE / "memory_v5" / "data" / "v5"
PERSIST_KEY = "ui_conversation_tree"


def _load(ids):
    batch = v5s.get_batch(ids)
    return {mid: m.content for mid, m in batch.items()}


def _search(q, top_k=10):
    res = v5s.search(q, top_k=top_k)
    return [{"id": r.id, "content": r.content} for r in res]


def parse_history(path: Path):
    """返回 [(timestamp_str, text), ...] 顺序保留所有用户消息。"""
    entries = []
    cur_ts = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        if line.startswith("# "):
            cur_ts = line[2:].strip()
        elif line.startswith("+"):
            text = line[1:].strip()
            if text:
                entries.append((cur_ts, text))
    return entries


def make_title(text: str, limit: int = 30) -> str:
    t = text.replace("\n", " ").strip()
    return t[:limit] + ("…" if len(t) > limit else "")


def main() -> int:
    if not HERMES_HISTORY.exists():
        print(f"[import] ERROR: {HERMES_HISTORY} not found")
        return 1

    entries = parse_history(HERMES_HISTORY)
    if not entries:
        print("[import] ERROR: no messages parsed")
        return 1

    # 备份旧树 JSON
    old_json = DATA_DIR / f"{PERSIST_KEY}.json"
    if old_json.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = DATA_DIR / f"{PERSIST_KEY}.json.bak-hermes-import-{stamp}"
        shutil.copy2(old_json, bak)
        print(f"[import] backed up old tree -> {bak.name}")

    t = ct.ConversationTree(
        persist_key=PERSIST_KEY,
        data_dir=str(DATA_DIR),
        _store=v5s.store,
        _load=_load,
        _search=_search,
    )

    ts_first = entries[0][0] or "unknown"
    ts_last = entries[-1][0] or "unknown"
    root = t.init([{
        "role": "system",
        "content": (
            f"Hermes 会话导入 (来源: .hermes_history)\n"
            f"时间跨度: {ts_first} → {ts_last}\n"
            f"共 {len(entries)} 条用户消息 (无助手回复, 仅用户侧记录)。"
        ),
    }])
    # 线性链: 每条用户消息一个节点, 沿创建序串联
    for ts, text in entries:
        t.add_turn(
            [{"role": "user", "content": text}],
            title=make_title(text),
            tags="hermes-import",
        )

    data = json.loads(t.serialize())
    print(f"[import] OK: root={root.id}, total nodes={len(data['nodes'])} "
          f"(1 root + {len(entries)} messages)")
    print(f"[import] JSON written -> {old_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
