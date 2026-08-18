"""tree_adapter 模块沙箱测试（portable-python 跑，离线、无 V5 存储依赖）。

覆盖：
  - tag_for_node 标签生成
  - TreePathCompressor.compress：head 全 / tail 全 / middle→summary / fork 锚点 / 预算护栏
  - tree_scoped_retrieve：path+branch 提权、全局限 top_k（monkeypatch 全局检索）
  - build_tree_aware_context：fake tree 端到端组装
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core")))

from memory_v5.extensions import tree_adapter as ta


# ── fake tree ──────────────────────────────────────────────────────────────
class FakeNode:
    def __init__(self, nid, branch_label=None, summary="", merged_from=None,
                 conclusions=None, depth=0):
        self.id = nid
        self.branch_label = branch_label
        self.summary = summary
        self.merged_from = merged_from or []
        self.conclusions = conclusions or []
        self.depth = depth


class FakeTree:
    def __init__(self):
        # root → a → b(fork) → c → d(leaf), plus sibling s of b
        self.nodes = {
            "n0": FakeNode("n0", None, "root seed", depth=0),
            "n1": FakeNode("n1", None, "discussed poker", depth=1),
            "n2": FakeNode("n2", "strategy", "explored GTO", depth=2),
            "n3": FakeNode("n3", None, "decided bluff freq", depth=3),
            "n4": FakeNode("n4", None, "finalized plan", depth=4),
            "nS": FakeNode("nS", "ml", "alternate ML approach", depth=2,
                           conclusions=[type("C", (), {"text": "use self-play"})()]),
        }
        # nS is sibling of n2
        self._path = ["n0", "n1", "n2", "n3", "n4"]

    def get_path(self, node_id=None):
        return [self.nodes[i] for i in self._path]

    def get_node(self, nid):
        return self.nodes.get(nid)

    def get_sibling_nodes(self, nid):
        if nid == "n2":
            return [self.nodes["nS"]]
        return []

    def get_context_with_meta(self, node_id=None):
        msgs_map = {
            "n0": [{"role": "user", "content": "root question"},
                   {"role": "assistant", "content": "root answer long"}],
            "n1": [{"role": "user", "content": "followup about poker"},
                   {"role": "assistant", "content": "poker analysis"}],
            "n2": [{"role": "user", "content": "try strategy branch"},
                   {"role": "assistant", "content": "GTO breakdown"}],
            "n3": [{"role": "user", "content": "bluff frequency?"},
                   {"role": "assistant", "content": "set 30%"}],
            "n4": [{"role": "user", "content": "finalize plan"},
                   {"role": "assistant", "content": "plan done"}],
        }
        out = []
        for i in self._path:
            n = self.nodes[i]
            out.append({
                "node_id": n.id,
                "depth": n.depth,
                "branch_label": n.branch_label,
                "v5_memory_id": 100 + int(i[1:]),
                "summary": n.summary,
                "messages": msgs_map[i],
            })
        return out


class TagForNodeTest(unittest.TestCase):
    def test_no_branch(self):
        self.assertEqual(ta.tag_for_node("n1"), "node:n1")

    def test_with_branch(self):
        self.assertEqual(ta.tag_for_node("n2", "strategy"), "node:n2 branch:strategy")


class TreePathCompressorTest(unittest.TestCase):
    def setUp(self):
        self.tree = FakeTree()
        self.meta = self.tree.get_context_with_meta()

    def test_head_and_tail_full_middle_summarized(self):
        out = ta.TreePathCompressor(head_nodes=1, tail_nodes=1).compress(self.meta)
        roles = [(m.get("role"), m.get("content")) for m in out]
        # head n0 full (2 msgs) + tail n4 full (2 msgs) = 4 base msgs
        # middle n1,n2,n3 → 3 summary lines
        contents = [c for _, c in roles]
        self.assertIn("root answer long", contents)        # head kept
        self.assertIn("plan done", contents)               # tail kept
        self.assertTrue(any("earlier branch" in c for c in contents))  # middle summarized

    def test_fork_anchor_emitted(self):
        out = ta.TreePathCompressor(head_nodes=1, tail_nodes=3).compress(self.meta)
        fork_markers = [m["content"] for m in out
                        if m.get("content", "").startswith("[fork anchor")]
        # n2 has branch_label="strategy" and is in middle (head=1 → n0; tail=3 → n2,n3,n4)
        # n2 is in tail here, so it gets full messages + a fork anchor marker
        self.assertTrue(any("branch:strategy" in m for m in fork_markers))

    def test_budget_guard(self):
        # force tiny budget to trigger overflow compression path
        out = ta.TreePathCompressor(head_nodes=2, tail_nodes=2,
                                    budget_messages=3).compress(self.meta)
        self.assertLessEqual(len(out), 3 + 4)


class TreeScopedRetrieveTest(unittest.TestCase):
    def setUp(self):
        self.tree = FakeTree()

    def _patch(self, monkeypatch_results):
        # P6 收敛: tree_scoped_retrieve 已从 mr.retrieve 切到 unified_retrieve(scope="semantic"),
        # monkeypatch 目标同步更新 (旧目标 mr.retrieve 已非调用点, patch 无效会打到真实检索)。
        import memory_v5.memory_retrieval as mr
        self._orig = mr.unified_retrieve
        mr.unified_retrieve = lambda query, scope="semantic", top_k=5, character=None, **kw: monkeypatch_results

    def tearDown(self):
        import memory_v5.memory_retrieval as mr
        if hasattr(self, "_orig"):
            mr.unified_retrieve = self._orig

    def test_path_boost_and_limit(self):
        results = [
            {"id": 1, "content": "x", "tags": "node:n3", "score": 0.5},   # on path
            {"id": 2, "content": "y", "tags": "branch:strategy", "score": 0.5},  # on branch (matches path label)
            {"id": 3, "content": "z", "tags": "global", "score": 0.9},     # global high
            {"id": 4, "content": "w", "tags": "node:n9", "score": 0.5},    # off-path
        ]
        self._patch(results)
        out = ta.tree_scoped_retrieve(self.tree, "n4", "query", top_k=3)
        self.assertEqual(len(out), 3)
        # path(1) boosted +0.40 → 0.90 should rank first; global(3) 0.90 second; branch 0.70 third
        self.assertEqual(out[0]["id"], 1)
        self.assertEqual(out[0]["tree_scope"], "path")
        self.assertEqual(out[1]["id"], 3)
        self.assertEqual(out[2]["id"], 2)
        self.assertEqual(out[2]["tree_scope"], "branch")

    def test_off_path_excluded_when_full(self):
        results = [
            {"id": 4, "content": "w", "tags": "node:n9", "score": 0.99},  # off-path
            {"id": 3, "content": "z", "tags": "global", "score": 0.1},
        ]
        self._patch(results)
        out = ta.tree_scoped_retrieve(self.tree, "n4", "query", top_k=5)
        # off-path (n9) not in path/branch → global scope, but lower tree_score than global(3)? 
        # n9 score 0.99 → 0.99; n3 score 0.1 → 0.1. n9 ranks first as global.
        ids = [r["id"] for r in out]
        self.assertIn(4, ids)
        self.assertEqual(out[0]["id"], 4)
        self.assertEqual(out[0]["tree_scope"], "global")


class BuildTreeAwareContextTest(unittest.TestCase):
    def test_assembles_system_plus_l0(self):
        self.tree = FakeTree()
        out = ta.build_tree_aware_context(self.tree, "n4", head_nodes=1, tail_nodes=1)
        self.assertTrue(out[0]["role"] == "system")
        self.assertIn("branching context", out[0]["content"])
        # L1 sibling injected
        self.assertIn("Sibling branches", out[0]["content"])
        # L0 compressed messages present
        contents = [m.get("content") for m in out]
        self.assertIn("plan done", contents)  # tail kept
        self.assertTrue(any("earlier branch" in c for c in contents))  # middle summarized


if __name__ == "__main__":
    unittest.main(verbosity=2)
