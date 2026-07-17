# extra_tool.py

> 源文件：`Ikaros-memory/v5/tools/extra_tool.py`

v5.tools.extra_tool — P1/P2 tools (narrative, dissonance, proactive,
self-discovery, reflect-op).

These wrap the heavier / less critical subsystems.  All are wrapped with
@safe_tool and have graceful fallbacks when :8080 / ChromaDB / Hermes are
unavailable.
