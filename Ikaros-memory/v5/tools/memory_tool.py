"""v5.tools.memory_tool — 5 memory tools.

  v5_memory_store(content, ...)     -> store a memory, return {id, ok}
  v5_memory_search(query, ...)      -> fused / FTS5 / emotion-tag search
  v5_memory_get(memory_id)          -> fetch one memory
  v5_memory_delete(memory_id)       -> delete one memory
  v5_memory_stats()                 -> storage statistics

Extended from the legacy mcp_server v5_store/v5_search with Ekko-style
structured fields (domain / category_path / key) and time / emotion filters.
"""

from __future__ import annotations

import json

from v5.tools.utils import safe_tool, dumps


@safe_tool
def v5_memory_store(
    content: str,
    type: str = "fact",
    weight: float = 0.6,
    tags: str = "",
    domain: str = None,
    category_path: str = None,
    key: str = None,
    importance: float = 0.5,
    pad_p: float = 0.0,
    pad_a: float = 0.0,
    pad_d: float = 0.0,
) -> str:
    """Store a memory (V5 native + optional Ekko-style structured fields).

    Structured fields are encoded into tags:
      v5_domain:<domain>  v5_cat:<category_path>  v5_key:<key>
    so they can be retrieved exactly later (see memory_api / v5_memory_search).

    Optional: runs dissonance.detect_dissonance() for fact/preference types.
    """
    tag_set = [t for t in (tags or "").split(",") if t]
    if domain:
        tag_set.append(f"v5_domain:{domain}")
    if category_path:
        tag_set.append(f"v5_cat:{category_path}")
    if key:
        tag_set.append(f"v5_key:{key}")
    combined_tags = ",".join(dict.fromkeys(tag_set))

    from v5 import store as v4

    mid = v4.store(
        content=content,
        type=type,
        weight=max(0.0, min(1.0, weight)),
        tags=combined_tags,
        pad_p=float(pad_p),
        pad_a=float(pad_a),
        pad_d=float(pad_d),
    )

    dissonance = None
    try:
        from v5.dissonance import detect_dissonance
        dr = detect_dissonance(content, type)
        if dr and dr.get("conflicts"):
            dissonance = dr
    except Exception:  # noqa: BLE001
        pass

    return dumps({"id": mid, "ok": True, "dissonance": dissonance})


@safe_tool
def v5_memory_search(
    query: str,
    top_k: int = 5,
    min_weight: float = 0.0,
    time_start: float = None,
    time_end: float = None,
    exclude: str = None,
    emotion_tag: str = None,
) -> str:
    """Search long-term memory.

    Paths (first match wins):
      1. emotion_tag given  -> emotional_memory.search_by_emotion()
      2. time/exclude given  -> memory_retrieval.retrieve() (3-way fusion)
      3. default            -> search.fused_search() (FTS5 + vector)
      4. any failure        -> FTS5 only (store.search)
    Always returns a JSON array string; never raises.
    """
    # 1. emotion-tag retrieval
    if emotion_tag:
        from v5.emotional_memory import search_by_emotion
        results = search_by_emotion(emotion_tag, top_k=top_k)
        return dumps(results)

    exclude_list = None
    if exclude:
        exclude_list = [e for e in exclude.split(",") if e]

    # 2. advanced fused retrieval (time range / exclusion filters)
    if (time_start is not None and time_end is not None) or exclude_list:
        try:
            from v5.memory_retrieval import retrieve
            time_range = (time_start, time_end) if (time_start and time_end) else None
            results = retrieve(query, top_k=top_k, time_range=time_range,
                               exclude=exclude_list, min_weight=min_weight)
            if results:
                return dumps(results)
        except Exception:  # noqa: BLE001
            pass

    # 3. default fused search (FTS5 + vector), fallback FTS5 only
    try:
        from v5.search import fused_search
        results = fused_search(query, top_k=top_k)
        if results:
            if min_weight > 0:
                results = [r for r in results if float(r.get("weight", 0)) >= min_weight]
            return dumps(results)
    except Exception:  # noqa: BLE001
        pass

    # 4. FTS5 fallback
    try:
        from v5 import store as v4
        mems = v4.search(query, top_k=top_k, min_weight=min_weight)
        return dumps([
            {
                "id": str(m.id), "content": m.content, "type": m.type,
                "weight": m.weight, "score": 0.3, "source": "fts",
            }
            for m in mems
        ])
    except Exception:  # noqa: BLE001
        return "[]"


@safe_tool
def v5_memory_get(memory_id: int) -> str:
    """Fetch a single memory by id."""
    from v5 import store as v4
    m = v4.get(int(memory_id))
    if m is None:
        return dumps({"ok": False, "error": "not_found", "id": memory_id})
    return dumps({
        "id": m.id, "content": m.content, "type": m.type, "tags": m.tags,
        "weight": m.weight, "created": m.created,
        "pad_p": m.pad_p, "pad_a": m.pad_a, "pad_d": m.pad_d,
    })


@safe_tool
def v5_memory_delete(memory_id: int) -> str:
    """Delete a single memory by id."""
    from v5 import store as v4
    ok = bool(v4.delete(int(memory_id)))
    return dumps({"ok": ok, "id": memory_id})


@safe_tool
def v5_memory_stats() -> str:
    """Return storage statistics."""
    from v5 import store as v4
    return dumps(v4.stats())
