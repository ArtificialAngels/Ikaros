# utils.py

> 源文件：`Ikaros-memory/v5/tools/utils.py`

v5.tools.utils — shared helpers for the V5 MCP tool layer.

Design rules (from the V5 agent-ization plan):
  - Every tool has a fallback.  No tool may raise an unhandled exception.
  - All tool functions return a JSON *string* (so they serialize cleanly
    over MCP stdio / SSE and can be parsed by Hermes Studio).
  - Heavy v5 submodules (affect / store / psutil-backed vitality ...) are
    imported *lazily inside* each tool function, never at module top, so
    `from v5.tools import *` stays importable even in a minimal env.
