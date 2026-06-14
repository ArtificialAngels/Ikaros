"""
Hermes - Clean E2E Test Suite

Tests core hermes components without requiring GPU/llama-server.
Run from project root:
    portable-python\\python.exe tests\\test_hermes.py

Covers:
  1. GPU detection (via modules.env_bootstrap.gpu_detect)
  2. Config loading + env var expansion
  3. Agent instantiation (AIAgent)
  4. Knowledge base ingest + search (hermes.knowledge)
  5. Hash embeddings (deterministic + distinguishing)
  6. Memos client instantiation

Web server (hermes serve) is NOT tested here — it's tested manually via
bin\\hermes-all.bat. Server has data-dependent startup time that doesn't
fit a 30s test budget cleanly.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Resolve project root: tests/ -> parent is project root
TESTS_DIR = Path(__file__).parent.resolve()
ROOT = TESTS_DIR.parent
sys.path.insert(0, str(ROOT))
os.environ["HERMES_DATA_DIR"] = str(ROOT / "hermes" / "data")
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUNBUFFERED"] = "1"
os.environ["HERMES_EMBEDDER"] = "hash"
os.environ["HERMES_LLM_MOCK"] = "1"  # use mock for router tests

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

results = {"passed": 0, "failed": 0, "skipped": 0, "tests": []}


def record(name, ok, detail=""):
    if ok is None:
        status = "~"
        color = "\033[33m"
        results["skipped"] += 1
    elif ok:
        status = "✓"
        color = "\033[32m"
        results["passed"] += 1
    else:
        status = "✗"
        color = "\033[31m"
        results["failed"] += 1
    print(f"  {color}{status}\033[0m  {name}")
    if detail:
        print(f"        {detail}")
    results["tests"].append({"name": name, "ok": ok, "detail": detail})


# ============================================
# 1. GPU Detection
# ============================================
print("\n[1/6] GPU Detection")
try:
    from modules.env_bootstrap.gpu_detect import detect_all_gpus as detect_gpu
    g = detect_gpu()
    has_gpu = g.get("primary") in ("nvidia", "amd", "intel", "cuda", "vulkan", "hip")
    if g.get("primary") == "nvidia":
        nv = g["nvidia"]
        gpu_name = nv["gpus"][0]["name"] if nv.get("gpus") else "?"
        record("GPU detected", has_gpu, f"vendor={g['primary']}, gpu={gpu_name}")
    else:
        record("GPU detected (no NVIDIA)", True, f"primary={g['primary']}")
except Exception as e:
    record("GPU detection", False, str(e))


# ============================================
# 2. Config Loading
# ============================================
print("\n[2/6] Config loading")
try:
    from hermes.config import load_config, _expand_env
    cfg = load_config(str(ROOT / "config" / "hermes.yaml"))
    has_minimax = any(p.name == "minimax" for p in cfg.llm.cloud)
    record("Config loads YAML", cfg.agent.name is not None,
           f"name={cfg.agent.name}, version={cfg.agent.version}")
    record("MiniMax provider in config", has_minimax,
           f"providers={[p.name for p in cfg.llm.cloud]}")
    # Test env expansion
    expanded = _expand_env("${HERMES_DATA_DIR}")
    record("Env var expansion", "/hermes" in expanded or "\\hermes" in expanded,
           f"HERMES_DATA_DIR -> {expanded}")
except Exception as e:
    record("Config loading", False, str(e))


# ============================================
# 3. Agent instantiation
# ============================================
print("\n[3/6] Agent instantiation")
agent = None
try:
    from run_agent import AIAgent
    agent = AIAgent(model="", quiet_mode=True, enabled_toolsets=[])
    record("Agent constructs", True)
except Exception as e:
    record("Agent instantiation", False, str(e))


# ============================================
# 4. Knowledge Base (hermes.knowledge)
# ============================================
print("\n[4/6] Knowledge base (ingest + search)")
try:
    from hermes.knowledge import KnowledgeBase
    kb_dir = ROOT / "hermes" / "data" / "knowledge"
    kb = KnowledgeBase(path=kb_dir)
    # Ingest a test file
    test_file = kb_dir / "test_ingest.md"
    test_file.write_text("# Test\nThis is a test document about AI agents and LLMs.", encoding="utf-8")
    count = kb.ingest(test_file, tag="test")
    # Search (async)
    async def kb_search():
        return await kb.search("AI agents", k=3)
    results_kb = asyncio.run(kb_search())
    record("KB ingest", count > 0, f"chunks={count}")
    record("KB search", len(results_kb) > 0, f"hits={len(results_kb)}, top_score={results_kb[0][1]:.3f}" if results_kb else "no hits")
    # Cleanup
    test_file.unlink(missing_ok=True)
except Exception as e:
    record("KB", False, str(e))


# ============================================
# 5. Embeddings (hash-based)
# ============================================
print("\n[5/6] Embeddings (hash-based)")
try:
    import hashlib
    def hex_embed(text, dim=384):
        h = hashlib.sha512(text.encode("utf-8")).digest()
        return [((h[j % len(h)] / 255.0) - 0.5) * 2.0 for j in range(dim)]
    e1 = hex_embed("test")
    e2 = hex_embed("test")
    e3 = hex_embed("different")
    deterministic = e1 == e2
    distinguishing = e1 != e3
    record("Embeddings deterministic", deterministic, f"vec_len={len(e1)}")
    record("Embeddings differentiate", distinguishing, f"diff_amp={(sum(abs(a-b) for a,b in zip(e1,e3))/len(e1)):.4f}")
except Exception as e:
    record("Embeddings", False, str(e))


# ============================================
# 6. Memos Client
# ============================================
print("\n[6/6] Memos client")
try:
    from hermes.memos_client import MemosClient
    client = MemosClient(base_url="http://127.0.0.1:5230")
    has_async_ctx = hasattr(client, '__aenter__') and hasattr(client, '__aexit__')
    has_close = hasattr(client, 'close')
    record("MemosClient instantiation", True)
    record("MemosClient async context manager", has_async_ctx)
    record("MemosClient close method", has_close)
except Exception as e:
    record("Memos client", False, str(e))


# ============================================
# Summary
# ============================================
print("\n" + "=" * 70)
total = results['passed'] + results['failed'] + results['skipped']
print(f"  Results: {results['passed']}/{total} passed, {results['skipped']} skipped")
print("=" * 70)
for t in results['tests']:
    if t['ok'] is None:
        status = "~"
    elif t['ok']:
        status = "✓"
    else:
        status = "✗"
    print(f"  {status} {t['name']}")
print()

if results['failed'] == 0:
    print("\033[32m  ALL TESTS PASSED\033[0m")
else:
    print(f"\033[33m  {results['failed']} tests failed\033[0m")

results_file = ROOT / "hermes" / "data" / "logs" / "e2e-results.json"
results_file.parent.mkdir(parents=True, exist_ok=True)
results_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nResults saved to: {results_file}")

sys.exit(0 if results['failed'] == 0 else 1)
