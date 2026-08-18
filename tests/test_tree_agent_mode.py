"""分支代理归属 (ikaros 单模式) 单元测试.

2026-08-18: hermes 任务代理模式整体退役, 对话树统一为 Ikaros 伴侣人格.
conversation_tree.set_agent 保留 'hermes' 值兼容存量节点, 但 server.py
的 /api/set_agent 仅接受 ikaros; 本测试验证 conversation_tree 层语义.

验证:
  A1. set_agent 写 node.agent 字段 (ikaros), 序列化重载后存活.
  A2. set_agent 非法值归默认 'ikaros'.
  A3. set_agent 不存在节点抛 ValueError.
  P1. build_system_prompt 任意 mode 统一返回 Ikaros 伴侣人格.
  P2. 两种 mode 提示一致 (不再有任务代理差异).
"""

import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core" / "conversation-tree"))

from memory_v5.conversation_tree import ConversationTree
import server as ct_server


class FakeStore:
    def __init__(self):
        self.records = {}
        self.next_id = 1

    def store(self, content, type="conversation", tags=""):
        mid = self.next_id
        self.next_id += 1
        self.records[mid] = (content, type, tags)
        return mid

    def load(self, ids):
        return {mid: self.records[mid][0] for mid in ids if mid in self.records}


def _new_tree():
    fake = FakeStore()
    tmp = tempfile.mkdtemp(prefix="tree_agent_")
    tree = ConversationTree(
        persist_key="test_tree_agent",
        data_dir=tmp,
        _store=fake.store,
        _load=fake.load,
    )
    return fake, tree


# ────────────── A: set_agent ──────────────
def test_a1_set_agent_writes_and_persists():
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    child = tree.add_turn([{"role": "user", "content": "more"}])
    tree.set_agent(child.id, "ikaros")
    assert tree.nodes[child.id].agent == "ikaros"
    raw = tree.serialize()
    t2 = ConversationTree.deserialize(
        raw, persist_key="test_tree_agent", data_dir=tree.data_dir,
    )
    assert t2.nodes[child.id].agent == "ikaros"


def test_a2_set_agent_invalid_falls_back():
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    child = tree.add_turn([{"role": "user", "content": "more"}])
    tree.set_agent(child.id, "bogus-agent")
    assert tree.nodes[child.id].agent == "ikaros"
    # 存量 hermes 值兼容保留 (仅 conversation_tree 层, server 端点已退役 hermes)
    tree.set_agent(child.id, "  HERMES ")
    assert tree.nodes[child.id].agent == "hermes"


def test_a3_set_agent_missing_raises():
    fake, tree = _new_tree()
    tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    try:
        tree.set_agent("nope", "ikaros")
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing node")


# ────────────── P: build_system_prompt ──────────────
def test_p1_system_prompt_ikaros():
    """2026-08-18: hermes 任务代理模式退役, build_system_prompt 对任意 mode 统一返回 Ikaros 人格。"""
    out = ct_server.build_system_prompt("ikaros")
    assert "伊卡洛斯" in out or "Ikaros" in out or "公理" in out or "SOUL" in out or "树形" in out


def test_p2_modes_unified():
    hermes = ct_server.build_system_prompt("hermes")
    ikaros = ct_server.build_system_prompt("ikaros")
    # hermes 退役后两种 mode 统一返回同一伴侣人格 (不再有任务代理提示差异)
    assert hermes == ikaros
    assert ("伊卡洛斯" in ikaros or "Ikaros" in ikaros or "树形" in ikaros
            or "公理" in ikaros or "SOUL" in ikaros)


# ────────────── D: 统一 ikaros 人格注入 ──────────────
def test_d1_legacy_hermes_node_gets_ikaros_persona():
    """存量 hermes 节点也注入统一 Ikaros 伴侣人格 (不再区分 agent 模式)。"""
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    node = tree.add_turn([{"role": "user", "content": "task please"}])
    tree.set_agent(node.id, "hermes")
    saved = ct_server._tree
    ct_server._tree = tree
    try:
        msgs = ct_server.build_chat_messages_v5(node.id, "do the thing")
    finally:
        ct_server._tree = saved
    sys_msgs = [m for m in msgs if m["role"] == "system"]
    assert sys_msgs, "expected at least one system message"
    joined = "\n".join(m["content"] for m in sys_msgs)
    assert ("伊卡洛斯" in joined or "Ikaros" in joined or "树形" in joined
            or "公理" in joined or "SOUL" in joined or "身份档案" in joined)


def test_d2_ikaros_mode_injects_persona():
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    ikaros_node = tree.add_turn([{"role": "user", "content": "chat"}])
    saved = ct_server._tree
    ct_server._tree = tree
    try:
        msgs = ct_server.build_chat_messages_v5(ikaros_node.id, "hey")
    finally:
        ct_server._tree = saved
    sys_msgs = [m for m in msgs if m["role"] == "system"]
    joined = "\n".join(m["content"] for m in sys_msgs)
    assert ("伊卡洛斯" in joined or "Ikaros" in joined or "树形" in joined
            or "公理" in joined or "SOUL" in joined or "身份档案" in joined)


if __name__ == "__main__":
    test_a1_set_agent_writes_and_persists()
    test_a2_set_agent_invalid_falls_back()
    test_a3_set_agent_missing_raises()
    test_p1_system_prompt_ikaros()
    test_p2_modes_unified()
    test_d1_legacy_hermes_node_gets_ikaros_persona()
    test_d2_ikaros_mode_injects_persona()
    print("ALL AGENT MODE TESTS PASSED")
