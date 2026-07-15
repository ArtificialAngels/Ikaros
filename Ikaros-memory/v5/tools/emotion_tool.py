"""v5.tools.emotion_tool — 3 emotion tools.

  v5_analyze_emotion(text)   -> apply a PAD event + optional causal record
  v5_emotion_status()        -> current PAD state (no external dependency)
  v5_emotion_label(text)     -> 1-2 emotion tags (LLM, falls back to rule)

All return JSON strings; all are wrapped with @safe_tool so they never raise.
"""

from __future__ import annotations

import json

from v5.tools.utils import safe_tool, dumps, local_llm_available


@safe_tool
def v5_analyze_emotion(text: str) -> str:
    """Update Ikaros's PAD emotion state from a piece of text and return it.

    Call chain: AffectState.load() -> apply_event(text); also fires a
    best-effort causal-emotion record (silently skipped on failure).
    Fallback: :8080 down => still returns the (updated) current state.
    """
    from v5.affect import AffectState, apply_event
    from v5.emotional_memory import maybe_record_emotion

    old = AffectState.load()
    old_pad = (old.pleasure, old.arousal, old.dominance)
    new = apply_event(text)
    new_pad = (new.pleasure, new.arousal, new.dominance)

    try:
        maybe_record_emotion(old_pad, new_pad, text)
    except Exception:  # noqa: BLE001
        pass

    delta = abs(new_pad[0] - old_pad[0]) + abs(new_pad[1] - old_pad[1]) + abs(new_pad[2] - old_pad[2])
    return dumps({
        "pleasure": round(new.pleasure, 4),
        "arousal": round(new.arousal, 4),
        "dominance": round(new.dominance, 4),
        "mood_label": new.to_prompt(),
        "delta": round(delta, 4),
        "intensity": round(min(1.0, delta / 0.6), 3),
    })


@safe_tool
def v5_emotion_status() -> str:
    """Return the current PAD emotion state (no external dependency).

    Includes a best-effort vitality label (skipped gracefully if psutil /
    vitality unavailable).
    """
    from v5.affect import AffectState

    s = AffectState.load().decay()

    vitality_label = None
    try:
        from v5.vitality import Vitality
        vitality_label = Vitality.load().label()
    except Exception:  # noqa: BLE001
        pass

    return dumps({
        "pleasure": round(s.pleasure, 4),
        "arousal": round(s.arousal, 4),
        "dominance": round(s.dominance, 4),
        "mood_label": s.to_prompt(),
        "vitality_label": vitality_label,
    })


@safe_tool
def v5_emotion_label(text: str) -> str:
    """Return 1-2 emotion tags for the text.

    Call chain: emotional_memory.label_emotion() (local qwen3-1.7b, falls
    back to a PAD->tag rule internally).  `method` reports which path ran.
    Fallback: :8080 down => rule-based tags.
    """
    from v5.affect import AffectState
    from v5.emotional_memory import label_emotion

    s = AffectState.load().decay()
    new_pad = (s.pleasure, s.arousal, s.dominance)
    tags = label_emotion(text, new_pad)

    method = "llm" if local_llm_available() else "rule"
    return dumps({"tags": tags, "method": method})
