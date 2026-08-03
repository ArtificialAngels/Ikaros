"""core/memory_v5/conversation_tree.py 引擎级回归 (子任务 L3 / M2).

这些修复不经过 server.py 的 HTTP 链路, 直接对 ConversationTree 引擎做单测:
- M2: node_type 语义 — 首个子节点=trunk, 后续兄弟=branch (修复全 trunk 失效).
- L3: prune(root) 被守卫拒绝 (避免删光整树且 current_id 指向已删根).
"""
from __future__ import annotations

import pytest

import memory_v5.conversation_tree as ct


class _Mem:
    """极简内存 store, 兼容 ConversationTree 注入的 _store/_load/_search 接口."""

    def __init__(self):
        self.mem: dict[int, str] = {}
        self.c = 0

    def store(self, content, type: str = "conversation", weight: float = 0.6,
              tags: str = "", **_kw):
        self.c += 1
        self.mem[self.c] = content
        return self.c

    def get_batch(self, ids):
        from memory_v5.store import Memory
        return {i: Memory(id=i, content=self.mem.get(i, ""), type="conversation",
                          tags="", weight=0.6, access_count=0, last_accessed=0.0,
                          created=0.0, short_term=True, long_term=False)
                for i in ids if i in self.mem}

    def search(self, query, top_k: int = 10, **_kw):
        return []

    def delete(self, memory_id):
        return self.mem.pop(memory_id, None) is not None


def _make_tree(tmp_path):
    m = _Mem()
    t = ct.ConversationTree(
        persist_key="test_eng", data_dir=str(tmp_path),
        _store=m.store, _load=m.get_batch, _search=m.search)
    t.init([{"role": "system", "content": "sys"}])
    return t


def test_node_type_first_child_is_trunk_second_is_branch(tmp_path):
    """M2: 根的首个子节点=trunk; 根的第二个兄弟子节点=branch."""
    t = _make_tree(tmp_path)
    root = t.root_id
    n1 = t.add_turn([{"role": "user", "content": "a"},
                     {"role": "assistant", "content": "b"}])
    assert n1.node_type == "trunk", n1.node_type

    # 第二个兄弟 (仍以 root 为父): 应为 branch
    n2 = t.add_turn([{"role": "user", "content": "c"},
                     {"role": "assistant", "content": "d"}], parent_id=root)
    assert n2.node_type == "branch", n2.node_type


def test_node_type_branch_under_existing_parent(tmp_path):
    """M2: 分支判定只看"父节点是否已有子节点", 与深度无关.

    n1 是根的首子(trunk); 其首子 n1a 仍 trunk (主线延续); n1 的第二个子 n1b 才是 branch.
    """
    t = _make_tree(tmp_path)
    n1 = t.add_turn([{"role": "user", "content": "a"},
                     {"role": "assistant", "content": "b"}])
    n1a = t.add_turn([{"role": "user", "content": "c"},
                      {"role": "assistant", "content": "d"}], parent_id=n1.id)
    n1b = t.add_turn([{"role": "user", "content": "e"},
                      {"role": "assistant", "content": "f"}], parent_id=n1.id)
    assert n1.node_type == "trunk"
    assert n1a.node_type == "trunk"   # n1 的首子: 主线延续
    assert n1b.node_type == "branch"  # n1 的第二个子: 旁系分支


def test_prune_root_is_guarded(tmp_path):
    """L3: 剪根必须被拒绝 (ValueError), 不能删光整棵树."""
    t = _make_tree(tmp_path)
    t.add_turn([{"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"}])
    with pytest.raises(ValueError):
        t.prune(t.root_id)
    # 树仍完整
    assert t.root_id in t.nodes
    assert len(t.nodes) == 2


def test_prune_non_root_works(tmp_path):
    """对照: 剪非根子树正常 (L3 守卫不误伤)."""
    t = _make_tree(tmp_path)
    n1 = t.add_turn([{"role": "user", "content": "a"},
                     {"role": "assistant", "content": "b"}])
    n2 = t.add_turn([{"role": "user", "content": "c"},
                     {"role": "assistant", "content": "d"}], parent_id=n1.id)
    t.prune(n2.id)
    assert n2.id not in t.nodes
    assert n1.id in t.nodes
