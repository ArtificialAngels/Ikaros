"""
Completion Drain for Bridge Pool (Artificial Angel Phase 2)
============================================================
Background thread that drains process_registry.completion_queue and stores
async delegation results so they can be injected into the next chat turn.

The webui's broker uses bridge_pool.AgentPool which runs AIAgent directly
(not through the gateway). The gateway has _async_delegation_watcher, but
the broker path has no equivalent — so background delegate_task() results
would be lost. This module fills that gap.

Architecture:
  1. A daemon thread polls completion_queue every 2 seconds
  2. Async delegation completions are stored in _pending_completions[session_id]
  3. On the next _run_chat call, pending completions are injected as context
     so the agent can report results to the user
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ikaros.completion_drain")

# session_id -> list of completion event dicts
_pending_completions: Dict[str, List[Dict[str, Any]]] = {}
_pending_lock = threading.Lock()
_drain_thread: Optional[threading.Thread] = None
_drain_started = False


def _drain_loop(interval: float = 2.0) -> None:
    """Background loop: drain completion_queue and bucket by session_key."""
    global _drain_started
    _drain_started = True
    logger.info("completion_drain: background thread started (interval=%.1fs)", interval)

    while True:
        try:
            from tools.process_registry import process_registry
            cq = process_registry.completion_queue

            # Drain all available events (non-blocking)
            drained = 0
            while not cq.empty():
                try:
                    evt = cq.get_nowait()
                except Exception:
                    break

                evt_type = evt.get("type", "")
                if evt_type != "async_delegation":
                    # Not our concern — requeue for other consumers
                    try:
                        cq.put(evt)
                    except Exception:
                        pass
                    break

                session_key = evt.get("session_key", "")
                if not session_key:
                    logger.debug("completion_drain: event without session_key, dropping")
                    continue

                with _pending_lock:
                    if session_key not in _pending_completions:
                        _pending_completions[session_key] = []
                    _pending_completions[session_key].append(evt)
                drained += 1

            if drained:
                logger.info("completion_drain: drained %d completion(s)", drained)

        except Exception as exc:
            logger.warning("completion_drain: drain loop error: %s", exc)

        time.sleep(interval)


def start_drain_thread() -> None:
    """Start the background drain thread (idempotent)."""
    global _drain_thread
    if _drain_thread is not None and _drain_thread.is_alive():
        return
    _drain_thread = threading.Thread(
        target=_drain_loop,
        name="completion-drain",
        daemon=True,
    )
    _drain_thread.start()
    logger.info("completion_drain: thread launched")


def pop_pending_completions(session_id: str) -> List[Dict[str, Any]]:
    """Retrieve and clear pending completions for a session.

    Called by the patched _run_chat to inject results before the agent runs.
    """
    with _pending_lock:
        events = _pending_completions.pop(session_id, [])
    return events


def format_completions_context(events: List[Dict[str, Any]]) -> str:
    """Format pending completion events into a system context block.

    This text is prepended to the user's message so the agent sees the
    background task results and can report them.
    """
    if not events:
        return ""

    lines = [
        "[IMPORTANT: Background Task Completion(s)]",
        "The following background tasks have completed since the last interaction:",
        ""
    ]

    for evt in events:
        goal = evt.get("goal", "unknown task")
        status = evt.get("status", "unknown")
        summary = evt.get("summary", "")
        error = evt.get("error", "")
        duration = evt.get("duration_seconds", 0)
        delegation_id = evt.get("delegation_id", "")

        lines.append(f"--- Task: {goal[:100]} ---")
        lines.append(f"  Status: {status}")
        lines.append(f"  Duration: {duration:.1f}s")

        if summary:
            lines.append(f"  Result: {summary[:500]}")
        if error:
            lines.append(f"  Error: {error[:200]}")

        lines.append("")

    lines.append("Please inform the user about these completed tasks concisely.")
    lines.append("[END Background Task Completions]")

    return "\n".join(lines)


def patch_agent_pool() -> bool:
    """Monkey-patch AgentPool to add completion drain.

    - Adds _pending_completions dict and starts drain thread in __init__
    - Injects pending completions as context in _run_chat
    Returns True if patch was applied.
    """
    try:
        import bridge_pool as bp
    except ImportError:
        logger.debug("completion_drain: bridge_pool not importable yet")
        return False

    # Patch __init__ to start the drain thread
    original_init = bp.AgentPool.__init__

    def _patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        start_drain_thread()
        logger.info("completion_drain: AgentPool patched with drain thread")

    bp.AgentPool.__init__ = _patched_init

    # Patch _run_chat to inject pending completions
    original_run_chat = bp.AgentPool._run_chat

    def _patched_run_chat(self, session, record, message, *args, **kwargs):
        # Check for pending completions for this session
        pending = pop_pending_completions(session.session_id)
        if pending:
            context_text = format_completions_context(pending)
            if context_text:
                # Prepend completion context to the message
                original_message = message
                if isinstance(message, str):
                    message = context_text + "\n\n" + message
                elif isinstance(message, dict) and "content" in message:
                    message = {**message, "content": context_text + "\n\n" + str(message.get("content", ""))}
                else:
                    # message might be a list or other format — wrap as string
                    message = context_text + "\n\n" + str(message)
                logger.info(
                    "completion_drain: injected %d completion(s) into session %s",
                    len(pending), session.session_id,
                )
        return original_run_chat(self, session, record, message, *args, **kwargs)

    bp.AgentPool._run_chat = _patched_run_chat

    logger.info("completion_drain: AgentPool._run_chat patched")
    return True
