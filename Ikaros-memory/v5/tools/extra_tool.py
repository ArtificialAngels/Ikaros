"""v5.tools.extra_tool — P1/P2 tools (narrative, dissonance, proactive,
self-discovery, reflect-op).

These wrap the heavier / less critical subsystems.  All are wrapped with
@safe_tool and have graceful fallbacks when :8080 / ChromaDB / Hermes are
unavailable.
"""

from __future__ import annotations

from v5.tools.utils import safe_tool, dumps


@safe_tool
def v5_narrative_generate(days: int = 30, use_llm: bool = True) -> str:
    """Generate a monthly self-narrative from recent memories.

    Falls back to a rule-based narrative when :8080 / DeepSeek is down.
    """
    from v5.narrative import generate_narrative
    r = generate_narrative(days=days, use_llm=use_llm)
    return dumps(r, ensure_ascii=False)


@safe_tool
def v5_dissonance_check(content: str, mem_type: str = "fact") -> str:
    """Detect whether `content` contradicts an existing memory.

    Fallback: :8080 down => no NLI performed, returns {conflicts: []}.
    """
    from v5.dissonance import detect_dissonance
    r = detect_dissonance(content, mem_type)
    return dumps(r, ensure_ascii=False)


@safe_tool
def v5_proactive_check() -> str:
    """Decide whether Ikaros should proactively speak right now.

    Fallback: runs the gate checks locally (no LLM needed).
    """
    from v5.proactive import should_speak, try_proactive

    ok, reason = should_speak()
    text = None
    if ok:
        try:
            text = try_proactive()
        except Exception:  # noqa: BLE001
            text = None
    return dumps({"should_speak": ok, "reason": reason, "text": text}, ensure_ascii=False)


@safe_tool
def v5_self_discover() -> str:
    """Run one self-architecture discovery pass (reads project files).

    Fallback: :8080 down => returns {written: 0}.
    """
    from v5.self_discovery import self_discover
    n = self_discover()
    return dumps({"written": n, "ok": n > 0})


@safe_tool
def v5_reflect_run_op(op_name: str = "", force: bool = False) -> str:
    """Run one (or all due) reflection ops from the registry.

    op_name: name of a registered op (consolidate / distill / reflect /
             cleanup / narrative / self_discovery / vector_sync / promote /
             dedup).  Empty => run all currently-due ops.
    Per-op fallback: a failing op is reported, run_all continues on error.
    """
    from v5.reflect.registry import make_default_scheduler
    from v5.reflect.scheduler import load_state, save_state

    sched = make_default_scheduler(load_state())
    force = bool(force)

    if op_name:
        op = next((o for o in sched._ops if o.name == op_name), None)
        if op is None:
            return dumps({"ok": False, "error": f"unknown op: {op_name}"}, ensure_ascii=False)
        n = sched.run_one(op, force=force)
        save_state(sched.state)
        return dumps({"op": op_name, "processed": n, "ok": True}, ensure_ascii=False)

    results = sched.run_all(force=force, continue_on_error=True)
    return dumps({"results": results, "ok": True}, ensure_ascii=False)
