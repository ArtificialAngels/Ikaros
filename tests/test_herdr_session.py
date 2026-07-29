"""B3 单元测试：SessionRegistry + SessionBridge（mock herdr client，无外部依赖）。

运行：PYTHONPATH=E:/Ikaros/core E:/Ikaros/runtime/portable-python/python.exe -m pytest tests/test_herdr_session.py -q
"""

import os
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from herdr.session import (  # noqa: E402
    SessionRegistry,
    SessionBridge,
    map_agent_status,
)


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #
class FakeTree:
    """记录 set_exec_state 调用；可模拟节点不存在（KeyError）。"""

    def __init__(self, persist_key="ui_conversation_tree", valid=None):
        self.persist_key = persist_key
        self.valid = set(valid) if valid is not None else None
        self.calls = []

    def set_exec_state(self, node_id, state, progress=None, detail=None, **kw):
        if self.valid is not None and node_id not in self.valid:
            raise KeyError("node not found: %s" % node_id)
        self.calls.append({"node_id": node_id, "state": state, "detail": detail})
        return node_id


class MockHerdrClient:
    """可控的 HerdrClient 替身：snapshot 返回固定结构，subscribe 后台推送事件。"""

    def __init__(self, snapshot, events=None):
        self._snapshot = snapshot
        self._events = events or []
        self._handler = None
        self.closed = False
        self.subscribed = False

    def session_snapshot(self):
        return self._snapshot

    def subscribe(self, subscriptions, handler=None):
        self.subscribed = True
        self._handler = handler
        if self._events:

            def push():
                for e in self._events:
                    if self._handler:
                        self._handler(e)

            threading.Thread(target=push, daemon=True).start()
        return {"ok": True}

    def close(self):
        self.closed = True


SNAPSHOT = {
    "type": "session_snapshot",
    "snapshot": {
        "workspaces": [
            {"workspace_id": "w1", "agent_status": "working"},
            {"workspace_id": "w2", "agent_status": "unknown"},  # 无真实状态 -> 不写
        ],
        "tabs": [
            {"tab_id": "w1:t1", "workspace_id": "w1", "agent_status": "unknown"},
        ],
        # herdr 真实：panes 为扁平列表，每个自带 workspace_id
        "panes": [
            {"pane_id": "p21", "workspace_id": "w2", "agent_status": "blocked"},
            {"pane_id": "p22", "workspace_id": "w2", "agent_status": "done"},
            # 未知 workspace（无绑定）-> 应被忽略
            {"pane_id": "p99", "workspace_id": "w9", "agent_status": "working"},
        ],
        "agents": [],
    },
}


# --------------------------------------------------------------------------- #
# map_agent_status
# --------------------------------------------------------------------------- #
def test_map_agent_status_normalization():
    assert map_agent_status("working") == "working"
    assert map_agent_status("running") == "working"   # 同义
    assert map_agent_status("needs_input") == "blocked"
    assert map_agent_status("exited") == "done"
    assert map_agent_status("") == "unknown"
    assert map_agent_status("weird") == "unknown"
    assert map_agent_status(None) == "unknown"


# --------------------------------------------------------------------------- #
# SessionRegistry
# --------------------------------------------------------------------------- #
def test_registry_bind_lookup_persist_reload(tmp_path):
    path = str(tmp_path / "bindings.json")
    reg = SessionRegistry(path)
    reg.bind_session("ui_conversation_tree", herdr_socket="C:/x/herdr.sock", herdr_session="main")
    reg.bind_workspace("ui_conversation_tree", "w1", "n_batch1", label="batch1", root_pane_id="p1")
    reg.bind_pane("ui_conversation_tree", "w1", "p1a", "n_sub1")

    # 同进程查询
    b = reg.lookup_session("ui_conversation_tree")
    assert b is not None and b.herdr_socket == "C:/x/herdr.sock"
    assert reg.lookup_by_workspace("w1")[1].node_id == "n_batch1"
    assert reg.lookup_by_pane("p1a") == ("ui_conversation_tree", "w1", "n_sub1")

    # 重载（新实例读同一文件）
    reg2 = SessionRegistry(path)
    assert reg2.lookup_by_workspace("w1")[1].node_id == "n_batch1"
    assert reg2.lookup_by_pane("p1a")[2] == "n_sub1"

    # 解绑
    assert reg2.unbind_session("ui_conversation_tree") is True
    assert reg2.lookup_session("ui_conversation_tree") is None


def test_registry_resolve_socket_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv("HERDR_SOCKET_PATH", raising=False)
    monkeypatch.delenv("HERDR_SESSION", raising=False)
    path = str(tmp_path / "b2.json")
    reg = SessionRegistry(path)
    # 无绑定 -> 走默认解析（含测试环境可能的 env）
    s_default = reg.resolve_socket("ui_conversation_tree")
    # 绑定显式 socket -> 优先
    reg.bind_session("ui_conversation_tree", herdr_socket="C:/explicit/herdr.sock")
    assert reg.resolve_socket("ui_conversation_tree") == "C:/explicit/herdr.sock"
    # 未设显式 socket 但设了 env -> env 生效
    reg.unbind_session("ui_conversation_tree")
    monkeypatch.setenv("HERDR_SOCKET_PATH", "C:/env/herdr.sock")
    assert reg.resolve_socket("ui_conversation_tree") == "C:/env/herdr.sock"
    assert s_default  # 默认解析应返回非空路径


# --------------------------------------------------------------------------- #
# SessionBridge
# --------------------------------------------------------------------------- #
def test_bridge_resync_snapshot_to_exec_state():
    tree = FakeTree(valid={"n_batch1", "n_batch2", "n_sub"})
    reg = SessionRegistry(os.path.join(tempfile.mkdtemp(), "b.json"))
    reg.bind_workspace("ui_conversation_tree", "w1", "n_batch1")
    reg.bind_workspace("ui_conversation_tree", "w2", "n_batch2")
    reg.bind_pane("ui_conversation_tree", "w2", "p21", "n_sub")

    client = MockHerdrClient(SNAPSHOT)
    bridge = SessionBridge(client, tree, registry=reg, ikaros_session="ui_conversation_tree")
    applied = bridge.resync()

    by_node = {c["node_id"]: c["state"] for c in tree.calls}
    assert by_node.get("n_batch1") == "working"      # workspace 级 agent
    assert by_node.get("n_sub") == "blocked"         # pane 级绑定
    assert by_node.get("n_batch2") == "done"         # p22 无 pane 绑定 -> 回落 workspace 级
    assert "w9" not in applied                       # 未知 workspace 被忽略
    assert len(tree.calls) == 3


def test_bridge_resync_skips_invalid_node():
    tree = FakeTree(valid={"n_batch1"})  # 仅 n_batch1 有效
    reg = SessionRegistry(os.path.join(tempfile.mkdtemp(), "b.json"))
    reg.bind_workspace("ui_conversation_tree", "w1", "n_batch1")
    reg.bind_workspace("ui_conversation_tree", "w2", "n_missing")  # 节点不存在
    client = MockHerdrClient(SNAPSHOT)
    bridge = SessionBridge(client, tree, registry=reg)
    bridge.resync()  # 不应抛异常（无效节点被容错跳过）
    assert [c["node_id"] for c in tree.calls] == ["n_batch1"]


def test_bridge_attach_event_incremental():
    tree = FakeTree(valid={"n3", "n3b"})
    reg = SessionRegistry(os.path.join(tempfile.mkdtemp(), "b.json"))
    reg.bind_workspace("ui_conversation_tree", "w3", "n3")
    reg.bind_pane("ui_conversation_tree", "w3", "p3", "n3b")

    events = [
        {"id": "sub_1", "method": "events",
         "params": {"event": "pane.agent_status_changed",
                    "data": {"pane_id": "p3", "status": "done"}}},
        {"id": "sub_1", "method": "events",
         "params": {"event": "agent.status_changed",
                    "data": {"workspace_id": "w3", "status": "working"}}},
        # 未知 -> 不应触发任何 set_exec_state
        {"id": "sub_1", "method": "events",
         "params": {"event": "pane.agent_status_changed",
                    "data": {"pane_id": "p_unknown", "status": "working"}}},
    ]
    client = MockHerdrClient(SNAPSHOT, events=events)
    bridge = SessionBridge(client, tree, registry=reg)
    bridge.attach()

    assert client.subscribed is True
    # 等后台推送线程跑完
    import time
    time.sleep(0.2)
    bridge.detach()
    assert client.closed is True

    by_node = {c["node_id"]: c["state"] for c in tree.calls}
    # attach 先 resync（w3 不在 SNAPSHOT 中 -> 无调用），再收 2 个有效事件
    assert by_node.get("n3b") == "done"
    assert by_node.get("n3") == "working"
    assert len([c for c in tree.calls if c["node_id"] in ("n3", "n3b")]) == 2
