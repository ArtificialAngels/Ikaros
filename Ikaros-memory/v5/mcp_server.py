#!/usr/bin/env python3
"""v5.mcp_server — Ikaros V5 Memory as MCP stdio server.

Exposes tools for Hermes Agent / Hermes Studio to read/write/reflect
on Ikaros's long-term memory, emotion, and self-cognition.

Tools:
  v5_store(content, type, weight, tags, pad_p, pad_a, pad_d)
    → 存一条记忆，返 id

  v5_search(query, top_k, min_weight)
    → 双路融合检索 (FTS5 + 向量语义)

  v5_reflect(mode)
    → 触发一次反思循环 (reflect / philosophy / cycle)

  v5_latest_thought()
    → 最近一次内心独白 / 思考

Registry as MCP server in config.yaml:
  mcp_servers:
    ikaros-v5-memory:
      command: E:\\Ikaros\\portable-python\\python.exe
      args:
        - E:\\Ikaros\\Ikaros-memory\\v5\\mcp_server.py
      enabled: true
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# ── 确保 Ikaros-memory/ 在 Python 路径中 ──────────────────────
_HERE = Path(__file__).resolve().parent  # Ikaros-memory/v5/
_V5_ROOT = _HERE.parent                  # Ikaros-memory/
if str(_V5_ROOT) not in sys.path:
    sys.path.insert(0, str(_V5_ROOT))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [v5-mcp] %(message)s",
)
logger = logging.getLogger("ikaros.v5.mcp")


# ── MCP Server ─────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "Ikaros V5 Memory",
    instructions=(
        "Ikaros V5 Memory System — long-term memory store, "
        "semantic search, and self-reflection."
    ),
)


# ── Tool: v5_store ─────────────────────────────────────────────
@mcp.tool()
def v5_store(
    content: str,
    type: str = "fact",
    weight: float = 0.6,
    tags: str = "",
    pad_p: float = 0.0,
    pad_a: float = 0.0,
    pad_d: float = 0.0,
) -> str:
    """Store a memory into Ikaros's long-term memory.

    Args:
        content: 记忆内容 (text)
        type: 类型 (fact / lesson / conversation / belief / inner_monologue / self_reflection)
        weight: 权重 0.0-1.0 (越高越重要)
        tags: 逗号分隔标签 (e.g. "important,emotion:joy")
        pad_p: PAD 情感 P (pleasure, -1~1)
        pad_a: PAD 情感 A (arousal, -1~1)
        pad_d: PAD 情感 D (dominance, -1~1)

    Returns:
        JSON: {"id": int, "ok": true} 或 {"error": "..."}
    """
    try:
        from v5 import store as v4

        mid = v4.store(
            content=content,
            type=type,
            weight=max(0.0, min(1.0, weight)),
            tags=tags,
            pad_p=pad_p,
            pad_a=pad_a,
            pad_d=pad_d,
        )
        return json.dumps({"id": mid, "ok": True}, ensure_ascii=False)
    except Exception as e:
        logger.warning("v5_store failed: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ── Tool: v5_search ────────────────────────────────────────────
@mcp.tool()
def v5_search(
    query: str,
    top_k: int = 5,
    min_weight: float = 0.0,
) -> str:
    """Search Ikaros's long-term memory (FTS5 keyword + vector semantic fusion).

    Args:
        query: 搜索关键词
        top_k: 返回条数 (max 20)
        min_weight: 最低权重过滤

    Returns:
        JSON array of {id, content, type, weight, score, source, pad_p, pad_a, pad_d}
    """
    try:
        top_k = max(1, min(20, top_k))
        from v5.search import fused_search

        results = fused_search(query, top_k=top_k)
        if not results:
            return "[]"

        # 过滤权重
        if min_weight > 0:
            results = [r for r in results if r.get("weight", 0) >= min_weight]

        return json.dumps(results, ensure_ascii=False, default=str)
    except Exception as e:
        logger.warning("v5_search failed: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ── Tool: v5_reflect ───────────────────────────────────────────
@mcp.tool()
def v5_reflect(mode: str = "cycle") -> str:
    """Trigger a reflection cycle in Ikaros's self-cognition engine.

    Args:
        mode: 反思模式
          - "cycle"   → 完整节拍 (reflect + 好奇心 + 关怀 + 精力)
          - "reflect" → 仅自我反思 (用 self_model + 记忆)
          - "philosophy" → 仅哲学探索 (爱/人/机器人/自我)

    Returns:
        JSON: {"mode": "...", "text": "...", "ok": true} 或 {"error": "..."}
    """
    try:
        import v5.metacog as metacog

        if mode == "reflect":
            result = metacog.reflect_once()
        elif mode == "philosophy":
            result = metacog.explore_philosophy()
        else:
            result = metacog.cycle()

        if result is None:
            return json.dumps(
                {"mode": mode, "text": None, "ok": True, "note": "no output"},
                ensure_ascii=False,
            )

        # metacog 返回 dict，序列化
        return json.dumps(
            {"mode": mode, **result, "ok": True},
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        logger.warning("v5_reflect failed: %s", e)
        return json.dumps({"error": str(e), "mode": mode}, ensure_ascii=False)


# ── Tool: v5_latest_thought ────────────────────────────────────
@mcp.tool()
def v5_latest_thought() -> str:
    """Get Ikaros's most recent inner thought / monologue.

    Returns:
        JSON: {"text": "...", "mood": "...", "intensity": 0.0, "created": 0.0}
        或 {"error": "..."}
    """
    try:
        latest = _V5_ROOT / "data" / "v5" / "latest_thought.json"
        if not latest.is_file():
            return json.dumps(
                {"text": None, "note": "no thought yet"}, ensure_ascii=False
            )
        data = json.loads(latest.read_text(encoding="utf-8"))
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception as e:
        logger.warning("v5_latest_thought failed: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ── Tool: v5_status ────────────────────────────────────────────
@mcp.tool()
def v5_status() -> str:
    """Get the current status of Ikaros V5 memory system.

    Returns hardware-level summary: memory count, DB size, last update timestamps,
    current mood (PAD), self-model summary, and subsystem health.

    Returns:
        JSON status dict
    """
    result: dict = {"ok": False}

    try:
        from v5 import store as v4

        with v4.conn() as c:
            count = c.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
            result["memory_count"] = count
    except Exception as e:
        result["memory_error"] = str(e)

    # DB file size
    db_path = _V5_ROOT / "data" / "v4" / "v4.db"
    if db_path.is_file():
        result["db_size_kb"] = round(db_path.stat().st_size / 1024, 1)

    # 情感状态
    try:
        from v5.affect import AffectState

        state = AffectState.load()
        p, a, d = state.pleasure, state.arousal, state.dominance
        result["mood"] = {"pleasure": p, "arousal": a, "dominance": d, "label": state.to_prompt()}
    except Exception as e:
        result["mood_error"] = str(e)

    # 自我模型
    try:
        from v5.self_model import SelfModel

        sm = SelfModel.load()
        beliefs = sm.data.get("beliefs", {}) if hasattr(sm, "data") else {}
        result["self"] = {
            "curiosity": sm.get_curiosity(),
            "belief_count": len(beliefs),
            "last_updated": getattr(sm, "last_updated", None),
        }
    except Exception as e:
        result["self_error"] = str(e)

    # 文件时间戳
    for name in ("latest_thought", "affect", "self_model", "subconscious"):
        f = _V5_ROOT / "data" / "v5" / f"{name}.json"
        if f.is_file():
            result[f"{name}_ts"] = f.stat().st_mtime

    result["ok"] = True
    return json.dumps(result, ensure_ascii=False, default=str)


# ── V5 Agent-ization: register the 19 standardized v5_* tools ──
# These wrap the existing V5 modules (never modifying them) so Ikaros's
# capabilities can be selected / invoked like Ekko in Hermes Studio.
# The legacy v5_store / v5_search / v5_reflect / v5_latest_thought /
# v5_status tools above are kept for backward compatibility.
from v5.tools import (  # noqa: E402
    v5_analyze_emotion, v5_emotion_status, v5_emotion_label,
    v5_memory_store, v5_memory_search, v5_memory_get, v5_memory_delete,
    v5_memory_stats,
    v5_self_model, v5_self_reflect, v5_curiosity_check, v5_subconscious,
    v5_care_check, v5_care_status,
    v5_vitality, v5_vitality_tick,
    v5_relationship, v5_relationship_tick,
    v5_narrative_generate, v5_dissonance_check, v5_proactive_check,
    v5_self_discover, v5_reflect_run_op,
)

_NEW_V5_TOOLS = [
    v5_analyze_emotion, v5_emotion_status, v5_emotion_label,
    v5_memory_store, v5_memory_search, v5_memory_get, v5_memory_delete,
    v5_memory_stats,
    v5_self_model, v5_self_reflect, v5_curiosity_check, v5_subconscious,
    v5_care_check, v5_care_status,
    v5_vitality, v5_vitality_tick,
    v5_relationship, v5_relationship_tick,
    v5_narrative_generate, v5_dissonance_check, v5_proactive_check,
    v5_self_discover, v5_reflect_run_op,
]
for _tool_fn in _NEW_V5_TOOLS:
    try:
        mcp.add_tool(_tool_fn)
    except Exception as _e:  # noqa: BLE001
        logger.warning("failed to register tool %s: %s",
                       getattr(_tool_fn, "__name__", _tool_fn), _e)


# ── 启动 ────────────────────────────────────────────────────────
def main():
    if len(sys.argv) > 1 and sys.argv[1] == "sse":
        # Hermes Studio transport: SSE on :9877
        try:
            mcp.settings.host = "127.0.0.1"
            mcp.settings.port = 9877
        except Exception:  # noqa: BLE001
            pass
        logger.info("v5 MCP server starting (sse) on 127.0.0.1:9877 ...")
        mcp.run(transport="sse")
    else:
        logger.info("v5 MCP server starting (stdio)...")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
