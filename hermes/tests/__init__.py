"""
Self-test suite for Hermes.

Run with: python -m hermes test
"""
from __future__ import annotations
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger("hermes.tests")


def _ok(msg: str):
    print(f"  \033[32m✓\033[0m {msg}")


def _fail(msg: str):
    print(f"  \033[31m✗\033[0m {msg}")


def _info(msg: str):
    print(f"  \033[36mℹ\033[0m {msg}")


async def test_llm_providers(agent) -> bool:
    """Test LLM provider connectivity."""
    print("\n[1/6] LLM Providers")
    health = await agent.router.health_check_all()
    any_ok = False
    for name, ok in health.items():
        if ok:
            _ok(f"{name} reachable")
            any_ok = True
        else:
            _fail(f"{name} unreachable")
    return any_ok


async def test_chat_basic(agent) -> bool:
    """Test a basic chat completion."""
    print("\n[2/6] Basic Chat")
    try:
        start = time.time()
        reply = await agent.chat("用一句话介绍你自己。", remember=False)
        elapsed = time.time() - start
        if reply and len(reply) > 5:
            _ok(f"Got reply in {elapsed:.1f}s: {reply[:80]}...")
            return True
        _fail("Empty or too-short reply")
        return False
    except Exception as e:
        _fail(f"Chat failed: {e}")
        return False


async def test_chat_streaming(agent) -> bool:
    """Test streaming chat."""
    print("\n[3/6] Streaming Chat")
    try:
        ctx = await agent.build_context("Hello")
        from hermes.llm import Message
        messages = [
            Message("system", ctx["system"]),
            Message("user", "用10个字说hi"),
        ]
        stream = await agent.router.chat(messages, max_tokens=50, stream=True)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        full = "".join(chunks)
        if full:
            _ok(f"Streamed: {full[:80]}")
            return True
        _fail("Stream produced no content")
        return False
    except Exception as e:
        _fail(f"Stream failed: {e}")
        return False


async def test_memory(agent) -> bool:
    """Test memory add and search."""
    print("\n[4/6] Memory")
    try:
        # Remember
        test_text = f"Hermes test memory at {time.time()}"
        item = await agent.memory.remember(test_text, tags=["test"])
        _ok(f"Remembered item: {item.id[:8]}")

        # Recall
        results = await agent.memory.recall(test_text, k=3)
        found = any(test_text in it.text for it, _ in results)
        if found:
            _ok("Memory recall works")
            return True
        _fail("Memory recall did not find the item")
        return False
    except Exception as e:
        _fail(f"Memory test failed: {e}")
        return False


async def test_knowledge(agent) -> bool:
    """Test knowledge base ingestion and search."""
    print("\n[5/6] Knowledge Base")
    try:
        # Create a test file
        test_dir = agent.paths["knowledge"] / "_test"
        test_dir.mkdir(exist_ok=True)
        test_file = test_dir / "test_doc.md"
        test_file.write_text(
            "# Test Document\n\n"
            "Hermes is a portable AI agent. "
            "It can run on USB drives and works offline. "
            "Qwen2.5 is a powerful language model by Alibaba.\n\n"
            "## Features\n- Portable\n- Offline-first\n- Hybrid LLM\n",
            encoding="utf-8",
        )
        # Ingest
        count = agent.knowledge.ingest(test_file)
        _ok(f"Ingested {count} chunks")

        # Search
        results = await agent.knowledge.search("What is Hermes?", k=3)
        if results:
            _ok(f"Search returned {len(results)} results, top score: {results[0][1]:.3f}")
            # Cleanup
            test_file.unlink()
            test_dir.rmdir()
            # Remove test chunks
            agent.knowledge.chunks = [
                c for c in agent.knowledge.chunks if "_test" not in c.source
            ]
            agent.knowledge._save()
            return True
        _fail("Search returned no results")
        return False
    except Exception as e:
        _fail(f"KB test failed: {e}")
        return False


async def test_skills(agent) -> bool:
    """Test skills."""
    print("\n[6/6] Skills")
    try:
        result = agent.skills.call("time", {})
        if result and ":" in result:
            _ok(f"time skill: {result}")
        else:
            _fail(f"time skill returned: {result}")
            return False

        result = agent.skills.call("calc", {"expression": "2 + 2 * 3"})
        if result == "8":
            _ok(f"calc skill: 2 + 2 * 3 = {result}")
        else:
            _fail(f"calc skill returned: {result}")
            return False

        return True
    except Exception as e:
        _fail(f"Skills test failed: {e}")
        return False


def run_all_tests(agent) -> bool:
    """Run all tests. Returns True if all pass."""
    print("=" * 60)
    print(f"  Hermes v{agent.config.agent.version} - Self Test")
    print("=" * 60)

    # Init
    asyncio.run(agent.initialize())
    agent.print_status()

    # Run tests
    results = []
    results.append(("LLM Providers", asyncio.run(test_llm_providers(agent))))
    results.append(("Basic Chat", asyncio.run(test_chat_basic(agent))))
    results.append(("Streaming", asyncio.run(test_chat_streaming(agent))))
    results.append(("Memory", asyncio.run(test_memory(agent))))
    results.append(("Knowledge", asyncio.run(test_knowledge(agent))))
    results.append(("Skills", asyncio.run(test_skills(agent))))

    # Summary
    print("\n" + "=" * 60)
    print("  Summary")
    print("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        status = "\033[32mPASS\033[0m" if ok else "\033[31mFAIL\033[0m"
        print(f"  {status}  {name}")
    print(f"\n  {passed}/{len(results)} tests passed")
    print("=" * 60)

    return passed == len(results)


# CLI test runner
if __name__ == "__main__":
    from hermes.config import load_config
    from hermes.agent import HermesAgent
    agent = HermesAgent(load_config())
    sys.exit(0 if run_all_tests(agent) else 1)
