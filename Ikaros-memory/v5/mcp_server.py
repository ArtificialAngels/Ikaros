#!/usr/bin/env python3
# 详细说明见 docs/scripts/Ikaros-memory/v5/mcp_server.md

from __future__ import annotations

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


# 内联说明见 docs/scripts/Ikaros-memory/v5/mcp_server.md（见“内联注释摘录”）
from v5.tools import (  # noqa: E402
    v5_analyze_emotion, v5_emotion_status, v5_emotion_label,
    v5_memory_store, v5_memory_search, v5_memory_get, v5_memory_delete,
    v5_memory_stats,
    v5_self_model, v5_self_reflect, v5_latest_thought,
    v5_curiosity_check, v5_subconscious,
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
    v5_self_model, v5_self_reflect, v5_latest_thought,
    v5_curiosity_check, v5_subconscious,
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
        # Hermes Studio transport: SSE on :9877.
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = 9877
        logger.info("v5 MCP server starting (sse) on 127.0.0.1:9877 ...")
        mcp.run(transport="sse")
    else:
        logger.info("v5 MCP server starting (stdio)...")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()