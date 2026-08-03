"""core/memory_v5/conversation_tree.py 引擎级回归 (子任务 L3 / M2).

这些修复不经过 server.py 的 HTTP 链路, 直接对 ConversationTree 引擎做单测:
- M2: node_type 语义 — 首个子节点=trunk, 后续兄弟=branch (修复全 trunk 失效).
- L3: prune(root) 被守卫拒绝 (避免删光整树且 current_id 指向已删根).
"""
from __future__ import annotations

import json

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


# ────────────────── S1: trunk_id 显式主线语义 ──────────────────

def test_trunk_id_advances_on_mainline(tmp_path):
    """S1: 主线延续时 trunk_id 前进; fork/分支不改变主线终点."""
    t = _make_tree(tmp_path)
    assert t.trunk_id is None
    a = t.add_turn([{"role": "user", "content": "主线1"},
                    {"role": "assistant", "content": "b"}])
    assert t.trunk_id == a.id  # 根首子 → 主线起点
    a2 = t.add_turn([{"role": "user", "content": "主线2"},
                     {"role": "assistant", "content": "c"}], parent_id=a.id)
    assert t.trunk_id == a2.id  # 主线延续 → 终点前进
    # fork 分支不动主线
    b = t.fork_branch(a.id, "exp", [{"role": "user", "content": "分支"}])
    assert t.trunk_id == a2.id
    assert b.node_type == "branch"
    # 分支内继续 → branch, 主线终点不变
    c = t.add_turn([{"role": "user", "content": "分支内"}], parent_id=b.id)
    assert c.node_type == "branch"
    assert t.trunk_id == a2.id
    # 从主线中间节点 a 分叉 → branch (主线终点仍是 a2)
    d = t.add_turn([{"role": "user", "content": "中间分叉"}], parent_id=a.id)
    assert d.node_type == "branch"
    assert t.trunk_id == a2.id
    # 主线终点下继续 → 主线延续
    e = t.add_turn([{"role": "user", "content": "主线3"}], parent_id=a2.id)
    assert e.node_type == "trunk"
    assert t.trunk_id == e.id


def test_trunk_id_serialize_roundtrip(tmp_path):
    """S1: trunk_id 随序列化持久化, 重启后主线语义不丢."""
    t = _make_tree(tmp_path)
    a = t.add_turn([{"role": "user", "content": "主线1"},
                    {"role": "assistant", "content": "b"}])
    a2 = t.add_turn([{"role": "user", "content": "主线2"},
                     {"role": "assistant", "content": "c"}], parent_id=a.id)
    t.fork_branch(a.id, "exp", [{"role": "user", "content": "分支"}])
    raw = t.serialize()
    t2 = ct.ConversationTree.deserialize(raw)
    assert t2.trunk_id == a2.id
    # 旧 JSON (无 trunk_id) → 按最深 trunk 链推断
    old = json.loads(raw)
    old.pop("trunk_id")
    t3 = ct.ConversationTree.deserialize(old)
    assert t3.trunk_id == a2.id  # 主线链 a→a2, 最深 trunk = a2


def test_set_trunk_promotes_branch(tmp_path):
    """S1: set_trunk 把分支节点提升为主线终点."""
    t = _make_tree(tmp_path)
    a = t.add_turn([{"role": "user", "content": "主线1"},
                    {"role": "assistant", "content": "b"}])
    b = t.fork_branch(a.id, "exp", [{"role": "user", "content": "分支"}])
    assert t.trunk_id == a.id
    t.set_trunk(b.id)
    assert t.trunk_id == b.id
    # 分支内继续 → 现在变成主线延续 (trunk)
    c = t.add_turn([{"role": "user", "content": "提升后继续"}], parent_id=b.id)
    assert c.node_type == "trunk"
    assert t.trunk_id == c.id
    # 原主线节点 a 下继续 → branch (主线已切换)
    d = t.add_turn([{"role": "user", "content": "旧主线继续"}], parent_id=a.id)
    assert d.node_type == "branch"


def test_trunk_id_redirect_on_delete(tmp_path):
    """S1: 主线终点被删/剪 → trunk_id 回退到最近的 trunk 祖先."""
    t = _make_tree(tmp_path)
    a = t.add_turn([{"role": "user", "content": "主线1"},
                    {"role": "assistant", "content": "b"}])
    a2 = t.add_turn([{"role": "user", "content": "主线2"},
                     {"role": "assistant", "content": "c"}], parent_id=a.id)
    assert t.trunk_id == a2.id
    t.delete_node(a2.id)
    assert t.trunk_id == a.id  # 回退到父 (trunk)
    a3 = t.add_turn([{"role": "user", "content": "主线3"},
                     {"role": "assistant", "content": "d"}], parent_id=a.id)
    assert a3.node_type == "trunk"
    assert t.trunk_id == a3.id
    t.prune(a3.id)
    assert t.trunk_id == a.id


def test_conclude_trunk_id_fallback(tmp_path):
    """S1: 主线终点收尾 (conclusion) → trunk_id 回退到 trunk 祖先."""
    t = _make_tree(tmp_path)
    a = t.add_turn([{"role": "user", "content": "主线1"},
                    {"role": "assistant", "content": "b"}])
    a2 = t.add_turn([{"role": "user", "content": "主线2"},
                     {"role": "assistant", "content": "c"}], parent_id=a.id)
    t.conclude_branch(a2.id, ["收尾"])
    assert t.trunk_id == a.id
    # conclusion 节点下继续 → branch (不延续主线)
    d = t.add_turn([{"role": "user", "content": "收尾后"}], parent_id=a2.id)
    assert d.node_type == "branch"
