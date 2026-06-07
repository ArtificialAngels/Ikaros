"""
SessionStore: persistent chat-session store backed by JSON files.

Replaces the previous in-memory `agent._chat_sessions` dict. Each session
is a single file at ``<data_dir>/sessions/<id>.json``; reads are cheap
(used for /api/chat/sessions listing) and writes go through an
``asyncio.Lock`` to keep concurrent appends consistent.

File schema (per session):
    {
      "id": str,
      "title": str,
      "created_at": float,        # epoch seconds
      "updated_at": float,
      "model": str,
      "profile": str,
      "messages": [
        {
          "role": "user" | "assistant" | "system",
          "content": str,
          "timestamp": int,         # ms epoch
          "model": str | None,
          "provider": str | None,
          "tokens": int | None,
          "stream_id": str | None,
        },
        ...
      ],
    }

Public API:
    - ``SessionStore(path)``        -> construct (does not touch disk)
    - ``.get(sid) -> dict | None``  -> load one session
    - ``.list() -> list[dict]``     -> summary list (no full messages)
    - ``.save(sid, data) -> None``  -> atomic write (tempfile + os.replace)
    - ``.delete(sid) -> bool``      -> remove file
    - ``.create(initial_msg, ...)`` -> new session with first user turn
    - ``.patch_message(sid, idx, **fields)`` -> in-place mutation of one message

The store is intentionally tiny: no external deps, no schema migration,
no indexing. For 100k sessions on a USB stick it's still fast enough —
``list()`` reads only the first KB of each file.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes.sessions")

# Files whose names are not valid session ids (defense in depth)
_INVALID_FN_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_id(s: str) -> str:
    """Sanitize a session id to a safe filename component."""
    s = (s or "").strip()
    if not s:
        s = "sess_" + uuid.uuid4().hex[:12]
    s = _INVALID_FN_CHARS.sub("_", s)
    return s[:128]


def _now() -> float:
    return time.time()


def _now_ms() -> int:
    return int(time.time() * 1000)


class SessionStore:
    """File-backed chat-session store.

    Reads are unlocked (assume OS-level read consistency on a single host);
    writes are serialized per-store via an ``asyncio.Lock`` so a
    mid-stream delta-patch and a final ``save()`` cannot interleave.
    """

    def __init__(self, base_dir: str | os.PathLike[str]):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    # ---- low-level IO -----------------------------------------------------

    def _path(self, sid: str) -> Path:
        return self.base_dir / f"{_safe_id(sid)}.json"

    def _read(self, sid: str) -> dict | None:
        p = self._path(sid)
        if not p.is_file():
            return None
        try:
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"SessionStore: failed to read {p}: {e}")
            return None

    def _atomic_write(self, sid: str, data: dict) -> None:
        p = self._path(sid)
        tmp = p.with_suffix(p.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        except OSError as e:
            logger.error(f"SessionStore: failed to write {p}: {e}")
            raise

    # ---- public API -------------------------------------------------------

    def get(self, sid: str) -> dict | None:
        """Return full session dict, or None if not found."""
        if not sid:
            return None
        return self._read(sid)

    def list(self, limit: int = 200, offset: int = 0) -> list[dict]:
        """Return a summary list (no full message bodies) ordered by
        updated_at descending. Each entry is a lightweight dict safe to
        surface through the API.

        Reading is unlocked — we accept that two simultaneous writes might
        show up slightly out of order in the list (they're timestamped
        by ``updated_at`` so the next call will be consistent).
        """
        out: list[dict] = []
        try:
            for p in self.base_dir.glob("*.json"):
                # Skip tmp files from atomic write
                if p.name.endswith(".json.tmp"):
                    continue
                try:
                    with p.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, json.JSONDecodeError) as e:
                    logger.debug(f"Skipping unreadable session file {p}: {e}")
                    continue
                msgs = data.get("messages") or []
                last = msgs[-1] if msgs else {}
                out.append({
                    "id": data.get("id", p.stem),
                    "title": data.get("title") or (last.get("content", "")[:60] if last else "Chat"),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                    "message_count": len(msgs),
                    "last_message": (last.get("content", "")[:120] if last else ""),
                    "last_role": last.get("role", ""),
                    "model": data.get("model", ""),
                    "profile": data.get("profile", ""),
                    "archived": data.get("archived", False),
                    "pinned": data.get("pinned", False),
                })
        except OSError as e:
            logger.error(f"SessionStore.list: glob failed: {e}")
        # Sort newest-first; break ties by id for stable order
        out.sort(key=lambda d: (d.get("updated_at", 0), d.get("id", "")), reverse=True)
        return out[offset:offset + limit]

    async def save(self, sid: str, data: dict) -> None:
        """Persist a full session. Acquires the write lock."""
        async with self._lock:
            data["id"] = sid
            data["updated_at"] = _now()
            data.setdefault("created_at", data["updated_at"])
            data.setdefault("title", "Chat")
            data.setdefault("model", "")
            data.setdefault("profile", "default")
            data.setdefault("messages", [])
            self._atomic_write(sid, data)

    async def delete(self, sid: str) -> bool:
        """Remove a session file. Returns True if something was removed."""
        async with self._lock:
            p = self._path(sid)
            if not p.is_file():
                return False
            try:
                p.unlink()
                return True
            except OSError as e:
                logger.error(f"SessionStore.delete: failed to unlink {p}: {e}")
                return False

    # ---- higher-level helpers --------------------------------------------

    def create(self, initial_msg: str, *, model: str = "", profile: str = "default",
               session_id: str | None = None) -> dict:
        """Build a fresh session dict and persist it. Returns the dict.

        This is sync because it's invoked from request handlers that
        already have the lock; concurrency is handled by the caller.
        """
        sid = session_id or ("sess_" + uuid.uuid4().hex[:12])
        now = _now()
        title = (initial_msg or "New chat")[:60].replace("\n", " ").strip() or "New chat"
        data = {
            "id": sid,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "model": model,
            "profile": profile,
            "archived": False,
            "pinned": False,
            "messages": [
                {
                    "role": "user",
                    "content": initial_msg,
                    "timestamp": _now_ms(),
                    "model": model or None,
                    "provider": None,
                }
            ],
        }
        try:
            self._atomic_write(sid, data)
        except OSError as e:
            logger.warning(f"SessionStore.create: write failed ({e}); returning in-memory only")
        return data

    async def patch_message(self, sid: str, idx: int, **fields: Any) -> dict | None:
        """In-place update of one message inside a session.

        Used by the SSE streaming pipeline to grow the assistant message
        as tokens arrive: ``patch_message(sid, -1, content=...)``.
        """
        async with self._lock:
            data = self._read(sid)
            if not data or "messages" not in data or not data["messages"]:
                return None
            # Support negative indexing for "the last message"
            if idx < 0:
                idx = len(data["messages"]) + idx
            if idx < 0 or idx >= len(data["messages"]):
                return None
            msg = data["messages"][idx]
            for k, v in fields.items():
                if k == "append_content" and isinstance(v, str) and isinstance(msg.get("content"), str):
                    msg["content"] = msg["content"] + v
                else:
                    msg[k] = v
            data["updated_at"] = _now()
            # Auto-derive a better title from the first user turn if we
            # still have the placeholder
            if data.get("title") in (None, "", "New chat", "Chat"):
                first_user = next(
                    (m for m in data["messages"] if m.get("role") == "user"),
                    None,
                )
                if first_user and first_user.get("content"):
                    data["title"] = first_user["content"][:60].replace("\n", " ").strip()
            self._atomic_write(sid, data)
            return data

    async def append_message(self, sid: str, role: str, content: str,
                             **fields: Any) -> dict | None:
        """Append a new message to the session and persist."""
        async with self._lock:
            data = self._read(sid)
            if not data:
                # Auto-create a session if it does not exist yet (best-effort)
                data = {
                    "id": sid,
                    "title": (content or role)[:60].replace("\n", " ").strip() or "Chat",
                    "created_at": _now(),
                    "updated_at": _now(),
                    "model": "",
                    "profile": "default",
                    "archived": False,
                    "pinned": False,
                    "messages": [],
                }
            msg = {
                "role": role,
                "content": content,
                "timestamp": _now_ms(),
                **fields,
            }
            data["messages"].append(msg)
            data["updated_at"] = _now()
            if data.get("title") in (None, "", "New chat", "Chat"):
                if role == "user" and content:
                    data["title"] = content[:60].replace("\n", " ").strip()
            self._atomic_write(sid, data)
            return data

    # ---- debug -----------------------------------------------------------

    def stats(self) -> dict:
        try:
            files = list(self.base_dir.glob("*.json"))
            total = len(files)
            size = sum(p.stat().st_size for p in files)
        except OSError:
            total, size = 0, 0
        return {
            "base_dir": str(self.base_dir),
            "session_count": total,
            "total_bytes": size,
        }
