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


# ────────────────── F1: branch 下继续对话继承 branch 类型 ──────────────────

def test_node_type_branch_continuation_stays_branch(tmp_path):
    """F1: branch 节点下的首个子节点必须是 branch (旧逻辑误标 trunk).

    回归: fork 出的分支里继续对话, 节点类型继承 branch, 保证 merge_target /
    is_valid_branch / __trunk__ 查找不被误标 trunk 污染.
    """
    t = _make_tree(tmp_path)
    a = t.add_turn([{"role": "user", "content": "主线"},
                    {"role": "assistant", "content": "b"}])
    # 主线延续 (a 尚无 children) → trunk
    a1 = t.add_turn([{"role": "user", "content": "主线继续"},
                     {"role": "assistant", "content": "c"}], parent_id=a.id)
    assert a1.node_type == "trunk", a1.node_type
    # 从 a 分叉 → branch
    b = t.fork_branch(a.id, "exp", [{"role": "user", "content": "分支"}])
    assert b.node_type == "branch"
    # 分支里继续对话 → 必须仍是 branch (F1 核心: 旧逻辑误标 trunk)
    c = t.add_turn([{"role": "user", "content": "分支内继续"},
                    {"role": "assistant", "content": "d"}], parent_id=b.id)
    assert c.node_type == "branch", c.node_type
    # 再往下两层仍是 branch
    d = t.add_turn([{"role": "user", "content": "再深一层"}], parent_id=c.id)
    assert d.node_type == "branch", d.node_type
    # a 已有子节点后, 再在其下加节点 = 旁系分叉 → branch
    a2 = t.add_turn([{"role": "user", "content": "主线分叉"}], parent_id=a.id)
    assert a2.node_type == "branch", a2.node_type


def test_node_type_trunk_after_conclusion_is_branch(tmp_path):
    """F1 扩展: conclusion 类型节点下继续对话按 branch 处理 (非 trunk)."""
    t = _make_tree(tmp_path)
    a = t.add_turn([{"role": "user", "content": "主线"},
                    {"role": "assistant", "content": "b"}])
    t.conclude_branch(a.id, ["结论"])
    c = t.add_turn([{"role": "user", "content": "结论后继续"}], parent_id=a.id)
    assert c.node_type == "branch", c.node_type


# ────────────────── F2: 删除/剪枝清理 merge 引用 ──────────────────

def test_prune_cleans_merge_refs(tmp_path):
    """F2: prune 后 trunk.merged_from / merged_insights / 注入结论被清理."""
    t = _make_tree(tmp_path)
    a = t.add_turn([{"role": "user", "content": "主线"},
                    {"role": "assistant", "content": "b"}])
    b = t.fork_branch(a.id, "exp", [{"role": "user", "content": "分支"}])
    t.conclude_branch(b.id, ["分支结论"])
    t.merge_branch(b.id, a.id)
    assert b.id in a.merged_from
    assert len(a.conclusions) == 1
    assert len(a.state["merged_insights"]) == 1
    # 剪掉分支子树 → merge 引用全部清理
    t.prune(b.id)
    assert b.id not in a.merged_from
    assert a.state.get("merged_insights") == []
    assert all("merged from" not in c.text for c in a.conclusions)
    # 剪掉的节点确实没了
    assert b.id not in t.nodes


def test_delete_node_cleans_merge_refs(tmp_path):
    """F2: delete_node 后 merge 引用同样被清理 (unmerge 不再静默失效)."""
    t = _make_tree(tmp_path)
    a = t.add_turn([{"role": "user", "content": "主线"},
                    {"role": "assistant", "content": "b"}])
    b = t.fork_branch(a.id, "exp", [{"role": "user", "content": "分支"}])
    t.conclude_branch(b.id, ["分支结论"])
    t.merge_branch(b.id, a.id)
    # 删除分支节点本身 (而非剪子树)
    t.delete_node(b.id)
    assert b.id not in t.nodes
    assert a.id not in a.merged_from
    assert a.state.get("merged_insights") == []
    assert all("merged from" not in c.text for c in a.conclusions)
