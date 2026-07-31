"""rename_node / delete_node 单元测试.

验证:
  R1. rename 写入 meta.title, 序列化后重载仍存活.
  R2. 空串清除 meta.title (回退自动摘要).
  R3. 重命名不存在节点抛 ValueError.
  D1. 删除叶子节点: 从父 children 移除, 节点消失.
  D2. 删除带子节点的节点: 子节点重挂父节点 (parent_id + children 同步, 深度递归重算).
  D3. 删除根节点抛 ValueError.
  D4. 删除当前节点: current_id 重指父节点 (或 root).
  D5. 删除不影响关联 v5 记忆 (FakeStore 记录仍在, 留孤儿记忆).
"""

import sys
import tempfile

sys.path.insert(0, "E:/Ikaros/core")

from memory_v5.conversation_tree import ConversationTree


class FakeStore:
    def __init__(self):
        self.records = {}          # mid -> (content, type, tags)
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
    tmp = tempfile.mkdtemp(prefix="tree_node_ops_")
    tree = ConversationTree(
        persist_key="test_tree",
        data_dir=tmp,
        _store=fake.store,
        _load=fake.load,
    )
    return fake, tree


# ────────────── R: rename_node ──────────────
def test_r1_rename_writes_and_persists():
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    child = tree.add_turn([{"role": "user", "content": "more"}])
    tree.rename_node(child.id, "我的分支")
    assert tree.nodes[child.id].meta.get("title") == "我的分支"
    # 序列化重载后仍存活
    raw = tree.serialize()
    tree2 = ConversationTree.deserialize(
        raw, persist_key="test_tree", data_dir=tree.data_dir,
    )
    assert tree2.nodes[child.id].meta.get("title") == "我的分支"


def test_r2_empty_clears_title():
    fake, tree = _new_tree()
    tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    child = tree.add_turn([{"role": "user", "content": "more"}])
    tree.rename_node(child.id, "标题")
    assert tree.nodes[child.id].meta.get("title") == "标题"
    tree.rename_node(child.id, "   ")
    assert "title" not in tree.nodes[child.id].meta


def test_r3_rename_missing_raises():
    fake, tree = _new_tree()
    tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    try:
        tree.rename_node("nope", "x")
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing node rename")


# ────────────── D: delete_node ──────────────
def test_d1_delete_leaf():
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    child = tree.add_turn([{"role": "user", "content": "c"}])
    assert child.id in tree.nodes[root.id].children
    tree.delete_node(child.id)
    assert child.id not in tree.nodes
    assert child.id not in tree.nodes[root.id].children


def test_d2_delete_reattaches_children_and_depth():
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    mid = tree.add_turn([{"role": "user", "content": "mid"}])
    leaf = tree.add_turn([{"role": "user", "content": "leaf"}], parent_id=mid.id)
    assert leaf.id in tree.nodes[mid.id].children
    assert tree.nodes[leaf.id].depth == 2
    tree.delete_node(mid.id)
    assert mid.id not in tree.nodes
    # 叶子重挂根, parent_id + children 同步
    assert leaf.id in tree.nodes[root.id].children
    assert tree.nodes[leaf.id].parent_id == root.id
    # 深度递归重算 (从 2 -> 1)
    assert tree.nodes[leaf.id].depth == 1


def test_d3_delete_root_raises():
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    try:
        tree.delete_node(root.id)
    except ValueError:
        return
    raise AssertionError("expected ValueError for root deletion")


def test_d4_delete_current_repoints():
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    child = tree.add_turn([{"role": "user", "content": "c"}])
    assert tree.current_id == child.id
    tree.delete_node(child.id)
    assert tree.current_id == root.id


def test_d5_delete_keeps_v5_memory():
    fake, tree = _new_tree()
    root = tree.init(seed_messages=[{"role": "user", "content": "hi"}])
    child = tree.add_turn([{"role": "user", "content": "c"}])
    mid = child.v5_memory_id
    assert mid in fake.records
    tree.delete_node(child.id)
    # 记忆仍在 (孤儿保留, 不误删对话内容)
    assert mid in fake.records


if __name__ == "__main__":
    test_r1_rename_writes_and_persists()
    test_r2_empty_clears_title()
    test_r3_rename_missing_raises()
    test_d1_delete_leaf()
    test_d2_delete_reattaches_children_and_depth()
    test_d3_delete_root_raises()
    test_d4_delete_current_repoints()
    test_d5_delete_keeps_v5_memory()
    print("ALL NODE OPS TESTS PASSED")
