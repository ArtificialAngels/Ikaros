# __init__.py

> 源文件：`Ikaros-memory/v5/tools/__init__.py`

v5.tools — unified export + registration entry for the V5 MCP tool layer.

Every capability of Ikaros V5 is exposed here as a pluggable, standardized
`v5_*` tool.  These tools are thin, dependency-safe wrappers around the
existing V5 modules (affect / store / metacog / care / vitality / ...).
The underlying modules are NEVER modified — only wrapped.

Import surface:
    from v5.tools import *          # registers all 24 v5_* tools in __all__
    from v5.tools.emotion_tool import v5_emotion_status
    from v5.tools.memory_tool import v5_memory_store, v5_memory_search

All tool functions return a JSON *string* and are guarded by @safe_tool, so
they can be invoked over MCP (stdio / SSE) or by the agent orchestrator
without ever raising.

## 内联注释摘录

# Allow the documented acceptance command
#   `from v5.tools import *; print('tools OK:', len(__all__))`
# to work (Python's `import *` does not otherwise expose __all__).

