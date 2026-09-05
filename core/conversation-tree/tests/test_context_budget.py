"""TDD: context window usage tracking + auto-fork at 100%.

When LLM returns usage with prompt_tokens >= 80% of context_window:
- Server should emit a SSE 'warn' event with context_window_warning marker.

When prompt_tokens >= 100% of context_window:
- Server should emit a SSE 'auto_fork' event AND actually create a new card
  on the conversation tree (via fork_branch) before the user sees the next
  response, so the user doesn't lose the overflow.

The old card should be summarized (LLM call) and the summary becomes the
new card's initial system context.

API surface to test:
    from conversation_tree.server import _check_context_usage, _auto_fork_and_summarize

    _check_context_usage(usage, context_window) -> dict
        Returns {"pct": float, "level": "ok"|"warn"|"fork", "message": str}
        level="warn"  if pct >= 0.8
        level="fork"  if pct >= 1.0
        level="ok"    otherwise

    _auto_fork_and_summarize(tree, fork_point_id, old_messages, agent) -> str
        Returns the new card id (created via tree.fork_branch).
        Also stores a NodeInsight on the OLD card summarizing it.
"""
import os
import sys
import json
import types

# Make core modules importable
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, os.path.join(ROOT, "core", "conversation-tree"))

import pytest


@pytest.fixture
def fake_server(monkeypatch):
    """Import server module with no API key (will fail on real LLM call).

    We import lazily so test doesn't fail on collection.
    """
    sys.modules.pop("server", None)
    # Avoid heavy imports — mock the dependency that triggers _call_llm path
    from memory_v5 import conversation_tree as ct_mod
    from memory_v5.conversation_tree import ConversationTree, _default_load

    class StubStore:
        def store(self, content, type="fact", tags=""):
            self._c = content
            return 1

    tree = ConversationTree(
        persist_key="test_ctx",
        data_dir=None,
        _store=StubStore().store,
        _load=_default_load,
    )
    tree.init(seed_messages=[{"role": "user", "content": "root"}])
    return {"tree": tree, "StubStore": StubStore}


def test_check_context_usage_ok(fake_server):
    """Below 80% is OK."""
    from server import _check_context_usage
    usage = {"prompt_tokens": 1000}
    result = _check_context_usage(usage, context_window=64000)
    assert result["level"] == "ok"
    assert result["pct"] == pytest.approx(1000 / 64000, abs=1e-6)


def test_check_context_usage_warn(fake_server):
    """At 80% (not yet 100%) triggers warn level."""
    from server import _check_context_usage
    usage = {"prompt_tokens": 52000}  # ~81%
    result = _check_context_usage(usage, context_window=64000)
    assert result["level"] == "warn"
    assert result["pct"] >= 0.8


def test_check_context_usage_fork(fake_server):
    """At 100%+ triggers fork level."""
    from server import _check_context_usage
    usage = {"prompt_tokens": 65000}  # > 100%
    result = _check_context_usage(usage, context_window=64000)
    assert result["level"] == "fork"
    assert result["pct"] >= 1.0


def test_check_context_usage_missing_prompt(fake_server):
    """If prompt_tokens missing, treat as 0 (don't false-trigger)."""
    from server import _check_context_usage
    usage = {"completion_tokens": 100}
    result = _check_context_usage(usage, context_window=64000)
    assert result["level"] == "ok"
    assert result["pct"] == 0.0


def test_auto_fork_and_summarize_creates_new_card(fake_server):
    """At 100%, fork_branch is called; old card gets a NodeInsight."""
    from server import _auto_fork_and_summarize
    tree = fake_server["tree"]
    root = next(iter(tree.nodes.values()))
    old_msgs = [
        {"role": "user", "content": "Discuss design A"},
        {"role": "assistant", "content": "Design A: simple, fast, brittle."},
        {"role": "user", "content": "Why brittle?"},
        {"role": "assistant", "content": "Single point of failure in parser."},
    ]
    new_id = _auto_fork_and_summarize(
        tree,
        fork_point_id=root.id,
        old_messages=old_msgs,
        summary_text="[auto-summary] Discussed Design A: simple+fast but brittle due to single-point-of-failure in parser.",
    )
    # New node created
    assert new_id in tree.nodes
    new_node = tree.nodes[new_id]
    assert new_node.parent_id == root.id
    # Old (root) should have a new insight (the summary)
    assert len(root.conclusions) >= 1
    last_insight = root.conclusions[-1]
    assert "[auto-summary]" in last_insight.text


def test_auto_fork_idempotent_within_window(fake_server):
    """Two consecutive 100% triggers should each fork a NEW card (no dedup).

    We don't have a "do-not-fork-twice-in-one-second" rule yet. Test asserts
    distinct child ids — this documents the behavior so future-me can change
    it consciously.
    """
    from server import _auto_fork_and_summarize
    tree = fake_server["tree"]
    root = next(iter(tree.nodes.values()))
    old_msgs = [{"role": "user", "content": "x"}]

    id1 = _auto_fork_and_summarize(tree, root.id, old_msgs, "[auto-summary] #1")
    id2 = _auto_fork_and_summarize(tree, root.id, old_msgs, "[auto-summary] #2")
    assert id1 != id2
    assert id1 in tree.nodes
    assert id2 in tree.nodes