"""conversation-tree 会话导出 JSONL 镜像 (P3, CortexFS 借鉴落地项, 2026-08-10).

背景: CortexFS 的「会话即可审计的普通文件」理念 —— 对话内容目前散在
v5.db (节点消息 JSON) + ui_conversation_tree.json (拓扑指针), 不可直接
diff / grep。本脚本把**所有**会话树节点的对话记录导出为逐行 JSON,
任何工具可审计。

行格式 (每行一条对话记录; 节点的一轮 turn 通常含 user + assistant 两条):
  {"node_id": ..., "parent_id": ..., "role": ..., "content": ...,
   "created": ..., "session": <persist_key>}

用法:
  python bin/export-convtree-jsonl.py
  python bin/export-convtree-jsonl.py --output data/eval/convtree_export.jsonl

退出码: 0 = 成功 (至少导出 1 条); 1 = 找不到任何会话树 / 输出写入失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from memory_v5 import store as v5_store  # noqa: E402
from memory_v5.conversation_tree import ConversationTree, V5_DATA_DIR  # noqa: E402

DEFAULT_SESSION = "ui_conversation_tree"
DEFAULT_OUTPUT = ROOT / "data" / "eval" / "convtree_export.jsonl"


def _load_batch(memory_ids: list[int]) -> dict[int, str]:
    """批量读记忆内容 → {id: content_string} (与 server.py _load_str 同构)."""
    if not memory_ids:
        return {}
    batch = v5_store.get_batch(memory_ids)
    return {mid: (m.content or "") for mid, m in batch.items()}


def enumerate_persist_keys(data_dir: Path) -> list[str]:
    """枚举所有会话树 persist_key: sessions.json 注册表优先, 失败时扫描可解析文件."""
    keys: list[str] = []
    sess_file = data_dir / "sessions.json"
    if sess_file.exists():
        try:
            sessions = json.loads(sess_file.read_text(encoding="utf-8"))
            if isinstance(sessions, list):
                for s in sessions:
                    pk = (s or {}).get("persist_key")
                    if pk and pk not in keys:
                        keys.append(pk)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] sessions.json unreadable ({e}); falling back to file scan",
                  file=sys.stderr)
    if DEFAULT_SESSION not in keys and (data_dir / f"{DEFAULT_SESSION}.json").exists():
        keys.append(DEFAULT_SESSION)
    if not keys:
        # 兜底: 任何能成功反序列化为树 (含 nodes 列表) 的 JSON 都算一棵树
        for p in sorted(data_dir.glob("*.json")):
            if p.name == "sessions.json":
                continue
            try:
                if ConversationTree.load(persist_key=p.stem, data_dir=data_dir,
                                         _load=_load_batch) is not None:
                    keys.append(p.stem)
            except Exception:  # noqa: BLE001
                continue
    return keys


def _flatten_content(content) -> str:
    """消息 content 拍平成字符串 (多模态 ContentBlock 列表 → JSON 文本)."""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def export_tree(out_f, tree: ConversationTree, pk: str) -> int:
    """导出单棵树的所有节点对话记录, 返回写出的行数."""
    ids = sorted({n.v5_memory_id for n in tree.nodes.values() if n.v5_memory_id > 0})
    contents = _load_batch(ids) if ids else {}
    written = 0
    missing = 0
    for n in tree.nodes.values():
        raw = contents.get(n.v5_memory_id, "")
        if not raw:
            missing += 1
            continue
        try:
            msgs = json.loads(raw)
        except json.JSONDecodeError:
            msgs = [{"role": "system", "content": raw}]
        if not isinstance(msgs, list):
            msgs = [{"role": "system", "content": raw}]
        for m in msgs:
            if not isinstance(m, dict):
                continue
            rec = {
                "node_id": n.id,
                "parent_id": n.parent_id,
                "role": str(m.get("role") or "?"),
                "content": _flatten_content(m.get("content")),
                "created": n.created_at,
                "session": pk,
            }
            out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
    if missing:
        print(f"[warn] {pk}: {missing} node(s) without stored content, skipped",
              file=sys.stderr)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Export all conversation-tree conversation records as JSONL "
                    "(CortexFS audit-file mirror).")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                    help=f"output JSONL path (default: {DEFAULT_OUTPUT})")
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="V5 data dir (default: core/memory_v5/data/v5)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else V5_DATA_DIR
    if not data_dir.is_dir():
        print(f"[ERROR] V5 data dir not found: {data_dir}", file=sys.stderr)
        return 1

    keys = enumerate_persist_keys(data_dir)
    if not keys:
        print(f"[ERROR] no conversation trees found under {data_dir}", file=sys.stderr)
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    n_trees = 0
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            for pk in keys:
                tree = ConversationTree.load(persist_key=pk, data_dir=data_dir,
                                             _load=_load_batch)
                if tree is None:
                    print(f"[warn] tree '{pk}' failed to load, skipped", file=sys.stderr)
                    continue
                written = export_tree(f, tree, pk)
                n_trees += 1
                total += written
                print(f"[export] {pk}: {len(tree.nodes)} nodes, {written} records")
    except OSError as e:
        print(f"[ERROR] failed to write {out_path}: {e}", file=sys.stderr)
        return 1

    print(f"[OK] {total} records from {n_trees} tree(s) -> {out_path}")
    return 0 if total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
