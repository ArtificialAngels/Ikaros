"""core/memory_v5/conversation_tree.py 树域标签钩子 + memory_ids 持久化测试.

验证:
  H1. init/add_turn/fork_branch 写入 V5 时带 node:/branch: 树域标签.
  H2. 节点 id 在存储前生成, 标签里的 node:<id> 与实际 node.id 一致 (tag_for_node 契约).
  H3. add_memory 双写 node.memory_ids 并落盘 (serialize 含 memory_ids).
  H4. 重载后 memory_ids 存活, 且 retrieve() 在 _node_memories 为空时仍能从持久化字段取回 fact.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from memory_v5.conversation_tree import ConversationTree, MemoryRetriever


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


def _conversation_records(fake):
    return [(mid, c, t, g) for mid, (c, t, g) in fake.records.items() if t == "conversation"]


def test_h1_h2_tag_on_store():
    fake = FakeStore()
    tree = ConversationTree(_store=fake.store, _load=fake.load)
    root = tree.init(seed_messages=[{"role": "user", "content": "hello"}])
    # 根节点无 branch_label → 仅 node:<id>
    recs = _conversation_records(fake)
    assert len(recs) == 1, recs
    mid, content, ctype, tags = recs[0]
    assert f"node:{root.id}" in tags.split(), tags
    assert "branch:" not in tags, tags

    # add_turn 带 branch_label → 同时 node + branch
    child = tree.add_turn(
        [{"role": "user", "content": "more"}], branch_label="ml"
    )
    recs = _conversation_records(fake)
    last = recs[-1]
    tags = last[3]
    assert f"node:{child.id}" in tags.split(), tags
    assert "branch:ml" in tags.split(), tags
    # 标签里的 node id 必须与实际 node.id 一致
    assert any(t == f"node:{child.id}" for t in tags.split()), tags


def test_fork_branch_tag():
    fake = FakeStore()
    tree = ConversationTree(_store=fake.store, _load=fake.load)
    tree.init(seed_messages=[{"role": "user", "content": "x"}])
    br = tree.fork_branch(tree.root_id, "strategy",
                          [{"role": "user", "content": "branch turn"}])
    recs = _conversation_records(fake)
    tags = recs[-1][3]
    assert f"node:{br.id}" in tags.split(), tags
    assert "branch:strategy" in tags.split(), tags


def test_h3_memory_ids_persist():
    fake = FakeStore()
    tree = ConversationTree(_store=fake.store, _load=fake.load)
    root = tree.init(seed_messages=[{"role": "user", "content": "x"}])
    mr = MemoryRetriever(tree)
    res = mr.add_memory({"text": "user prefers red", "node_id": root.id})
    mid = res["id"]
    assert mid > 0
    # 节点内存字段更新
    assert mid in tree.nodes[root.id].memory_ids, tree.nodes[root.id].memory_ids
    # serialize 落盘含 memory_ids
    raw = tree.serialize()
    assert f'"memory_ids": [{mid}]' in raw or f'"memory_ids":[{mid}]' in raw, raw


def test_h4_reload_survives_and_retrieve():
    fake = FakeStore()
    tree = ConversationTree(_store=fake.store, _load=fake.load)
    root = tree.init(seed_messages=[{"role": "user", "content": "x"}])
    mr = MemoryRetriever(tree)
    res = mr.add_memory({"text": "persisted fact", "node_id": root.id})
    mid = res["id"]

    # 重载 (新 MemoryRetriever, 内存 _node_memories 为空)
    raw = tree.serialize()
    tree2 = ConversationTree.deserialize(
        raw, persist_key=tree.persist_key, data_dir=tree.data_dir,
        _store=fake.store, _load=fake.load,
    )
    assert mid in tree2.nodes[root.id].memory_ids, tree2.nodes[root.id].memory_ids

    mr2 = MemoryRetriever(tree2)
    assert mr2._node_memories == {}, "期望重载后内存映射为空, 仅靠持久化字段"
    out = mr2.retrieve(root.id, include_path=True, include_cross=False)
    path_ids = [p["mem"]["id"] for p in out["path"]]
    assert mid in path_ids, (mid, path_ids)


if __name__ == "__main__":
    test_h1_h2_tag_on_store()
    test_fork_branch_tag()
    test_h3_memory_ids_persist()
    test_h4_reload_survives_and_retrieve()
    print("ALL TREE CORE HOOK TESTS PASSED")
