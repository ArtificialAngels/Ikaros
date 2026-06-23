"""
Copilot ACP bridge — routes chat requests to ``copilot --acp --stdio``.

The upstream ``agent.copilot_acp_client.CopilotACPClient`` wraps GitHub's
Copilot CLI as an OpenAI-compatible chat backend. This module:

1. Detects whether ``copilot`` CLI is available on PATH (or via
   ``HERMES_COPILOT_ACP_COMMAND`` env var).
2. Provides a thin OpenAI-compatible ``chat()`` function.
3. Falls back gracefully with a clear error when the CLI is missing.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---- Lazy import of upstream (won't crash if hermes-agent not on PYTHONPATH) ----

def _get_acp_client():
    """Lazy import of upstream CopilotACPClient."""
    try:
        from agent.copilot_acp_client import CopilotACPClient
        return CopilotACPClient
    except ImportError:
        logger.warning(
            "CopilotACPClient not available (hermes-agent/agent/ not on PYTHONPATH?)"
        )
        return None


# ---- Availability check ----

def _resolve_copilot_command() -> str | None:
    """Return path to ``copilot`` CLI, or None if not found."""
    cmd = (
        os.environ.get("HERMES_COPILOT_ACP_COMMAND", "").strip()
        or os.environ.get("COPILOT_CLI_PATH", "").strip()
    )
    if cmd:
        return cmd

    # Search PATH
    resolved = shutil.which("copilot")
    return resolved


def is_available() -> bool:
    """True iff copilot CLI is reachable."""
    path = _resolve_copilot_command()
    if not path:
        return False
    try:
        subprocess.run([path, "--version"], capture_output=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---- OpenAI-compatible chat wrapper ----

_client_instance = None


def _get_client() -> Any | None:
    """Get or create the singleton CopilotACPClient."""
    global _client_instance
    if _client_instance is not None:
        return _client_instance

    cls = _get_acp_client()
    if cls is None:
        return None

    cmd = _resolve_copilot_command()
    if not cmd:
        logger.error("copilot CLI not found — ACP client unavailable")
        return None

    try:
        _client_instance = cls()
        logger.info("CopilotACPClient initialized (command: %s)", cmd)
        return _client_instance
    except Exception as exc:
        logger.error("CopilotACPClient init failed: %s", exc)
        return None


async def chat(
    messages: List[Dict[str, Any]],
    model: str = "copilot",
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> Dict[str, Any]:
    """Send a chat request to Copilot ACP, returning OpenAI-compatible response.

    Args:
        messages: Chat messages in OpenAI format.
        model: Ignored — Copilot uses its own model selection.
        max_tokens: Not directly supported — Copilot manages its own budget.
        temperature: Not supported by ACP.

    Returns:
        OpenAI-compatible response dict with ``choices``, ``usage``, etc.

    Raises:
        RuntimeError: If ACP client is unavailable or the call fails.
    """
    client = _get_client()
    if client is None:
        raise RuntimeError(
            "Copilot ACP is not available. "
            "Install @github/copilot: npm install -g @github/copilot"
        )

    try:
        result = client._create_chat_completion(
            messages=messages,
            model=model,
        )
        return result
    except Exception as exc:
        logger.error("Copilot ACP chat failed: %s", exc)
        raise RuntimeError(f"Copilot ACP error: {exc}") from exc


def close():
    """Close the ACP client and release resources."""
    global _client_instance
    if _client_instance is not None:
        try:
            _client_instance.close()
        except Exception:
            pass
        _client_instance = None
