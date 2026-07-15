"""v5.tools.self_tool — 5 self-cognition tools.

  v5_self_model()        -> identity / capabilities / beliefs / questions
  v5_self_reflect(mode)  -> one metacog cycle (reflect | philosophy | cycle)
  v5_latest_thought()    -> most recent inner thought (data/v5/latest_thought.json)
  v5_curiosity_check()   -> curiosity level + idle + pending question
  v5_subconscious()      -> latest subconscious whisper (data/v5/subconscious.json)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from v5.tools.utils import safe_tool, dumps

_V5_DATA = Path(__file__).resolve().parent.parent.parent / "data" / "v5"


@safe_tool
def v5_self_model() -> str:
    """Return Ikaros's persistent self model (who she is)."""
    from v5.self_model import SelfModel
    sm = SelfModel.load()
    d = sm.data
    return dumps({
        "identity": d.get("identity"),
        "capabilities": d.get("capabilities"),
        "beliefs": d.get("beliefs"),
        "questions": d.get("questions"),
        "curiosity": sm.get_curiosity(),
    })


@safe_tool
def v5_self_reflect(mode: str = "reflect") -> str:
    """Run one metacog cycle.

    mode: "reflect" | "philosophy" | "cycle"
    Fallback: :8080 down => {"text": null, "note": "LLM unavailable"}.
    """
    import v5.metacog as metacog

    if mode == "reflect":
        r = metacog.reflect_once()
    elif mode == "philosophy":
        r = metacog.explore_philosophy()
    else:
        r = metacog.cycle()

    if r is None:
        return dumps({"text": None, "ok": True, "note": "LLM unavailable"}, ensure_ascii=False)
    return dumps({"mode": mode, "ok": True, **r}, ensure_ascii=False)


@safe_tool
def v5_latest_thought() -> str:
    """Return Ikaros's most recent inner thought / monologue."""
    p = _V5_DATA / "latest_thought.json"
    if not p.is_file():
        return dumps({"text": None, "note": "no thought yet"}, ensure_ascii=False)
    data = json.loads(p.read_text(encoding="utf-8"))
    return dumps(data, ensure_ascii=False)


@safe_tool
def v5_curiosity_check() -> str:
    """Return the current curiosity drive state."""
    from v5.self_model import SelfModel
    sm = SelfModel.load()
    level = sm.get_curiosity()

    idle_minutes = 0.0
    try:
        c = sm.data.get("curiosity", {})
        last = c.get("last_interaction_ts", 0) or 0
        idle_minutes = round((time.time() - last) / 60.0, 1) if last else 0.0
    except Exception:  # noqa: BLE001
        pass

    questions = sm.data.get("questions", []) or []
    has_question = bool(questions)
    question_text = questions[0] if has_question else None
    return dumps({
        "level": level,
        "idle_minutes": idle_minutes,
        "has_question": has_question,
        "question_text": question_text,
    })


@safe_tool
def v5_subconscious() -> str:
    """Return the latest subconscious whisper."""
    p = _V5_DATA / "subconscious.json"
    if not p.is_file():
        return dumps({"text": None, "note": "no subconscious yet"}, ensure_ascii=False)
    data = json.loads(p.read_text(encoding="utf-8"))
    return dumps(data, ensure_ascii=False)
