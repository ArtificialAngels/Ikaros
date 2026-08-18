#!/usr/bin/env python3
"""v5_call.py — Node(ikaros-memory 插件) -> memory_v5 的轻量桥接脚本（一次性进程）。

用法:
    python v5_call.py search '{"query": "...", "top_k": 5}'
    python v5_call.py store  '{"content": "...", "memory_type": "conversation", "tags": [...]}'

stdout 输出 JSON:
    search -> {"ok": true, "items": [{"id": 1, "content": "...", "score": 0.72, "type": "..."}, ...]}
    store  -> {"ok": true, "id": 123}

设计:
    - 不经过 MCP 协议 —— 直接 import memory_v5.memory_api（sys.path 加 core/），
      零额外进程常驻、单次调用即退, 避免在 harness 里维护常驻 Python。
    - 失败输出 {"ok": false, "error": "..."} 且退出码 0 —— 插件侧静默降级,
      绝不因记忆失败阻断 dsh 会话。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[5]  # plugins/ikaros-memory/bin -> Ikaros (E:/Ikaros)
_CORE = _ROOT / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))


def _call_search(args: dict) -> dict:
    from memory_v5 import memory_api
    api = memory_api.V5MemoryAPI()
    items = api.search(
        query=args.get("query") or "",
        top_k=int(args.get("top_k") or 5),
    )
    out = []
    for it in items or []:
        out.append({
            "id": it.get("id"),
            "content": str(it.get("content") or "")[:500],
            "score": round(float(it.get("score", it.get("raw", 0))), 3),
            "type": it.get("type", ""),
        })
    return {"ok": True, "items": out}


def _call_store(args: dict) -> dict:
    from memory_v5 import memory_api
    api = memory_api.V5MemoryAPI()
    content = str(args.get("content") or "").strip()
    if not content:
        return {"ok": False, "error": "empty content"}
    mid = api.store(
        content=content,
        memory_type=str(args.get("memory_type") or "conversation"),
        tags=list(args.get("tags") or []),
        importance=float(args.get("importance") or 0.5),
    )
    return {"ok": True, "id": mid}


_HANDLERS = {"search": _call_search, "store": _call_store}


def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "usage: v5_call.py <search|store> <json-args>"}))
        return 0
    op = sys.argv[1]
    try:
        args = json.loads(sys.argv[2])
    except json.JSONDecodeError:
        args = {}
    handler = _HANDLERS.get(op)
    if not handler:
        print(json.dumps({"ok": False, "error": f"unknown op: {op}"}))
        return 0
    try:
        result = handler(args)
    except Exception as exc:  # noqa: BLE001 — 桥接层失败必须吞掉返回 JSON, 不退栈
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())