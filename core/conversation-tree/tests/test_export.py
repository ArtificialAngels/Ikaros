"""core/conversation-tree/server.py 会话导出 pytest 测试 (任务1).

覆盖 (对标 hermes-studio ExportCompressor):
- GET /api/sessions/<id>/export?format=json   完整 JSON (节点内联 messages + thinking/tool_calls/usage)
- GET /api/sessions/<id>/export?format=txt    压缩可读文本
- 404 (会话不存在) / 400 (format 非法)
- 往返闭环: 导出 JSON → POST /api/sessions/import → 新会话消息历史与导出一致
"""
from __future__ import annotations

import json

import pytest
import memory_v5.conversation_tree as ct

from conftest import server, http_get, http_post  # type: ignore  # noqa: E402


def _create_session_with_turns(http_server):
    """新建会话并向活动树写入 2 个节点 (含思考/工具/用量), 返回 (sid, [node_id...])."""
    _, created = http_post(http_server, "/api/sessions/create")
    sid = created["active_id"]
    t = server._tree
    n1 = t.add_turn(
        messages=[
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀！这是回复。"},
        ],
        branch_label="main", title="问候",
        thinking="思考过程 123",
        usage={"total_tokens": 42, "prompt_tokens": 12, "completion_tokens": 30},
        tool_calls=[ct.ToolCall(id="t1", name="memory_search",
                                params={"q": "hi"}, result_summary="ok",
                                success=True)],
    )
    n2 = t.add_turn(
        messages=[
            {"role": "user", "content": "再问一句"},
            {"role": "assistant", "content": "再答一句。"},
        ],
        branch_label="main", title="追问",
    )
    return sid, [n1.id, n2.id]


# ────────────────── GET /api/sessions/:id/export?format=json ──────────────────

def test_export_json_via_http(http_server):
    """JSON 导出: 完整消息历史内联 + 节点元数据 (thinking/tool_calls/usage) + import 兼容结构."""
    sid, nids = _create_session_with_turns(http_server)
    status, data = http_get(http_server, f"/api/sessions/{sid}/export?format=json")
    assert status == 200
    assert data["session_id"] == sid
    assert data["name"]  # 会话标题
    assert "exported_at" in data
    tree = data["tree"]
    # import 兼容结构
    assert tree["schema"] == "super-conv-2.0"
    assert isinstance(tree["nodes"], list) and len(tree["nodes"]) >= 3  # root + 2
    # 消息内联 + v5_memory_id 清零 (自包含)
    nodes = {n["id"]: n for n in tree["nodes"]}
    assert nodes[nids[0]]["messages"] == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀！这是回复。"},
    ]
    assert nodes[nids[0]]["v5_memory_id"] == 0
    # 元数据完整: thinking / tool_calls / usage
    assert nodes[nids[0]]["thinking"] == "思考过程 123"
    assert nodes[nids[0]]["usage"]["total_tokens"] == 42
    tc = nodes[nids[0]]["tool_calls"][0]
    assert tc["name"] == "memory_search" and tc["params"]["q"] == "hi"
    # 无消息节点: messages = []
    assert nodes[nids[1]]["messages"] == [
        {"role": "user", "content": "再问一句"},
        {"role": "assistant", "content": "再答一句。"},
    ]


def test_export_json_default_format(http_server):
    """不传 format 默认 json."""
    sid, _ = _create_session_with_turns(http_server)
    status, data = http_get(http_server, f"/api/sessions/{sid}/export")
    assert status == 200
    assert data["session_id"] == sid and "tree" in data


# ────────────────── GET /api/sessions/:id/export?format=txt ──────────────────

def test_export_txt_via_http(http_server):
    """TXT 导出: 可读文本, 含会话标题与消息正文."""
    sid, nids = _create_session_with_turns(http_server)
    status, text = http_get(http_server, f"/api/sessions/{sid}/export?format=txt")
    assert status == 200
    assert isinstance(text, str)
    # 元数据 + 消息内容
    assert "对话树导出" in text
    assert "你好呀！这是回复。" in text
    assert "思考过程 123" in text
    assert "memory_search" in text
    assert "42 tokens" in text


# ────────────────── 错误分支 ──────────────────

def test_export_nonexistent_session_404(http_server):
    status, data = http_get(http_server, "/api/sessions/sess_ghost/export?format=json")
    assert status == 404
    assert "error" in data


def test_export_bad_format_400(http_server):
    sid, _ = _create_session_with_turns(http_server)
    status, data = http_get(http_server, f"/api/sessions/{sid}/export?format=xml")
    assert status == 400
    assert "format" in data["error"]


# ────────────────── 往返闭环: export → import ──────────────────

def test_export_import_roundtrip_via_http(http_server):
    """导出 JSON 经 /api/sessions/import 导回为新会话, 消息历史/思考/工具/用量一致."""
    sid, nids = _create_session_with_turns(http_server)
    _, payload = http_get(http_server, f"/api/sessions/{sid}/export?format=json")

    # 去掉 session_id → import 生成全新会话 (验证自包含, 不依赖原 store id)
    import_body = {"tree": payload["tree"], "name": "回导会话"}
    status, res = http_post(http_server, "/api/sessions/import", import_body)
    assert status == 200 and res.get("ok") is True
    new_sid = res["id"]
    assert new_sid != sid
    assert res["node_count"] >= 3
    assert res["migrated"] >= 2  # 两个含 messages 的节点应重新入库

    # 切到新会话并取 state
    status, sw = http_post(http_server, "/api/sessions/switch", {"id": new_sid})
    assert status == 200 and sw["active_id"] == new_sid
    _, st = http_get(http_server, "/api/state")
    nodes = {n["id"]: n for n in st["nodes"]}
    # 拓扑 id 保留 (export 未改写 id)
    assert nids[0] in nodes and nids[1] in nodes
    # 消息历史一致
    assert nodes[nids[0]]["messages"] == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀！这是回复。"},
    ]
    assert nodes[nids[1]]["messages"] == [
        {"role": "user", "content": "再问一句"},
        {"role": "assistant", "content": "再答一句。"},
    ]
    # 思考 / 工具 / 用量一致
    assert nodes[nids[0]]["thinking"] == "思考过程 123"
    assert nodes[nids[0]]["usage"]["total_tokens"] == 42
    assert nodes[nids[0]]["tool_calls"][0]["name"] == "memory_search"
