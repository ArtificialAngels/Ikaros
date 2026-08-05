"""Translate Hermes *native* session-chat SSE into the OpenAI-wire SSE frames
that the Ikaros conversation-tree (``core/conversation-tree/server.py``) expects.

WHY THIS EXISTS
---------------
Ikaros runs Hermes as a pristine, unmodified downstream (studio-style: zero
source intrusion into ``core/hermes``). The conversation-tree chat UI consumes a
custom SSE dialect emitted by two Ikaros patches on Hermes' OpenAI-wire
``/v1/chat/completions`` path (``gateway/platforms/api_server.py`` +
``agent/conversation_loop.py``):

    event: hermes.reasoning        data: {"text": "..."}          # model thinking
    event: hermes.tool.progress    data: {"tool","emoji","label",
                                           "toolCallId","status",
                                           "result": ...}         # tool lifecycle
    data: {choices:[{delta:{content/reasoning_content}}]}         # OpenAI chunks

Hermes' *native* session-chat endpoint (used by the Hermes dashboard itself,
UPSTREAM, unpatched) already streams everything we need — it just uses a
different event vocabulary:

    event: assistant.delta      data: {"delta": "<text>"}          # content
    event: tool.progress        data: {"tool_name":"_thinking",    # REASONING
                                       "delta": "<reasoning>"}
    event: tool.started         data: {"tool_name","preview","args"}
    event: tool.completed       data: {"tool_name","preview","args"}
    event: tool.failed          data: {"tool_name","preview","args"}
    event: run.started | message.started | assistant.completed |
           run.completed | done                                    # control, dropped

This module maps the native vocabulary onto the conversation-tree dialect so
the thinking block + tool cards keep working with ``core/hermes`` 100% pristine.
It is a pure, dependency-free translator — fully unit-testable offline.

SSE frame format (matches Hermes' ``_sse_frame`` + the conversation-tree parser,
which splits on ``\\n`` and reads ``event:``/``data:`` lines):

    event: hermes.reasoning\\ndata: {"text": "..."}\\n\\n
    event: hermes.tool.progress\\ndata: {...}\\n\\n
    data: {"choices":[{"delta":{"content":"..."}}]}\\n\\n
    data: [DONE]\\n\\n
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["SSETranslator"]

# Native (upstream Hermes) event names.
_EV_ASSISTANT_DELTA = "assistant.delta"
_EV_TOOL_PROGRESS = "tool.progress"
_EV_TOOL_STARTED = "tool.started"
_EV_TOOL_COMPLETED = "tool.completed"
_EV_TOOL_FAILED = "tool.failed"
_REASONING_TOOL = "_thinking"

# Control events we deliberately drop (the conversation-tree ends on [DONE]).
_DROP_EVENTS = frozenset({
    "run.started", "message.started", "assistant.completed",
    "run.completed", "done",
})

_DEFAULT_EMOJI = "🔧"


def _sse_frame(data: Any, *, event: Optional[str] = None) -> bytes:
    """Mirror Hermes' ``_sse_frame`` wire format."""
    if event:
        head = f"event: {event}\n"
    else:
        head = ""
    return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


class SSETranslator:
    """Stateful translator: feed native (event, payload) pairs, get wire frames.

    Usage::

        t = SSETranslator()
        for event, payload in native_stream:
            for frame in t.feed(event, payload):
                yield frame
        for frame in t.finish():
            yield frame
    """

    def __init__(self) -> None:
        # Stack of (toolCallId, tool_name) for in-flight tool invocations so a
        # running card can be paired with its completed/failed card by id.
        self._seq = 0
        self._stack: List[Tuple[str, str]] = []

    def feed(self, event: str, payload: Dict[str, Any]) -> List[bytes]:
        if event in _DROP_EVENTS:
            return []
        if event == _EV_ASSISTANT_DELTA:
            return self._on_delta(payload)
        if event == _EV_TOOL_PROGRESS:
            return self._on_tool_progress(payload)
        if event == _EV_TOOL_STARTED:
            return self._on_tool_started(payload)
        if event == _EV_TOOL_COMPLETED:
            return self._on_tool_completed(payload, failed=False)
        if event == _EV_TOOL_FAILED:
            return self._on_tool_completed(payload, failed=True)
        # Unknown native event: ignore (forward-compat).
        return []

    def finish(self) -> List[bytes]:
        """Emit the OpenAI terminating frame. Call once at stream end."""
        return [b"data: [DONE]\n\n"]

    # --- native event handlers -------------------------------------------

    def _on_delta(self, payload: Dict[str, Any]) -> List[bytes]:
        frames: List[bytes] = []
        # Native reasoning can also ride along inside the delta payload on some
        # providers; surface it as hermes.reasoning to mirror the OpenAI-wire
        # path the conversation-tree already parses (delta.reasoning_content).
        reasoning = payload.get("reasoning") or payload.get("reasoning_content")
        if reasoning:
            frames.append(_sse_frame({"text": reasoning}, event="hermes.reasoning"))
        delta = payload.get("delta")
        if delta:
            frames.append(_sse_frame(
                {"choices": [{"delta": {"content": delta}}]},
            ))
        return frames

    def _on_tool_progress(self, payload: Dict[str, Any]) -> List[bytes]:
        # Reasoning stream: tool_name == "_thinking" carries the model's
        # chain-of-thought. Map to hermes.reasoning so the thinking block shows.
        tool_name = payload.get("tool_name") or ""
        if tool_name == _REASONING_TOOL:
            text = payload.get("delta") or ""
            if text:
                return [_sse_frame({"text": text}, event="hermes.reasoning")]
            return []
        # A non-reasoning tool.progress: treat like a generic tool update.
        return self._emit_tool(
            tool_name,
            payload.get("preview") or "",
            payload.get("args"),
            status="running",
            failed=False,
        )

    def _on_tool_started(self, payload: Dict[str, Any]) -> List[bytes]:
        return self._emit_tool(
            payload.get("tool_name") or "tool",
            payload.get("preview") or "",
            payload.get("args"),
            status="running",
            failed=False,
        )

    def _on_tool_completed(self, payload: Dict[str, Any], *, failed: bool) -> List[bytes]:
        return self._emit_tool(
            payload.get("tool_name") or "tool",
            payload.get("preview") or "",
            payload.get("args"),
            status="failed" if failed else "completed",
            failed=failed,
        )

    # --- shared emit -----------------------------------------------------

    def _emit_tool(
        self,
        tool_name: str,
        label: str,
        args: Any,
        *,
        status: str,
        failed: bool,
    ) -> List[bytes]:
        # Pair running -> completed by a stable id via the in-flight stack.
        if status == "running":
            self._seq += 1
            tcid = f"tc_{self._seq}"
            self._stack.append((tcid, tool_name))
        else:
            tcid = ""
            for i in range(len(self._stack) - 1, -1, -1):
                if self._stack[i][1] == tool_name:
                    tcid = self._stack.pop(i)[0]
                    break
            if not tcid and self._stack:
                tcid = self._stack.pop(-1)[0]

        result = "" if status == "running" else (label if not failed else "")
        data = {
            "tool": tool_name,
            "emoji": _DEFAULT_EMOJI,
            "label": label or "",
            "toolCallId": tcid,
            "status": status,
        }
        if status != "running":
            data["result"] = result
        return [_sse_frame(data, event="hermes.tool.progress")]
