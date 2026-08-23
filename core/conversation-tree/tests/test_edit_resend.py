"""server.py /api/chat 编辑重发链路 (2026-08-23).

覆盖前端 editAndResend → colour chatStream(edit_of) 落库:
- edit_of 经 /api/chat 校验后传入 add_turn, 新节点 meta.edit_source 持久化, 独立成卡
- 非法 edit_of (node_id 不存在) 静默忽略, 不落编辑源, 不报错
"""
from __future__ import annotations

import json

import pytest

from conftest import server, http_get, http_post  # noqa: E402


def _sse_events(body):
    """/api/chat 返回 SSE (text/event-stream), 逐行解析 data: JSON 事件."""
    events = []
    for line in str(body).splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


@pytest.fixture
def mock_chat_stream(monkeypatch):
    """替换 _chat_stream_events, 让 /api/chat 不真调 LLM, 只产出内容并落库."""
    def _install(reply="编辑后回答"):
        def fake_stream(messages, mode, node_id, collector, tree=None):
            collector["content"] += reply
            collector["usage"] = {"total_tokens": 2}
            return [{"type": "content", "text": reply},
                    {"type": "usage", "usage": {"total_tokens": 2}}]
        monkeypatch.setattr(server, "_chat_stream_events", fake_stream)
    return _install


def _first_user_node(state):
    """返回第一个含 user 消息的非 root 节点."""
    for n in state["nodes"]:
        if n.get("depth", 0) > 0 and (n.get("messages") or []):
            msgs = [m for m in n["messages"] if m.get("role") != "system"]
            if any(m.get("role") == "user" for m in msgs):
                return n, msgs
    return None, None


def test_edit_resend_through_chat(http_server, mock_chat_stream):
    base = http_server
    # 建空会话 (ensure_tree 首次是 demo 树 → 应先建独立空树再断言卡片数)
    st0, s0 = http_post(base, "/api/sessions/create", {})
    assert st0 == 200
    root_id = s0["state"]["root_id"]
    # 连续对话 2 轮 → 主线 1 张卡
    for i, q in enumerate(["第一问", "第二问"]):
        st, body = http_post(base, "/api/chat", {"message": q})
        assert st == 200
        evs = _sse_events(body)
        assert evs and evs[-1]["type"] == "done"
    _, state1 = http_get(base, "/api/state?inline=1")
    assert len(state1["cards"]) == 1, [c["id"] for c in state1["cards"]]

    n, msgs = _first_user_node(state1)
    assert n is not None
    _u = next(m for m in msgs if m["role"] == "user")
    original_text = _u["content"]
    assert original_text == "第一问"

    # 编辑重发: force_branch + edit_of → 编辑 "第一问"
    edit_node_id = n["id"]
    st, body = http_post(base, "/api/chat", {
        "message": "第一问(改)",
        "parent_id": root_id,
        "force_branch": True,
        "edit_of": {"node_id": edit_node_id, "message_index": 0},
    })
    assert st == 200
    evs = _sse_events(body)
    assert evs and evs[-1]["type"] == "done"

    _, state2 = http_get(base, "/api/state?inline=1")
    by_id = {x["id"]: x for x in state2["nodes"]}
    edited = next(x for x in state2["nodes"]
                  if (x.get("messages") or []) and any(
                      isinstance(m.get("content"), str) and "第一问(改)" in m.get("content", "")
                      for m in x["messages"]))
    # 编辑源持久化到新节点 (注意 json 解码后 meta 内是 snake 结构直接透传)
    src = (edited.get("meta") or {}).get("edit_source")
    assert src == {"node_id": edit_node_id, "message_index": 0}, src
    # 独立成卡: 卡片数 2 (主线 + 编辑重发卡)
    assert len(state2["cards"]) == 2, [c["id"] for c in state2["cards"]]
    edit_card = next(c for c in state2["cards"] if edited["id"] in (c.get("node_ids") or []))
    assert edit_card["id"] == "card_" + edited["id"]


def test_edit_resend_ignores_bad_source(http_server, mock_chat_stream):
    """非法 edit_of (node_id 不存在) → 静默忽略, 不落编辑源, 不报错."""
    base = http_server
    st0, _ = http_post(base, "/api/sessions/create", {})
    assert st0 == 200
    _, s0 = http_get(base, "/api/state?inline=1")
    _, body = http_post(base, "/api/chat", {"message": "第一问"})
    assert _sse_events(body)[-1]["type"] == "done"
    st, body2 = http_post(base, "/api/chat", {
        "message": "再问",
        "force_branch": True,
        "edit_of": {"node_id": "nope_not_exists", "message_index": 0},
    })
    assert st == 200
    _, s2 = http_get(base, "/api/state?inline=1")
    nodes = [x for x in s2["nodes"] if x.get("meta") and x["meta"].get("edit_source")]
    assert nodes == [], "非法 edit_of 不应持久化编辑源"