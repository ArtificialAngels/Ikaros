#!/usr/bin/env python3
"""v5_call.py — Node(ikaros-memory 插件) -> memory_v5 的轻量桥接脚本（一次性进程）。

用法:
    python v5_call.py search '{"query": "...", "top_k": 5}'
    python v5_call.py store  '{"content": "...", "memory_type": "conversation", "tags": [...]}'
    python v5_call.py tick   '{}'                         # 记忆维护: 生命周期 + 反思 op
    python v5_call.py --daemon                            # 常驻 JSON 行协议

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


def _call_tick(args: dict) -> dict:
    """跑一期记忆维护: 生命周期 retention/归档 + 反思 op run_all（按各自间隔到期）。

    2026-08-24: watchdog 退役后 reflect scheduler 无自动触发源, long_term 一直为 0;
    本 op 让 ikaros-memory 插件的定时器周期驱动它。纯算法, 无额外 LLM 成本
    (reflect op 里的 LLM 生成类在 2026-08-14 决策 A 已停用)。
    """
    from memory_v5.reflect.registry import make_default_scheduler
    from memory_v5.reflect.scheduler import load_state, save_state

    sched = make_default_scheduler(load_state())
    results = sched.run_all(force=False, continue_on_error=True)
    return {"ok": True, "results": results}


_HANDLERS = {"search": _call_search, "store": _call_store, "tick": _call_tick}


def _handle_one(op: str, args_json: str) -> str:
    """执行单个 op, 返回 JSON 行 (异常吞掉返回错误, 不退栈)。"""
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        args = {}
    handler = _HANDLERS.get(op)
    if not handler:
        return json.dumps({"ok": False, "error": f"unknown op: {op}"}, ensure_ascii=False)
    try:
        result = handler(args)
    except Exception as exc:  # noqa: BLE001 — 桥接层失败必须吞掉返回 JSON, 不退栈
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return json.dumps(result, ensure_ascii=False)


def _daemon_loop() -> int:
    """常驻模式: stdin 逐行读 `op\tjson` 命令, stdout 逐行回 JSON 结果。

    复用进程 → 消除每次 spawn Python + import memory_v5 全链的 ~1.7s 冷启动。
    协议: 请求 `<op>\\t<json>`; 响应 `<json-line>`。空行/EOF 退出。
    """
    import sys as _sys
    _sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")  # type: ignore[union-attr]
    for raw in _sys.stdin:
        line = raw.strip()
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        op, _, args_json = line.partition("\t")
        _sys.stdout.write(_handle_one(op.strip(), args_json.strip()) + "\n")
        _sys.stdout.flush()
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--daemon":
        return _daemon_loop()
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "usage: v5_call.py <search|store> <json-args> | v5_call.py --daemon"}))
        return 0
    op = sys.argv[1]
    args_json = sys.argv[2]
    print(_handle_one(op, args_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())