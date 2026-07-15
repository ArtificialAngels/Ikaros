"""v5.tools.utils — shared helpers for the V5 MCP tool layer.

Design rules (from the V5 agent-ization plan):
  - Every tool has a fallback.  No tool may raise an unhandled exception.
  - All tool functions return a JSON *string* (so they serialize cleanly
    over MCP stdio / SSE and can be parsed by Hermes Studio).
  - Heavy v5 submodules (affect / store / psutil-backed vitality ...) are
    imported *lazily inside* each tool function, never at module top, so
    `from v5.tools import *` stays importable even in a minimal env.
"""

from __future__ import annotations

import functools
import importlib
import json
import logging
import socket
import sys
from pathlib import Path

logger = logging.getLogger("ikaros.v5.tools")

# Ikaros-memory/  (tools/ -> v5/ -> Ikaros-memory/)
V5_ROOT = Path(__file__).resolve().parent.parent.parent
if str(V5_ROOT) not in sys.path:
    sys.path.insert(0, str(V5_ROOT))

# Local LLM (qwen3-8b) listens on :8080.  Used only as a *best-effort*
# availability indicator so tools can report which code path they took.
_LOCAL_LLM_HOST = "127.0.0.1"
_LOCAL_LLM_PORT = 8080


def require_module(module_path: str):
    """Safely import a module, return None on failure (instead of raising)."""
    try:
        return importlib.import_module(module_path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("require_module failed for %s: %s", module_path, exc)
        return None


def safe_tool(fn):
    """Decorator: wrap a tool call in try/except.

    On success the decorated function's own return value is passed through
    (typically a JSON string).  On any unexpected exception we return a
    structured error JSON string instead of letting the exception escape
    (which would crash the MCP server / agent loop).
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tool %s failed: %s", getattr(fn, "__name__", "?"), exc)
            return json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "tool": getattr(fn, "__name__", None),
                },
                ensure_ascii=False,
            )

    return wrapper


def dumps(obj, ensure_ascii: bool = False) -> str:
    """json.dumps with unicode preserved and a tolerant default serializer.

    Accepts an optional ``ensure_ascii`` flag (defaults to False so Chinese
    text stays readable in tool output) and a tolerant ``default`` serializer
    for objects json can't encode directly.
    """
    return json.dumps(obj, ensure_ascii=ensure_ascii, default=str)


def local_llm_available(host: str = _LOCAL_LLM_HOST, port: int = _LOCAL_LLM_PORT,
                        timeout: float = 1.0) -> bool:
    """Best-effort TCP probe: is the local LLM server reachable?

    Used to decide whether a tool likely ran via the LLM ('llm') or fell
    back to a rule ('rule').  Cheap (no HTTP parse) and never raises.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False
