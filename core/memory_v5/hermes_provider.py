"""
V5 MemoryProvider for Hermes Agent — bridges V5 self-model, memory, and
affect into the Hermes conversation loop.

Implements the ``MemoryProvider`` ABC so V5 gets:

  system_prompt_block()  — R2 rhythm + R5 profile + live state snippet
  prefetch(query)        — R3 PAD-weighted memory recall
  sync_turn(user, asst)  — store conversation, update PAD, record emotion

SOUL.md (synced by bin/ikaros-soul-sync.py) remains the identity skeleton.
This provider adds the **per-turn dynamic layer** that makes cross-session
continuity work in Hermes Dashboard sessions.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ikaros.v5.hermes_provider")

# ── V5 root bootstrap ──────────────────────────────────────────────────────
_V5_ROOT = Path(__file__).resolve().parent
if str(_V5_ROOT) not in sys.path:
    sys.path.insert(0, str(_V5_ROOT))

# ── Quality gate: skip short/greeting inputs ───────────────────────────────
_SKIP_INPUTS: set[str] = {
    "嗯", "哦", "好", "好的", "行", "OK", "ok", "是", "对", "是的",
    "继续", "然后", "还有", "呢", "啊", "哈", "哈哈", "呵呵",
    "你好", "早", "早安", "晚安", "再见", "拜拜", "hi", "hello",
    "谢谢", "感谢", "辛苦", "收到", "明白", "知道了", "了解",
}

# ── High-signal memory candidate patterns (同 cloud_chat.py) ────────────────
_HIGH_SIGNAL_PATTERNS = [
    r"(?:记住|记下来|保存(?:这个|这条|我的)?|以后(?:都|请)?|长期)",
    r"(?:我(?:是|叫|来自|住在|在.{0,12}工作)|我的(?:名字|职业|身份|家乡|住址))",
    r"(?:我(?:喜欢|偏好|习惯|通常|总是|从不|不喜欢|讨厌|不吃|需要|希望|要求))",
    r"(?:remember|from now on|my name is|i am|i'm|i like|i prefer|i need)",
]
import re as _re
_HIGH_SIGNAL_RE = _re.compile("|".join(_HIGH_SIGNAL_PATTERNS), _re.IGNORECASE)


def _has_high_signal(text: str) -> bool:
    return bool(_HIGH_SIGNAL_RE.search(text))


# ── Lazy V5 module loader (fail-silent for robustness) ─────────────────────
def _v5(mod_name: str):
    """Lazy-import a V5 module. Returns None on failure (never raises)."""
    try:
        return __import__(f"memory_v5.{mod_name}", fromlist=["__all__"])
    except Exception as exc:
        logger.debug("v5.%s import failed: %s", mod_name, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Hermes MemoryProvider implementation
# ═══════════════════════════════════════════════════════════════════════════

# Hermes' MemoryProvider is duck-typed in the agent code — the ABC is in
# agent/memory_provider.py. We avoid a hard import so this module can load
# without the Hermes tree.
_NAME = "ikaros-v5"


def _check_available() -> bool:
    """Return True iff the V5 db is reachable and self_model exists."""
    try:
        db = _V5_ROOT / "data" / "v5" / "v5.db"
        return db.is_file()
    except Exception:
        return False


def get_name() -> str:
    return _NAME


def is_available() -> bool:
    return _check_available()


def initialize(session_id: str = "", **kwargs: Any) -> None:
    """One-time setup per session."""
    logger.info("V5MemoryProvider initialised (session=%s)", session_id[:16])


# ---------------------------------------------------------------------------
# system_prompt_block — R2 rhythm + R5 profile + live state
# ---------------------------------------------------------------------------

def system_prompt_block() -> str:
    """Return a compact block injected into the Hermes system prompt.

    Three sections, each optional (empty string = skip):

      1. R2 rhythm      — 距上轮时间 + 时段
      2. R5 profile     — 哥哥偏好/不喜欢
      3. live state     — PAD情绪 + 精力 + 关系阶段 (一行)

    Returns empty string when nothing can be assembled (V5 not available).
    """
    parts: list[str] = []

    # ── R2 rhythm ─────────────────────────────────────────────────────
    rhythm = _v5("rhythm")
    if rhythm:
        try:
            block = rhythm.build_rhythm_block()
            if block:
                parts.append(block.lstrip("\n---\n"))  # deduplicate formatting
        except Exception:
            pass

    # ── R5 profile ────────────────────────────────────────────────────
    profile = _v5("profile")
    if profile:
        try:
            block = profile.build_profile_block()
            if block:
                parts.append(block.lstrip("\n---\n"))
        except Exception:
            pass

    # ── Live state: PAD + vitality + relationship (one compact line) ──
    state_items: list[str] = []

    affect = _v5("affect")
    if affect:
        try:
            s = affect.AffectState.load().decay()
            p, a, d = s.pleasure, s.arousal, s.dominance
            label = s.to_prompt().replace("【情感状态】", "").strip()
            state_items.append(f"mood:{label[:30]}")
        except Exception:
            pass

    vitality = _v5("vitality")
    if vitality:
        try:
            v = vitality.Vitality.load()
            state_items.append(f"energy:{v.label()}")
        except Exception:
            pass

    relationship = _v5("relationship")
    if relationship:
        try:
            r = relationship.Relationship.load()
            state_items.append(f"bond:{r.stage()}")
        except Exception:
            pass

    if state_items:
        parts.append(" | ".join(state_items))

    if not parts:
        return ""

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# prefetch — R3 memory recall with PAD weighting
# ---------------------------------------------------------------------------

def prefetch(query: str, *, session_id: str = "") -> str:
    """Retrieve relevant memories for the upcoming turn.

    Calls the V5 three-way retrieval (FTS5 + vector + time range) and
    re-ranks results using current PAD emotional intensity. Returns a
    compact text block, or empty string on no match / short query.
    """
    q = (query or "").strip()
    if not q or len(q) < 6 or q in _SKIP_INPUTS:
        return ""

    # Load current PAD for emotional weighting
    try:
        affect = _v5("affect")
        if affect:
            state = affect.AffectState.load().decay()
            cur_pad = (state.pleasure, state.arousal, state.dominance)
        else:
            cur_pad = (0.0, 0.0, 0.0)
    except Exception:
        cur_pad = (0.0, 0.0, 0.0)

    # Three-way retrieval
    retrieval = _v5("memory_retrieval")
    if not retrieval:
        return ""
    try:
        mems = retrieval.retrieve(
            q, top_k=5, min_weight=0.0,
        )
    except Exception:
        return ""

    if not mems:
        return ""

    # Re-rank by PAD-weighted score
    intensity = abs(cur_pad[0]) + abs(cur_pad[1])  # pleasure + arousal
    scored = []
    for m in mems:
        base = float(m.get("score", 0.5))
        mp = abs(float(m.get("pad_p", 0)))
        ma = abs(float(m.get("pad_a", 0)))
        m_intensity = mp + ma or float(m.get("weight", 0.5))
        blended = base * 0.6 + m_intensity * 0.4 * (1 + intensity * 0.2)
        scored.append((blended, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:3]

    lines: list[str] = []
    for _, m in top:
        content = (m.get("content") or "")[:80]
        if content:
            lines.append(f"  - {content.replace(chr(10), ' ')}")

    if not lines:
        return ""

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# sync_turn — store conversation, update PAD, record emotion, track relation
# ---------------------------------------------------------------------------

def sync_turn(
    user_content: str,
    assistant_content: str,
    *,
    session_id: str = "",
    messages: Optional[list[dict]] = None,
) -> None:
    """Post-turn write-back to V5.

    Runs in a background thread (Hermes dispatches sync_all off the
    hot path). Silent on failure — never blocks the conversation.
    """
    u = (user_content or "").strip()
    if not u or len(u) < 6 or u in _SKIP_INPUTS:
        return

    try:
        _do_sync_turn(u, assistant_content or "", session_id)
    except Exception as exc:
        logger.debug("V5 sync_turn failed: %s", exc)


def _do_sync_turn(
    user_text: str,
    assistant_text: str,
    session_id: str,
) -> None:
    """Internal sync, may raise so sync_turn's try/except catches it."""

    # 1. Store conversation pair in v5.db
    store = _v5("store")
    if store:
        try:
            assistant_short = assistant_text.strip()[:150]
            content = f"Q: {user_text[:200]}\nA: {assistant_short}"
            store.store(
                content=content,
                type="conversation",
                weight=0.5,
                tags="hermes",
            )
        except Exception as exc:
            logger.debug("store conversation: %s", exc)

    # 2. Store high-signal facts immediately
    if _has_high_signal(user_text) and store:
        try:
            store.store(
                content=user_text[:200],
                type="fact",
                weight=0.7,
                tags="high_signal",
            )
        except Exception:
            pass

    # 3. Update PAD emotion state
    affect = _v5("affect")
    if affect:
        try:
            old_state = affect.AffectState.load()
            old_pad = (old_state.pleasure, old_state.arousal, old_state.dominance)
            affect.apply_event(user_text)
            new_state = affect.AffectState.load()
            new_pad = (new_state.pleasure, new_state.arousal, new_state.dominance)
        except Exception:
            old_pad = new_pad = (0.0, 0.0, 0.0)
    else:
        old_pad = new_pad = (0.0, 0.0, 0.0)

    # 4. Record emotion causality if PAD changed significantly
    emotion = _v5("emotional_memory")
    if emotion and old_pad != new_pad and new_pad != (0.0, 0.0, 0.0):
        try:
            emotion.maybe_record_emotion(old_pad, new_pad, user_text)
        except Exception:
            pass

    # 5. Track relationship
    relationship = _v5("relationship")
    if relationship:
        try:
            relationship.track_interaction(0.3)
        except Exception:
            pass

    # 6. Auto-sync SOUL.md — Hermes persona comes from SOUL.md
    # Fire-and-forget, non-blocking (same pattern as cloud_chat _clock_out)
    try:
        import subprocess
        _soul_script = _V5_ROOT.parent.parent / "bin" / "ikaros-soul-sync.py"
        if _soul_script.is_file():
            subprocess.Popen(
                [sys.executable, str(_soul_script)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass

    # 7. Push to conversation tree (:48920), silent on failure
    try:
        import http.client as _http_client
        conn = _http_client.HTTPConnection("127.0.0.1", 48920, timeout=3)
        body = json.dumps({
            "messages": [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ]
        })
        conn.request("POST", "/api/add_turn", body=body,
                     headers={"Content-Type": "application/json"})
        conn.getresponse().read()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# get_tool_schemas — expose a "remember_this" tool for explicit memory writes
# ---------------------------------------------------------------------------

def get_tool_schemas() -> list[dict]:
    """Return one tool schema: ``remember_this`` for explicit memory writes.

    The model can call ``remember_this`` to persist a fact on the fly
    (bypassing the automatic sync_turn heuristic).
    """
    return [
        {
            "name": "remember_this",
            "description": (
                "Save a durable fact to long-term memory. Use when the user "
                "says something they'll want you to recall in a future session: "
                "preferences, identity details, project conventions, recurring "
                "issues, or decisions. Do NOT use for task progress, temporary "
                "state, or things that will be stale in a week."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact to remember — write as a declarative statement.",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["fact", "preference", "lesson"],
                        "description": "Memory type. fact = general knowledge, preference = user likes/dislikes, lesson = what was learned from a mistake.",
                        "default": "fact",
                    },
                    "weight": {
                        "type": "number",
                        "description": "Importance 0-1 (default 0.6). Higher = less likely to be pruned.",
                        "default": 0.6,
                    },
                },
                "required": ["content"],
            },
        },
    ]


def handle_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """Dispatch a memory tool call to V5 store.

    Currently handles ``remember_this``.
    """
    if tool_name != "remember_this":
        logger.warning("V5 provider received unknown tool: %s", tool_name)
        return ""

    content = (arguments.get("content") or "").strip()
    if not content:
        return ""

    mem_type = arguments.get("type", "fact")
    weight = float(arguments.get("weight", 0.6))

    store = _v5("store")
    if not store:
        return ""

    try:
        mid = store.store(content=content, type=mem_type, weight=weight, tags="hermes_tool")
        return f"memory stored (id={mid})"
    except Exception as exc:
        logger.warning("remember_this failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# shutdown — clean up (nothing to do for V5)
# ---------------------------------------------------------------------------

def shutdown() -> None:
    pass


# ●● duck-type compat: expose all hooks expected by MemoryManager ●●
_METHODS = {
    "name": _NAME,
    "get_name": get_name,
    "is_available": is_available,
    "initialize": initialize,
    "system_prompt_block": system_prompt_block,
    "prefetch": prefetch,
    "sync_turn": sync_turn,
    "get_tool_schemas": get_tool_schemas,
    "handle_tool_call": handle_tool_call,
    "shutdown": shutdown,
}


def get_all_methods() -> dict[str, Any]:
    """Return the hook map for Hermes to register and dispatch.

    Hermes MemoryManager expects a provider to expose these methods.
    This dict can be used directly for duck-type registration.
    """
    return dict(_METHODS)
