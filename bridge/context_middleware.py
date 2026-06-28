"""
Context compression middleware for the Ikaros bridge.

Integrates upstream ``agent.context_compressor.ContextCompressor`` into
the bridge's chat_completions flow so long conversations stay within
context window without losing the thread.

Architecture:
  - One ``SessionCompressor`` per active session (LRU cache, max 128 sessions)
  - Wraps ``agent.context_compressor.ContextCompressor`` (upstream's default engine)
  - Hooked into bridge via ``before_chat()`` / ``after_chat()`` calls

Usage:
    from bridge.context_middleware import get_compressor
    compressor = get_compressor(session_id)
    compressed = compressor.before_chat(messages)
    # ... call LLM ...
    compressor.after_chat(response_usage)
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---- Upstream import (lazy, so it doesn't crash if upstream changes) ----

def _get_compressor_cls():
    """Lazy import of upstream ContextCompressor.

    Falls back to a no-op stub if the upstream module is unavailable,
    so the bridge can still serve requests without it.
    """
    try:
        from agent.context_compressor import ContextCompressor
        return ContextCompressor
    except ImportError:
        logger.warning(
            "upstream ContextCompressor not available — "
            "using no-op stub (install hermes-agent/agent/ ⤴)"
        )
        return None


# ---- No-op stub when upstream is missing ----

class _NoopCompressor:
    """Fallback that passes messages through unchanged."""
    name = "noop"
    last_prompt_tokens = 0
    last_completion_tokens = 0
    last_total_tokens = 0
    threshold_tokens = 0
    context_length = 0
    compression_count = 0
    threshold_percent = 0.75
    protect_first_n = 3
    protect_last_n = 6

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        pass

    def should_compress(self, prompt_tokens: int = None) -> bool:
        return False

    def compress(self, messages, current_tokens=None, focus_topic=None, force=False):
        return messages

    def on_session_reset(self):
        pass

    def on_session_end(self, session_id, messages):
        pass


# ---- Session-managed compressor wrapper ----

class SessionCompressor:
    """Manages one compressor per session, with usage tracking."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        cls = _get_compressor_cls()
        if cls:
            self._engine = cls()
            logger.info("context_compressor: using upstream ContextCompressor for %s", session_id)
        else:
            self._engine = _NoopCompressor()
        self._last_compress_at = 0
        self._total_compressions = 0
        self._message_count = 0

    def before_chat(self, messages: List[Dict]) -> List[Dict]:
        """Called BEFORE sending messages to the LLM.

        If the compressor decides it's time, it compresses the middle
        of the conversation. Returns (possibly compressed) messages.
        """
        if not self._engine.should_compress():
            return messages

        try:
            compressed = self._engine.compress(messages)
            self._last_compress_at = time.time()
            self._total_compressions += 1
            saved = len(messages) - len(compressed)
            logger.info(
                "compressor[%s]: compressed %d→%d messages (%d saved, #%d)",
                self.session_id, len(messages), len(compressed),
                saved, self._total_compressions,
            )
            return compressed
        except Exception as exc:
            logger.warning("compressor[%s]: compress failed: %s", self.session_id, exc)
            return messages

    def after_chat(self, usage: Optional[Dict]) -> None:
        """Called AFTER an LLM response with the usage dict.

        The compressor tracks token counts so it knows when to fire next.
        """
        if usage:
            self._engine.update_from_response(usage)
        self._message_count += 1

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "message_count": self._message_count,
            "total_compressions": self._total_compressions,
            "last_compress_at": self._last_compress_at,
            "engine": self._engine.name,
            "threshold_percent": self._engine.threshold_percent,
            "context_length": self._engine.context_length,
            "compression_count": self._engine.compression_count,
        }


# ---- LRU cache of session compressors ----

class CompressorPool:
    """LRU pool of session compressors (max 128 active sessions)."""

    def __init__(self, max_sessions: int = 128):
        self._max = max_sessions
        self._pool: OrderedDict[str, SessionCompressor] = OrderedDict()

    def get(self, session_id: str) -> SessionCompressor:
        """Get (or create) the compressor for the given session."""
        if session_id not in self._pool:
            if len(self._pool) >= self._max:
                evicted = self._pool.popitem(last=False)
                logger.info("compressor pool: evicted %s (≥%d sessions)", evicted[0], self._max)
            self._pool[session_id] = SessionCompressor(session_id)
        else:
            self._pool.move_to_end(session_id)  # LRU refresh
        return self._pool[session_id]

    def remove(self, session_id: str) -> None:
        self._pool.pop(session_id, None)

    def clear(self) -> None:
        self._pool.clear()

    @property
    def active_count(self) -> int:
        return len(self._pool)

    def list_stats(self) -> List[Dict]:
        return [c.stats for c in self._pool.values()]


# ---- Global singleton ----

_pool: CompressorPool | None = None


def get_pool() -> CompressorPool:
    global _pool
    if _pool is None:
        _pool = CompressorPool()
    return _pool


def get_compressor(session_id: str) -> SessionCompressor:
    return get_pool().get(session_id)
