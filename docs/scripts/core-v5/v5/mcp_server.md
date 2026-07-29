# mcp_server.py

> 源文件：`Ikaros-memory/v5/mcp_server.py`

v5.mcp_server — Ikaros V5 Memory as MCP stdio server.

Exposes tools for Hermes Agent / Hermes Studio to read/write/reflect
on Ikaros's long-term memory, emotion, and self-cognition.

All tools are registered from the single, standardized ``v5.tools`` layer
(see Ikaros-memory/v5/tools/__init__.py).  Every tool returns a JSON
*string* guarded by @safe_tool, so a single bad tool call can never crash
the server.  Error shape is uniform across all tools:

    {"ok": False, "error": "..."}

Registry as MCP server in config.yaml:
  mcp_servers:
    ikaros-v5-memory:
      command: ${IKAROS_PYTHON}
      args:
        - ${IKAROS_MEMORY}/v5/mcp_server.py
      enabled: true

## 内联注释摘录

# ── V5 Agent-ization: register the standardized v5_* tools ─────
# These wrap the existing V5 modules (never modifying them) so Ikaros's
# capabilities can be selected / invoked like Ekko in Hermes Studio.
# Every tool returns a JSON string and is guarded by @safe_tool, so the
# MCP protocol handshake (initialize / tools_list / tools_call) stays
# green and no single tool failure crashes the server.

