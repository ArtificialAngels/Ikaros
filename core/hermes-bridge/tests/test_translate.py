"""Unit tests for core/hermes-bridge/translate.py.

Verifies the translator maps Hermes' NATIVE (pristine) session-chat SSE into
the exact OpenAI-wire frames the conversation-tree parser
(core/conversation-tree/server.py::_stream_hermes_gateway) consumes:

    event: hermes.reasoning        {"text": ...}
    event: hermes.tool.progress    {"tool","emoji","label","toolCallId",
                                     "status","result"}
    data: {choices:[{delta:{content}}]}
    data: [DONE]

Run: python -m pytest core/hermes-bridge/tests/test_translate.py -q
"""

import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = os.path.join(_HERE, "..", "translate.py")


def _load():
    spec = importlib.util.spec_from_file_location("hermes_bridge_translate", _SPEC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


translate = _load()
SSETranslator = translate.SSETranslator


def _parse_frames(blob: bytes):
    """Split a byte blob of SSE frames into (event, data_dict) tuples,
    matching how the conversation-tree parser reads them."""
    out = []
    buf = blob.decode("utf-8")
    evt = None
    data_lines = []
    for line in buf.split("\n"):
        if line.startswith("event:"):
            evt = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        elif line == "":
            if data_lines:
                raw = "\n".join(data_lines)
                out.append((evt, raw))
            evt = None
            data_lines = []
    return out


def _feed(translator, events):
    blob = b""
    for name, payload in events:
        for frame in translator.feed(name, payload):
            blob += frame
    for frame in translator.finish():
        blob += frame
    return _parse_frames(blob)


def test_content_delta_emitted_as_openai_chunk():
    t = SSETranslator()
    frames = _feed(t, [("assistant.delta", {"delta": "hello world"})])
    # One content data frame + the [DONE] terminator (finish() appends it).
    assert frames[0] == (None, '{"choices": [{"delta": {"content": "hello world"}}]}')
    assert frames[-1] == (None, "[DONE]")
    assert len(frames) == 2


def test_reasoning_native_tool_progress_becomes_hermes_reasoning():
    t = SSETranslator()
    frames = _feed(t, [
        ("tool.progress", {"tool_name": "_thinking", "delta": "let me think..."}),
    ])
    # reasoning frame + [DONE] terminator.
    assert len(frames) == 2
    evt, raw = frames[0]
    assert evt == "hermes.reasoning"
    assert raw == '{"text": "let me think..."}'
    assert frames[-1] == (None, "[DONE]")


def test_tool_lifecycle_running_then_completed_paired_by_id():
    t = SSETranslator()
    frames = _feed(t, [
        ("tool.started", {"tool_name": "web_search", "preview": "query=foo", "args": {}}),
        ("tool.completed", {"tool_name": "web_search", "preview": "result text", "args": {}}),
    ])
    # Two hermes.tool.progress frames (+ [DONE] terminator); ids must match.
    assert len(frames) == 3
    assert all(e == "hermes.tool.progress" for e, _ in frames[:2])
    import json
    a = json.loads(frames[0][1])
    b = json.loads(frames[1][1])
    assert a["status"] == "running"
    assert b["status"] == "completed"
    assert a["toolCallId"] == b["toolCallId"]
    assert a["tool"] == "web_search"
    assert b["result"] == "result text"
    assert a["emoji"] == "🔧"


def test_tool_failed_status():
    t = SSETranslator()
    frames = _feed(t, [
        ("tool.started", {"tool_name": "x", "preview": "p", "args": {}}),
        ("tool.failed", {"tool_name": "x", "preview": "boom", "args": {}}),
    ])
    import json
    b = json.loads(frames[1][1])
    assert b["status"] == "failed"


def test_control_events_dropped_and_done_emitted():
    t = SSETranslator()
    frames = _feed(t, [
        ("run.started", {}),
        ("message.started", {}),
        ("assistant.delta", {"delta": "x"}),
        ("assistant.completed", {}),
        ("run.completed", {}),
        ("done", {}),
    ])
    # one content chunk + the [DONE] terminator
    assert frames[0] == (None, '{"choices": [{"delta": {"content": "x"}}]}')
    assert frames[-1] == (None, "[DONE]")


def test_tool_describe_not_filtered_here_but_shape_ok():
    # The conversation-tree filters tool_describe*; the translator only maps
    # shape. Ensure a generic tool still pairs correctly even if name matches.
    t = SSETranslator()
    frames = _feed(t, [
        ("tool.started", {"tool_name": "a", "preview": "1", "args": {}}),
        ("tool.started", {"tool_name": "a", "preview": "2", "args": {}}),
        ("tool.completed", {"tool_name": "a", "preview": "r2", "args": {}}),
        ("tool.completed", {"tool_name": "a", "preview": "r1", "args": {}}),
    ])
    import json
    # Only the hermes.tool.progress frames carry toolCallId; the trailing [DONE]
    # terminator is not JSON-parseable as a tool card.
    tool_frames = [f for f in frames if f[0] == "hermes.tool.progress"]
    ids = [json.loads(f[1])["toolCallId"] for f in tool_frames]
    # LIFO pairing: the most-recent completion pairs with the most-recent
    # still-in-flight start. With [s1, s2, c1, c2]: c1 pairs s2, c2 pairs s1.
    assert ids[1] == ids[2]   # second start (s2) pairs first completion (c1)
    assert ids[0] == ids[3]   # first start (s1) pairs second completion (c2)
