"""Tests for hermes-bridge server.py pure logic + native→dialect translation.

Run: pytest core/hermes-bridge/tests/test_server.py
(or plain python: python core/hermes-bridge/tests/test_server.py)
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BRIDGE = os.path.dirname(_HERE)
if _BRIDGE not in sys.path:
    sys.path.insert(0, _BRIDGE)

from server import (  # noqa: E402
    derive_conv_id,
    dispatch_native,
    parse_messages,
    safe_session_id,
)
from translate import SSETranslator  # noqa: E402


def _parse_frames(blob: bytes) -> list:
    out = []
    evt = None
    dl = []
    for line in blob.decode("utf-8").split("\n"):
        if line.startswith("event:"):
            evt = line[6:].strip()
        elif line.startswith("data:"):
            dl.append(line[5:].strip())
        elif line == "":
            if dl:
                out.append((evt, "\n".join(dl)))
            evt = None
            dl = []
    return out


def test_parse_messages_system_first_last():
    msgs = [
        {"role": "system", "content": "persona-A"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "last"},
        {"role": "system", "content": "ctx-B"},
    ]
    system, first, last = parse_messages(msgs)
    assert system == "persona-A\nctx-B"
    assert first == "first"
    assert last == "last"


def test_parse_messages_multimodal_flatten():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "url": "x"}]}]
    system, first, last = parse_messages(msgs)
    assert "hi" in last


def test_derive_conv_id_stable_and_distinct():
    a1 = derive_conv_id("sys", "first-user")
    a2 = derive_conv_id("sys", "first-user")
    b = derive_conv_id("sys", "different")
    assert a1 == a2
    assert a1 != b
    assert a1.startswith("fp_")


def test_safe_session_id():
    sid = safe_session_id("my conv/Id#1")
    assert sid.startswith("ikaros-")
    assert all(c.isalnum() or c in ("_", "-") for c in sid)
    assert len(sid) <= 180


def test_dispatch_native_content():
    t = SSETranslator()
    frames = dispatch_native("assistant.delta", json.dumps({"delta": "hi"}), t)
    assert frames == [b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n']


def test_dispatch_native_reasoning():
    t = SSETranslator()
    frames = dispatch_native("tool.progress", json.dumps({"tool_name": "_thinking", "delta": "think"}), t)
    assert frames == [b'event: hermes.reasoning\ndata: {"text": "think"}\n\n']


def test_dispatch_native_tool_lifecycle():
    t = SSETranslator()
    frames = []
    frames += dispatch_native("tool.started", json.dumps({"tool_name": "web", "preview": "q", "args": {}}), t)
    frames += dispatch_native("tool.completed", json.dumps({"tool_name": "web", "preview": "res", "args": {}}), t)
    parsed = _parse_frames(b"".join(frames))
    assert len(parsed) == 2
    assert all(e == "hermes.tool.progress" for e, _ in parsed)
    a, b_ = json.loads(parsed[0][1]), json.loads(parsed[1][1])
    assert a["status"] == "running" and b_["status"] == "completed"
    assert a["toolCallId"] == b_["toolCallId"] and b_["result"] == "res"


def test_dispatch_native_drops_control():
    t = SSETranslator()
    frames = dispatch_native("run.started", json.dumps({"x": 1}), t)
    assert frames == []


def test_full_native_stream_dialect():
    """模拟一整段 Hermes 原生 session-chat SSE, 验证翻译后正是对话树消费的 dialect."""
    t = SSETranslator()
    native = [
        ("run.started", {"user_message": {"role": "user", "content": "hi"}}),
        ("assistant.delta", {"delta": "Hello"}),
        ("tool.progress", {"tool_name": "_thinking", "delta": "let me think"}),
        ("tool.started", {"tool_name": "calc", "preview": "1+1", "args": {}}),
        ("tool.completed", {"tool_name": "calc", "preview": "2", "args": {}}),
        ("assistant.delta", {"delta": " there"}),
        ("done", {}),
    ]
    out = b""
    for evt, payload in native:
        for f in dispatch_native(evt, json.dumps(payload), t):
            out += f
    for f in t.finish():
        out += f

    parsed = _parse_frames(out)
    events = [e for e, _ in parsed]
    # 控制事件被丢弃, 只剩 reasoning / tool.progress / content / DONE
    assert "run.started" not in events
    assert "done" not in events
    assert "hermes.reasoning" in events
    assert "hermes.tool.progress" in events
    # 末帧是 [DONE]
    assert parsed[-1] == (None, "[DONE]")
    # reasoning 文本正确
    reason = [json.loads(d)["text"] for e, d in parsed if e == "hermes.reasoning"]
    assert reason == ["let me think"]
    # 正文拼接正确
    contents = [json.loads(d)["choices"][0]["delta"]["content"] for e, d in parsed if e is None and "choices" in d]
    assert contents == ["Hello", " there"]


if __name__ == "__main__":
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {e}")
    sys.exit(1 if failed else 0)
