#!/usr/bin/env python3
"""v5_call.py — Node(ikaros-memory 插件) -> memory_v5 的轻量桥接脚本（一次性进程）。

用法:
    python v5_call.py search '{"query": "...", "top_k": 5}'
    python v5_call.py store  '{"content": "...", "memory_type": "conversation", "tags": [...]}'
    python v5_call.py tick   '{}'                         # [deprecated] 见 loop
    python v5_call.py loop   '{"phase": "post", "response": "..."}'   # 标准记忆循环
    python v5_call.py --daemon                            # 常驻 JSON 行协议

stdout 输出 JSON:
    search -> {"ok": true, "items": [{"id": 1, "content": "...", "score": 0.72, "type": "..."}, ...]}
    store  -> {"ok": true, "id": 123}
    loop   -> {"ok": true, "phase": "post", "ran": [...], "skipped": {...},
               "errors": {...}, "results": {...}, "elapsed_ms": 12}

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
    """[deprecated 2026-08-30] 旧维护入口, 保留仅为兼容未重建的插件 dist。

    新代码请用 loop op 的 maintenance 阶段 —— 它除了反思管线, 还覆盖
    post 阶段的精力/关系推进与反重复语料记录, 且带统一的状态落盘与观测。
    """
    from memory_v5.reflect.registry import make_default_scheduler
    from memory_v5.reflect.scheduler import load_state, save_state

    sched = make_default_scheduler(load_state())
    results = sched.run_all(force=False, continue_on_error=True)
    return {"ok": True, "results": results, "deprecated": "use loop(op=maintenance)"}


def _call_loop(args: dict) -> dict:
    """标准记忆循环 (memory_v5/loop.py) —— 一个 phase 跑完所有到期 step。

    args:
        phase   pre | post | maintenance  (必填)
        query       pre 阶段: 本轮用户消息 (召回用)
        response    post 阶段: 本轮助手回复 (反重复语料用)
        session_id  召回去重 ledger 的会话键 (默认 default)
        character   角色名 (反重复语料按角色隔离)
        project     pre 阶段: 项目名 (默认 ikaros)
        force       true 忽略冷却全跑 (手动补账用)

    2026-08-30: 插件三个 hook (agent/pre-step、agent/turn-stopping、
    ctx.interval 定时器) 各自拼装的记忆动作, 统一收敛成调这一个 op。
    """
    from memory_v5 import loop as loop_mod

    phase = str(args.get("phase") or "").strip()
    if phase not in loop_mod.PHASES:
        return {"ok": False, "error": f"unknown phase: {phase!r}",
                "valid_phases": list(loop_mod.PHASES)}

    extra = {}
    if args.get("project") is not None:
        extra["project"] = args.get("project")
    for key in ("include_dsh_only", "project_top_k", "intensity"):
        if args.get(key) is not None:
            extra[key] = args.get(key)

    return loop_mod.run_phase(
        phase,
        query=str(args.get("query") or ""),
        response=str(args.get("response") or ""),
        session_id=str(args.get("session_id") or "default"),
        character=str(args.get("character") or ""),
        extra=extra,
        force=bool(args.get("force")),
    )


_HANDLERS = {
    "search": _call_search,
    "store": _call_store,
    "tick": _call_tick,          # deprecated
    "loop": _call_loop,
}


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