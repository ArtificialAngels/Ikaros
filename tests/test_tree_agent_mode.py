"""分支代理归属 (ekko-agent 模式) 单元测试.

验证:
  A1. set_agent 写 node.agent 字段 (ikaros/hermes), 序列化重载后存活.
  A2. set_agent 非法值归默认 'ikaros'.
  A3. set_agent 不存在节点抛 ValueError.
  P1. build_system_prompt('hermes') 返回 Hermes 任务代理提示.
  P2. build_system_prompt('ikaros') 返回 Ikaros 伴侣人格 (与 Hermes 提示不同).
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
    tree.set_agent(child.id, "hermes")
    assert tree.nodes[child.id].agent == "hermes"
    raw = tree.serialize()
    t2 = ConversationTree.deserialize(
        raw, persist_key="test_tree_agent", data_dir=tree.data_dir,
    )
    assert t2.nodes[child.id].agent == "hermes"


def test_a2_set_agent_invalid_falls_back():
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    child = tree.add_turn([{"role": "user", "content": "more"}])
    tree.set_agent(child.id, "bogus-agent")
    assert tree.nodes[child.id].agent == "ikaros"
    # 大小写/空白归一
    tree.set_agent(child.id, "  HERMES ")
    assert tree.nodes[child.id].agent == "hermes"


def test_a3_set_agent_missing_raises():
    fake, tree = _new_tree()
    tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    try:
        tree.set_agent("nope", "hermes")
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing node")


# ────────────── P: build_system_prompt ──────────────
def test_p1_hermes_prompt():
    out = ct_server.build_system_prompt("hermes")
    assert out == ct_server.HERMES_AGENT_PROMPT
    assert "Hermes" in out


def test_p2_ikaros_prompt_distinct():
    hermes = ct_server.build_system_prompt("hermes")
    ikaros = ct_server.build_system_prompt("ikaros")
    assert ikaros != hermes
    # Ikaros 伴侣人格应含身份标记 (公理/SOUL/心绪 任一, 或全部 fail-open 后的树形说明)
    assert ("伊卡洛斯" in ikaros or "Ikaros" in ikaros or "树形" in ikaros
            or "公理" in ikaros or "SOUL" in ikaros)


# ────────────── D: hermes 模式委托 (树域上下文 + 树域记忆, 不重复注入完整 SOUL) ──────────────
def test_d1_hermes_mode_delegates_no_soul_or_v5():
    """hermes 模式注入树域上下文(分支脉络) + 树域记忆, **不注入完整 SOUL**:
    gateway 的 core system 已含 SOUL.md + AGENTS.md + ikaros_v5 记忆, 外部 system 消息
    会叠加在 core 之后 (不替换), 故树端只补它没有的: 分支位置 + 树域记忆, 人格不重复注入."""
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    hermes_node = tree.add_turn([{"role": "user", "content": "task please"}])
    tree.set_agent(hermes_node.id, "hermes")
    saved = ct_server._tree
    ct_server._tree = tree
    try:
        msgs = ct_server.build_chat_messages_v5(hermes_node.id, "do the thing")
    finally:
        ct_server._tree = saved
    sys_msgs = [m for m in msgs if m["role"] == "system"]
    assert sys_msgs, "expected at least one system message"
    joined = "\n".join(m["content"] for m in sys_msgs)
    # 含树域上下文: 告知 Hermes 这是对话树分支 + 当前分支脉络 (路径摘要 + agent 归属)
    assert "conversation tree" in joined
    assert "Current branch path" in joined
    assert "Current node agent: hermes" in joined
    # 绝不含 tree 自行注入的完整 SOUL 身份 / Hermes 任务代理提示 (人格由 gateway core 提供)
    assert "[身份档案 SOUL]" not in joined
    assert "autonomous task agent" not in joined


def test_d2_ikaros_mode_injects_persona():
    """对照: ikaros 模式仍自行注入伴侣人格 (SOUL/公理/树形说明)."""
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
    test_p1_hermes_prompt()
    test_p2_ikaros_prompt_distinct()
    test_d1_hermes_mode_delegates_no_soul_or_v5()
    test_d2_ikaros_mode_injects_persona()
    print("ALL AGENT MODE TESTS PASSED")
