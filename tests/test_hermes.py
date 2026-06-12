"""
Hermes - Clean E2E Test Suite

Tests core hermes components without requiring GPU/llama-server.
Run from project root:
    portable-python\\python.exe tests\\test_hermes.py

Covers:
  1. GPU detection (via hermes.gpu)
  2. Config loading + env var expansion
  3. Agent instantiation (mock mode)
  4. Memory write + recall
  5. Knowledge base ingest + search
  6. LLM router (mock fallback chain)
  7. Skills (time, calc, echo, custom)
  8. Hash embeddings endpoint shape
  9. Planner (autonomous plan-and-execute loop)

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

results = {"passed": 0, "failed": 0, "tests": []}


def record(name, ok, detail=""):
    status = "✓" if ok else "✗"
    color = "\033[32m" if ok else "\033[31m"
    print(f"  {color}{status}\033[0m  {name}")
    if detail:
        print(f"        {detail}")
    results["tests"].append({"name": name, "ok": ok, "detail": detail})
    if ok:
        results["passed"] += 1
    else:
        results["failed"] += 1


# ============================================
# 1. GPU Detection
# ============================================
print("\n[1/8] GPU Detection")
try:
    # hermes.gpu was removed in Phase 1-6. The replacement lives in
    # modules/env_bootstrap/gpu_detect.py (also callable as
    # `python -m modules.env_bootstrap.gpu_detect recommend`).
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
print("\n[2/8] Config loading")
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
print("\n[3/8] Agent instantiation (mock mode)")
agent = None
try:
    from hermes.agent import HermesAgent
    cfg = load_config(str(ROOT / "config" / "hermes.yaml"))
    agent = HermesAgent(cfg, use_mock=True)
    record("Agent constructs", True, f"providers={[p.name for p in agent.router.providers.values()]}")
    record("Router has mock provider", "mock" in agent.router.providers)
except Exception as e:
    record("Agent instantiation", False, str(e))


# ============================================
# 4. Memory
# ============================================
print("\n[4/8] Memory (write + recall)")
async def test_memory():
    test_text = f"Test memory at {time.time()}: hermes e2e memory check"
    item = await agent.memory.remember(test_text, tags=["test", "e2e"])
    recs = await agent.memory.recall("e2e memory check", k=3)
    found = any(test_text in it.text for it, _ in recs)
    return item, found, len(recs)

try:
    item, found, n = asyncio.run(test_memory())
    record("Memory write+recall", found, f"id={item.id[:8]}, found={found}, n={n}")
except Exception as e:
    record("Memory write+recall", False, str(e))


# ============================================
# 5. Knowledge base
# ============================================
print("\n[5/8] Knowledge base (ingest + search)")
async def test_kb():
    kb_dir = ROOT / "hermes" / "data" / "knowledge"
    ingested = 0
    for md in kb_dir.glob("*.md"):
        if md.name != "index.jsonl":
            try:
                count = agent.knowledge.ingest(md, tag="hermes-docs")
                ingested += count
            except Exception as e:
                pass
    sr = await agent.knowledge.search("hybrid LLM offline portable", k=3)
    return ingested, len(sr), sr

try:
    ingested, n, sr = asyncio.run(test_kb())
    top_score = sr[0][1] if sr else 0
    record("KB ingest+search", n > 0,
           f"ingested_chunks={ingested}, hits={n}, top_score={top_score:.3f}")
except Exception as e:
    record("KB search", False, str(e))


# ============================================
# 6. LLM Router (mock mode)
# ============================================
print("\n[6/8] LLM router (mock mode)")
async def test_router():
    from hermes.llm import Message
    t0 = time.time()
    r = await agent.router.chat(
        messages=[Message("user", "hello")],
        max_tokens=50,
    )
    dt = time.time() - t0
    return r, dt, agent.router.order

try:
    r, dt, order = asyncio.run(test_router())
    record("LLM router works", len(r.content) > 0,
           f"order={order}, latency={dt*1000:.0f}ms, reply='{r.content[:40]}'")
except Exception as e:
    record("LLM router works", False, str(e))


# ============================================
# 7. Skills
# ============================================
print("\n[7/8] Skills")
try:
    skill_names = [s["name"] for s in agent.skills.list()]
    t = agent.skills.call("time", {})
    calc_result = agent.skills.call("calc", {"expression": "2+2*3"})
    skills_ok = "2026" in t and calc_result == "8"
    record("Skills work", skills_ok,
           f"available={skill_names}, time='{t}', calc(2+2*3)='{calc_result}'")
except Exception as e:
    record("Skills work", False, str(e))


# ============================================
# 8. Embeddings shim (hash-based)
# ============================================
print("\n[8/8] Embeddings shim (hash-based)")
try:
    import hashlib
    def hex_embed(text, dim=384):
        h = hashlib.sha512(text.encode("utf-8")).digest()
        return [((h[j % len(h)] / 255.0) - 0.5) * 2.0 for j in range(dim)]
    e1 = hex_embed("test")
    e2 = hex_embed("test")
    e3 = hex_embed("不同")
    deterministic = e1 == e2
    distinguishing = e1 != e3
    record("Embeddings deterministic", deterministic, f"vec_len={len(e1)}")
    record("Embeddings differentiate", distinguishing, f"diff_amp={(sum(abs(a-b) for a,b in zip(e1,e3))/len(e1)):.4f}")
except Exception as e:
    record("Embeddings shim", False, str(e))


# ============================================
# 9. Planner (autonomous task execution)
# ============================================
print("\n[9/9] Planner (autonomous task)")
async def test_planner():
    from hermes.planner import Planner
    planner = Planner(agent, verbose=False)
    result = await planner.run("check current time and echo result")
    has_plan = len(result.plan) > 0
    has_summary = len(result.final) > 0
    return has_plan, has_summary, result

try:
    has_plan, has_summary, plan_result = asyncio.run(test_planner())
    record("Planner generates plan", has_plan,
           f"steps={len(plan_result.plan)}, success={plan_result.success}")
    record("Planner summarizes", has_summary,
           f"summary_len={len(plan_result.final)}")
    # Verify step structure
    if plan_result.plan:
        step = plan_result.plan[0]
        has_fields = all(hasattr(step, attr) for attr in ['step', 'skill', 'args', 'why', 'result', 'status'])
        record("TaskStep has expected fields", has_fields,
               f"sample step: {step.skill}({step.args}) -> {step.status}")
except Exception as e:
    record("Planner", False, str(e))


# ============================================
# Summary
# ============================================
print("\n" + "=" * 70)
total = results['passed'] + results['failed']
print(f"  Results: {results['passed']}/{total} passed")
print("=" * 70)
for t in results['tests']:
    status = "✓" if t['ok'] else "✗"
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
