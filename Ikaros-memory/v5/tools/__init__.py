"""v5.tools — unified export + registration entry for the V5 MCP tool layer.

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
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure Ikaros-memory/ is importable (so `import v5` works from anywhere).
V5_ROOT = Path(__file__).resolve().parent.parent.parent
if str(V5_ROOT) not in sys.path:
    sys.path.insert(0, str(V5_ROOT))

from v5.tools import care_tool
from v5.tools import emotion_tool
from v5.tools import extra_tool
from v5.tools import memory_tool
from v5.tools import relationship_tool
from v5.tools import self_tool
from v5.tools import vitality_tool

# Collect every v5_* callable from the submodules into __all__.
__all__: list[str] = []
_SEEN = set()
for _mod in (
    emotion_tool, memory_tool, self_tool,
    care_tool, vitality_tool, relationship_tool, extra_tool,
):
    for _name in dir(_mod):
        if _name.startswith("v5_") and _name not in _SEEN:
            _fn = getattr(_mod, _name)
            if callable(_fn):
                globals()[_name] = _fn
                __all__.append(_name)
                _SEEN.add(_name)

__all__.sort()

# Allow the documented acceptance command
#   `from v5.tools import *; print('tools OK:', len(__all__))`
# to work (Python's `import *` does not otherwise expose __all__).
if "__all__" not in __all__:
    __all__.append("__all__")
