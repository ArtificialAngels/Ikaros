"""
WebUISettingsStore: persistent WebUI preferences backed by a single JSON file.

The new WebUI (`hermes/static/index.html` from hermes-webui) issues
``GET /api/settings`` and ``POST /api/settings`` to read and write
user-tweakable preferences — theme, skin, language, display toggles,
agent limits, memory caps, session policies, privacy flags, etc.

Previously these were never persisted: the JS adapter (`api-adapter.js`)
returned a hardcoded default on GET and a no-op on POST, so every page
reload started from scratch. This module is the server-side half of
the fix: the adapter now translates ``/api/settings`` to
``/api/webui/settings`` and just passes the server response through;
this store is the actual source of truth.

File layout
-----------
A single JSON file at ``<base_dir>/webui_settings.json`` (or the fallback
``<hermes>/data/webui_settings.json`` if no ``agent.paths["base"]`` is
available — same fallback policy as :class:`hermes.sessions.SessionStore`).

File schema is the full settings dict (no wrapping envelope). On first
load, if the file is missing or unreadable, the store seeds itself from
``DEFAULT_SETTINGS`` and persists that baseline. Subsequent ``update()``
calls do a *shallow* merge on top-level keys, plus a *one-level deep*
merge for the well-known nested-dict keys (``display``, ``agent``,
``memory``, ``session``, ``privacy``) so a POST that only changes
``display.streaming`` doesn't wipe out ``display.compact_mode``.

Concurrency
-----------
Writes are serialised by an :class:`asyncio.Lock` (one per store
instance). Reads are lock-free on the assumption that FastAPI serves
all requests from a single event-loop thread, but each ``get()`` returns
a deep copy so callers can mutate without affecting subsequent reads.

Public API
----------
- ``WebUISettingsStore(base_dir)``   -> construct
- ``.get() -> dict``                  -> full current settings (deep copy)
- ``.update(partial: dict) -> dict`` -> shallow + nested-1 merge, persist, return new state
- ``.reset() -> dict``                -> drop file, re-seed from DEFAULT_SETTINGS, return it
- ``.stats() -> dict``                -> path + last-write timestamp
- ``get_settings_store(base_dir)``    -> process-wide singleton accessor (cached per base_dir)
"""
from __future__ import annotations
import asyncio
import copy
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes.webui_settings")

# Filename under the base dir. Single global settings file (no per-user
# split — Hermes currently has no auth layer).
_SETTINGS_FILENAME = "webui_settings.json"

# Keys whose values are nested dicts and should be merged one level deep
# (not overwritten wholesale) on update(). Keep this list aligned with
# the schema surface used by `api-adapter.js`.
_NESTED_DICT_KEYS = ("display", "agent", "memory", "session", "privacy")

# Default baseline. MUST stay in sync with the original hardcoded default
# in `hermes/static/api-adapter.js` (which this replaces). When the file
# is missing or unreadable, this is what the user sees on first GET.
DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "dark",
    "skin": "default",
    "language": "zh",
    "send_key": "enter",
    "show_token_usage": True,
    "show_thinking": True,
    "show_tps": False,
    "show_cli_sessions": False,
    "show_quota_chip": False,
    "hide_empty_state_suggestions": False,
    "fade_text_effect": False,
    "sound_enabled": False,
    "notifications_enabled": False,
    "whats_new_summary_enabled": False,
    "whitelisted_browsers": [],
    "session_endless_scroll_enabled": False,
    "bot_name": "Hermes",
    "simplified_tool_calling": True,
    "terminal_auto_expand_on_output": False,
    "session_jump_buttons_enabled": False,
    "sidebar_density": "compact",
    "pinned_sessions_limit": 3,
    "busy_input_mode": "queue",
    "onboarding_completed": True,
    "check_for_updates": False,
    "show_reasoning": True,
    "reasoning_effort": "medium",
    "display": {
        "show_reasoning": True,
        "show_cost": False,
        "compact_mode": False,
        "streaming": True,
    },
    "agent": {
        "max_turns": 50,
        "timeout": 300,
        "tools_required": False,
    },
    "memory": {
        "enabled": True,
        "max_chars": 4000,
    },
    "session": {
        "idle_timeout": 1800,
        "reset_schedule": None,
    },
    "privacy": {
        "pii_redaction": False,
    },
}


def _deep_copy_settings(data: dict) -> dict:
    """Return a detached deep copy safe to hand back to API callers."""
    return copy.deepcopy(data)


def _shallow_merge_nested(base: dict, patch: dict) -> dict:
    """Merge ``patch`` into ``base`` with one-level deep merge for nested
    dicts and shallow overwrite for everything else.

    - Scalars, lists, and unknown nested-dict keys in ``patch`` replace
      the value in ``base`` (so the caller can intentionally clear a
      list by sending ``[]``).
    - For keys listed in ``_NESTED_DICT_KEYS`` where both sides are
      dicts, we recursively merge one level so that
      ``{"display": {"streaming": False}}`` does not wipe out
      ``display.compact_mode``.
    - The function does NOT mutate either input. It returns a new dict.
    """
    out = dict(base)
    for k, v in (patch or {}).items():
        if (
            k in _NESTED_DICT_KEYS
            and isinstance(v, dict)
            and isinstance(out.get(k), dict)
        ):
            merged = dict(out[k])
            for nk, nv in v.items():
                merged[nk] = nv
            out[k] = merged
        else:
            out[k] = v
    return out


class WebUISettingsStore:
    """File-backed WebUI settings store.

    Thread-safety: writes go through ``self._lock``; reads return a deep
    copy so concurrent callers can't observe each other's mutations.

    Disk safety: writes are atomic (tempfile + ``os.replace``), so a
    crash mid-write cannot leave a half-written file that would fail
    JSON decoding on next load.
    """

    def __init__(self, base_dir: str | os.PathLike[str]):
        self.base_dir = Path(base_dir)
        # We don't pre-create the base dir; we only create it on the
        # first write. This matches the design of `SessionStore` and
        # keeps the read path side-effect-free.
        self._path = self.base_dir / _SETTINGS_FILENAME
        self._lock = asyncio.Lock()
        # In-memory cache. Populated lazily on first read; invalidated
        # on every successful write. The cache holds the *raw* dict;
        # public ``get()`` returns a deep copy.
        self._cache: dict | None = None
        self._last_loaded: float = 0.0  # epoch seconds; 0 = never

    # ---- low-level IO -----------------------------------------------------

    def _seed(self) -> dict:
        """Return a fresh deep copy of the default baseline."""
        return copy.deepcopy(DEFAULT_SETTINGS)

    def _read_raw(self) -> dict | None:
        """Read the on-disk file. Returns None if missing or unreadable."""
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                f"WebUISettingsStore: failed to read {self._path}: {e}; "
                "will re-seed from defaults"
            )
            return None
        if not isinstance(data, dict):
            logger.warning(
                f"WebUISettingsStore: {self._path} is not a JSON object; "
                "will re-seed from defaults"
            )
            return None
        return data

    def _atomic_write(self, data: dict) -> None:
        """Persist ``data`` atomically. Caller must hold ``self._lock``.

        Creates ``base_dir`` if missing. Writes to ``<file>.tmp`` first
        then ``os.replace`` onto the real path. On Windows, the temp
        file is created in the same directory so ``os.replace`` is
        atomic on the same volume.
        """
        self.base_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError as e:
            logger.error(f"WebUISettingsStore: failed to write {self._path}: {e}")
            # Best-effort cleanup of the stray temp file
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise

    def _load(self) -> dict:
        """Return the in-memory cache, loading from disk if needed.

        Unlocked — call from contexts where you've already serialised
        (e.g. inside ``self._lock``) or where staleness for one tick
        is acceptable (the public ``get()`` path).
        """
        if self._cache is not None:
            return self._cache
        raw = self._read_raw()
        if raw is None:
            # First-time or corrupt file: seed from defaults.
            self._cache = self._seed()
        else:
            # Merge in any *new* default keys that have been added since
            # the file was last written. This means an upgraded Hermes
            # automatically surfaces new toggles without forcing the
            # user to "reset settings".
            self._cache = _shallow_merge_nested(self._seed(), raw)
        self._last_loaded = time.time()
        return self._cache

    # ---- public API -------------------------------------------------------

    def get(self) -> dict:
        """Return a deep copy of the current settings.

        If no file exists yet (or it was unreadable), the store seeds
        itself from ``DEFAULT_SETTINGS`` but does NOT persist until the
        next ``update()``/``reset()``. This keeps the read path
        idempotent and free of side-effects.
        """
        return _deep_copy_settings(self._load())

    async def update(self, partial: dict) -> dict:
        """Merge ``partial`` into the current settings, persist, and
        return the new state. Returns a deep copy.

        Concurrency: a single ``asyncio.Lock`` serialises writes. The
        read-modify-write is fully inside the lock so concurrent updates
        can't lose data.
        """
        if not isinstance(partial, dict):
            raise TypeError(f"update() expects a dict, got {type(partial).__name__}")
        async with self._lock:
            current = self._load()
            new_state = _shallow_merge_nested(current, partial)
            self._atomic_write(new_state)
            self._cache = new_state
            self._last_loaded = time.time()
            logger.debug(
                f"WebUISettingsStore: updated {self._path} "
                f"(+{len(partial)} top-level keys)"
            )
            return _deep_copy_settings(new_state)

    async def reset(self) -> dict:
        """Drop the on-disk file, re-seed from ``DEFAULT_SETTINGS``,
        persist the baseline, and return it.
        """
        async with self._lock:
            self._cache = self._seed()
            self._atomic_write(self._cache)
            self._last_loaded = time.time()
            logger.info(f"WebUISettingsStore: reset {self._path} to defaults")
            return _deep_copy_settings(self._cache)

    def stats(self) -> dict:
        """Diagnostic info: file path, size, last-load timestamp."""
        size = 0
        mtime = 0.0
        try:
            if self._path.is_file():
                st = self._path.stat()
                size = st.st_size
                mtime = st.st_mtime
        except OSError:
            pass
        return {
            "path": str(self._path),
            "exists": self._path.is_file(),
            "size_bytes": size,
            "last_loaded_at": self._last_loaded,
            "file_mtime": mtime,
        }


# ---- process-wide singleton -------------------------------------------------

_singleton: WebUISettingsStore | None = None
_singleton_key: tuple | None = None


def get_settings_store(base_dir: str | os.PathLike[str]) -> WebUISettingsStore:
    """Return the process-wide :class:`WebUISettingsStore` for
    ``base_dir``, creating it on first call.

    Re-callers with a *different* ``base_dir`` get a fresh instance
    (useful for tests). Re-callers with the same ``base_dir`` get the
    cached singleton.
    """
    global _singleton, _singleton_key
    key = (str(Path(base_dir).resolve()),)
    if _singleton is None or _singleton_key != key:
        _singleton = WebUISettingsStore(base_dir)
        _singleton_key = key
    return _singleton
