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
    event: error               data: {"message": "..."}            # runtime failure,
                                                                   # forwarded verbatim
    event: assistant.completed data: {"content": "..."}            # failure carrier:
                                                                   # forwarded as
                                                                   # event:error when
                                                                   # no delta preceded

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
# NOTE: ``assistant.completed`` is NOT unconditionally droppable: the gateway
# surfaces agent-internal failures (401 / provider auth / quota) as the
# completed message's ``content`` text rather than an ``event: error`` frame.
# We drop it only when content was already streamed via assistant.delta
# (normal completion); otherwise its non-empty content is the ONLY carrier of
# the failure and must be forwarded (see ``_on_assistant_completed``).
_DROP_EVENTS = frozenset({
    "run.started", "message.started", "assistant.completed",
    "run.completed", "done",
})

# Upstream *runtime* error frame (gateway ``_run_agent`` failure — e.g. the
# ``auth.lock`` Permission-denied case). This is NOT a control event: it carries
# the real failure reason and must reach the conversation-tree so its warn /
# degradation message stops being the generic "空响应". We pass it through
# verbatim (normalizing the upstream ``{message: ...}`` shape into the
# ``{error: ...}`` shape the conversation-tree ``_flush`` already parses).
_EV_ERROR = "error"
_EV_ASSISTANT_COMPLETED = "assistant.completed"

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
        # True once ANY content delta has been forwarded. Used to decide
        # whether an ``assistant.completed`` content is a duplicate of already
        # streamed text (drop) or the sole carrier of a failure (forward).
        self._content_emitted = False

    def feed(self, event: str, payload: Dict[str, Any]) -> List[bytes]:
        if event == _EV_ASSISTANT_COMPLETED:
            return self._on_assistant_completed(payload)
        if event in _DROP_EVENTS:
            return []
        if event == _EV_ERROR:
            # 透传上游运行时错误：把 {message:...} 规范成 {error:...}，
            # 让 conversation-tree 的 event:error 消费逻辑直接命中。
            err = payload.get("error") or payload.get("message") or "unknown upstream error"
            normalized = {
                "error": err,
                "type": payload.get("type", "upstream_error"),
            }
            return [_sse_frame(normalized, event=_EV_ERROR)]
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
            self._content_emitted = True
            frames.append(_sse_frame(
                {"choices": [{"delta": {"content": delta}}]},
            ))
        return frames

    def _on_assistant_completed(self, payload: Dict[str, Any]) -> List[bytes]:
        """Forward the completed message's content when it is the ONLY text.

        The gateway puts agent-internal failures (401 / provider auth /
        quota / permission) into the completed message's ``content`` instead
        of raising — and in that failure path NO ``assistant.delta`` content
        was ever emitted, so the completed ``content`` is the sole carrier of
        the error. Forwarding it as ``event: error`` lets the conversation-tree
        surface the real reason instead of degrading with "空响应".

        On a healthy stream the delta frames already carried the text, so this
        content is a duplicate and must stay dropped.
        """
        content = payload.get("content")
        if content and not self._content_emitted:
            return [_sse_frame({"error": content}, event=_EV_ERROR)]
        return []

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
