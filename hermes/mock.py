"""Mock LLM provider for testing.

Used by `MockProvider` in hermes/llm.py when HERMES_LLM_MOCK=1 or use_mock=True.
Provides canned chat responses and deterministic hash-based embeddings.
"""
from __future__ import annotations
import hashlib
import time
from typing import Any


def hash_embed(text: str, dim: int = 384) -> list[float]:
    """Deterministic hash-based embedding for tests.

    Not useful for real semantic search; just gives a stable
    vector for any input so memory/KB code paths can be tested.
    """
    h = hashlib.sha512(text.encode("utf-8")).digest()
    vec = []
    for j in range(dim):
        vec.append(((h[j % len(h)] / 255.0) - 0.5) * 2.0)
    return vec


_CANNED = [
    "Hello! I'm Hermes mock assistant. How can I help?",
    "I'm a mock - I don't actually do anything useful.",
    "Mock response: this is a test answer to your query.",
    "The quick brown fox jumps over the lazy dog.",
    "OK",
    "I'm running in mock mode (HERMES_LLM_MOCK=1).",
    "42",
]


def mock_chat(messages: list[dict[str, Any]]) -> str:
    """Generate a canned response based on the last user message.

    Deterministic-ish: the same input always returns the same canned response
    (based on message content hash). For planning-style prompts (JSON request),
    returns a valid sample plan to make end-to-end testing possible.
    """
    if not messages:
        return _CANNED[0]

    last_user = ""
    for m in messages:
        if m.get("role") == "user":
            last_user = m.get("content", "")

    if not last_user:
        return _CANNED[0]

    # Detect planning prompt: looks for "GOAL:" + "AVAILABLE SKILLS"
    if "GOAL:" in last_user and "AVAILABLE SKILLS" in last_user:
        return _mock_plan(last_user)

    # Detect summary prompt
    if "EXECUTION RESULTS:" in last_user:
        return "Mock summary: the requested task was processed using the available skills. See plan for details."

    # Pick a canned response based on hash of content
    h = hashlib.md5(last_user.encode("utf-8")).hexdigest()
    idx = int(h, 16) % len(_CANNED)
    return _CANNED[idx]


def _mock_plan(prompt: str) -> str:
    """Generate a mock plan that uses the most plausible skill from the prompt."""
    # Find AVAILABLE SKILLS section
    import re
    m = re.search(r"AVAILABLE SKILLS.*?(?:RULES:|OUTPUT FORMAT)", prompt, re.DOTALL)
    skills_text = m.group(0) if m else ""
    skill_names = re.findall(r"-\s+(\w+):", skills_text)

    if not skill_names:
        # Try to extract just the first skill name as fallback
        return '```json\n[]\n```'

    # Pick first 1-2 skills
    chosen = skill_names[:2] if len(skill_names) >= 2 else skill_names[:1]
    plan = []
    for i, s in enumerate(chosen, 1):
        plan.append({
            "step": i,
            "skill": s,
            "args": {},
            "why": f"Mock planner: use {s} (first available skill)",
        })
    return "```json\n" + __import__("json").dumps(plan, indent=2) + "\n```"
