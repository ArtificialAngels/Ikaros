#!/usr/bin/env python
"""ikaros-hermes-mem-feed.py — Ingest Hermes Agent sessions into V5 memory.

Reads Hermes session files (*.json) from HERMES_HOME/sessions/, extracts
conversation turns that haven't been ingested yet, and stores them into
V5 v5.db as 'conversation' type entries.  The ReflectScheduler's consolidate
op will later convert these into facts/preferences/lessons.

Usage:
    python bin/ikaros-hermes-mem-feed.py [--watch N] [--dry-run]

    --watch N   Daemon mode: run every N seconds (default: no daemon).
    --dry-run   Scan + report only, no writes.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

# ─── Bootstrap paths ───
IKAROS_ROOT = Path(os.environ.get("IKAROS_ROOT",
                    Path(__file__).resolve().parent))
HERMES_HOME = Path(os.environ.get("HERMES_HOME",
                    IKAROS_ROOT / "data" / "hermes-agent"))
V5_ROOT = IKAROS_ROOT / "core" / "v5"
SESSIONS_DIR = HERMES_HOME / "sessions"
INGEST_MARKER = HERMES_HOME / ".hermes_mem_feed_cursor"

sys.path.insert(0, str(V5_ROOT.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [mem-feed] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ikaros.mem_feed")

# ─── Quality gate (same as cloud_chat._record_conversation) ───
_SKIP_PATTERNS = frozenset({
    "嗯", "哦", "好", "好的", "行", "ok", "是", "对",
    "继续", "然后", "谢谢", "感谢", "收到",
})


def _should_store(content: str) -> bool:
    """Quality gate: skip greetings, single chars, emoji-only."""
    c = (content or "").strip()
    if not c or len(c) < 6:
        return False
    if c.lower() in _SKIP_PATTERNS:
        return False
    return True


def _load_cursor() -> dict:
    """Load the ingestion cursor: maps session_id -> last ingested turn index."""
    if INGEST_MARKER.is_file():
        try:
            return json.loads(INGEST_MARKER.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cursor(cursor: dict) -> None:
    """Save ingestion cursor atomically."""
    INGEST_MARKER.parent.mkdir(parents=True, exist_ok=True)
    tmp = INGEST_MARKER.with_suffix(".tmp")
    tmp.write_text(json.dumps(cursor, ensure_ascii=False), encoding="utf-8")
    tmp.replace(INGEST_MARKER)


def _read_session(session_path: Path) -> list[dict]:
    """Read a Hermes session file and return list of message turns.

    Hermes session JSON format observed:
      {"session_id": "...", "messages": [{"role": "user"/"assistant", "content": "..."}, ...]}
    
    Also handles the newer XDG-style format:
      {"id": "...", "title": "...", "conversations": [{"role": "user", "content": "..."}, ...]}
    """
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Cannot parse session %s: %s", session_path.name, e)
        return []

    # Try newer format first (id + conversations)
    msgs = data.get("conversations") or data.get("messages") or data.get("history") or []
    if isinstance(msgs, list) and msgs:
        return msgs

    # Try flat array
    if isinstance(data, list):
        return data

    # Try Hermes Agent's own session schema: {"sessions": [{...}]}
    sessions = data.get("sessions") or []
    if sessions:
        return sessions[0].get("messages", []) if isinstance(sessions[0], dict) else []

    return []


def ingest_once(*, dry_run: bool = False) -> dict:
    """Process all uningested Hermes sessions.

    Returns:
        dict: {ingested: int, sessions_scanned: int, sessions_new: int, errors: int}
    """
    from memory_v5 import store as v5_store

    cursor = _load_cursor()
    ingested = 0
    errors = 0
    sessions_new = 0
    sessions_scanned = 0

    if not SESSIONS_DIR.is_dir():
        logger.info("No sessions dir at %s", SESSIONS_DIR)
        return {"ingested": 0, "sessions_scanned": 0, "sessions_new": 0, "errors": 0}

    session_files = sorted(SESSIONS_DIR.glob("*.json"))
    for session_path in session_files:
        sid = session_path.stem  # filename without .json = session_id
        sessions_scanned += 1
        last_idx = cursor.get(sid, -1)

        msgs = _read_session(session_path)
        if not msgs:
            continue

        # Find new turns
        new = []
        for i, msg in enumerate(msgs):
            role = (msg.get("role") or "").lower()
            content = (msg.get("content") or "").strip()
            if i <= last_idx:
                continue
            if role not in ("user", "assistant"):
                continue
            if not _should_store(content):
                continue
            new.append(msg)

        if not new:
            continue

        sessions_new += 1

        if dry_run:
            logger.info("[DRY-RUN] %s: %d new turns (would store %d pairs)",
                        sid, len(new), len(new) // 2)
            continue

        # Store each user+assistant pair as a conversation
        i = 0
        stored_pairs = 0
        while i < len(new) - 1:
            user_msg = ""
            assistant_msg = ""

            # Find the next user-role message
            if new[i]["role"] == "user":
                user_msg = new[i]["content"]
                # Pair with next assistant message
                for j in range(i + 1, len(new)):
                    if new[j]["role"] == "assistant":
                        assistant_msg = new[j]["content"]
                        i = j + 1
                        break
                else:
                    i += 1
                    continue
            else:
                i += 1
                continue

            if not _should_store(user_msg):
                continue

            content = f"Q: {user_msg[:200].strip()}\nA: {assistant_msg[:150].strip()}" if assistant_msg else f"Q: {user_msg[:200].strip()}"
            try:
                v5_store.store(
                    content=content,
                    type="conversation",
                    weight=0.5,
                    tags="hermes_session",
                )
                stored_pairs += 1
                ingested += 1
            except Exception as e:
                logger.warning("store failed: %s", e)
                errors += 1

        if stored_pairs:
            # Update cursor to last processed turn index
            last_seen = -1
            for i, msg in enumerate(msgs):
                if msg.get("role") == "user" or msg.get("role") == "assistant":
                    last_seen = i
            cursor[sid] = last_seen
            logger.info("%s: ingested %d conversation pairs (%d turns new)",
                        sid, stored_pairs, len(new))

    if not dry_run:
        _save_cursor(cursor)

    total = ingested
    logger.info("Done: %d sessions scanned, %d new, %d conversations ingested, %d errors",
                sessions_scanned, sessions_new, total, errors)
    return {
        "ingested": total,
        "sessions_scanned": sessions_scanned,
        "sessions_new": sessions_new,
        "errors": errors,
    }


def watch(interval: int) -> None:
    """Daemon mode: ingest every N seconds."""
    logger.info("Starting Hermes memory feed daemon (interval=%ds)", interval)
    while True:
        try:
            ingest_once()
        except Exception as e:
            logger.error("Feed cycle failed: %s", e)
        time.sleep(interval)


def main() -> None:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args

    if "--watch" in args:
        idx = args.index("--watch")
        interval = int(args[idx + 1]) if idx + 1 < len(args) else 120
        logger.info("Daemon mode: --watch %d %s", interval,
                    "(dry-run)" if dry_run else "")
        while True:
            try:
                ingest_once(dry_run=dry_run)
            except Exception as e:
                logger.error("Feed cycle failed: %s", e)
            time.sleep(interval)
    else:
        result = ingest_once(dry_run=dry_run)
        if dry_run:
            print(f"[DRY-RUN] Would ingest: {result['ingested']} conversations "
                  f"from {result['sessions_new']} new sessions")
        else:
            print(f"Ingested: {result['ingested']} conversations "
                  f"({result['sessions_new']}/{result['sessions_scanned']} new sessions, "
                  f"{result['errors']} errors)")


if __name__ == "__main__":
    main()
