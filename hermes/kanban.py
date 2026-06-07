"""
Kanban: lightweight file-backed board + task store.

This is the MVP module the new Hermes WebUI needs to render its Kanban panel.
Scope: board + task CRUD, status transitions, block/unblock, bulk status
updates, board switcher, default board bootstrap, and read-only aggregates
(stats / assignees / config / events).

Out of scope (intentionally not implemented in this MVP):
  * Real-time SSE push — ``/api/kanban/events/stream`` returns a noop payload;
    the UI falls back to 30s polling via ``/api/kanban/events``.
  * Inter-task dependency graph.
  * Comments — ``/api/kanban/tasks/{id}/comments`` is a noop.
  * Worktree operations — ``/api/kanban/tasks/{id}/worktree/...`` noop.
  * Dispatcher — ``/api/kanban/dispatch`` returns ``{dispatched: []}``.

Persistence model
-----------------
All kanban state lives under ``<data_dir>/kanban/``:

    kanban/
        boards.json         # list[board] — one file, atomic rewrite
        tasks.json          # list[task]  — one file, atomic rewrite
        events.json         # list[event] — capped (newest N retained)
        active_board        # str         — slug of currently active board

Each file is rewritten with ``tempfile + os.replace`` so a half-written file
can never be observed by readers. Writes go through an ``asyncio.Lock`` so
two concurrent endpoint handlers don't tear the file.

Field schemas
-------------
Board (``board_id`` is the canonical field name; ``slug`` is accepted as
an alias because the new WebUI speaks both):
    {
      "board_id":   str,           # canonical: lowercase slug
      "slug":       str,           # alias of board_id (WebUI's term)
      "name":       str,
      "description": str,          # optional
      "icon":       str,           # optional emoji
      "color":      str,           # optional CSS color
      "columns":    list[str],     # ordered column names (kanban "statuses")
      "created_at": float,         # epoch seconds
      "updated_at": float,
      "archived":   bool,          # soft-delete flag
      "task_count": int,           # cached for /api/kanban/boards list
    }

Task:
    {
      "task_id":        str,        # canonical
      "id":             str,        # alias of task_id
      "board_id":       str,        # board slug
      "title":          str,
      "body":           str,        # markdown description
      "status":         str,        # one of board.columns
      "assignee":       str | None, # profile name or None
      "tenant":         str | None, # project / team slug
      "priority":       int,        # -100..100, default 0
      "tags":           list[str],
      "blocked":        bool,
      "blocked_reason": str | None,
      "due_at":         float | None,
      "created_at":     float,
      "updated_at":     float,
    }

Event (read-only feed for the UI's polling refresh):
    {
      "event_id":  int,            # monotonically increasing per board
      "board_id":  str,
      "task_id":   str | None,
      "kind":      str,            # "created" | "updated" | "status_changed" |
                                   # "blocked" | "unblocked" | "deleted" |
                                   # "board_created" | "board_renamed" |
                                   # "board_archived"
      "summary":   str,            # human-readable one-liner
      "at":        float,
    }
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
from typing import Any, Iterable

logger = logging.getLogger("hermes.kanban")

# ---- Constants --------------------------------------------------------------

# Default column set the UI renders as "Todo / Doing / Done". The new WebUI
# also supports richer flows (triage / ready / blocked / archived), but the
# MVP is intentionally narrow — the UI handles missing columns gracefully
# (it falls back to config.columns) and users can rename them later.
DEFAULT_COLUMNS = ["todo", "doing", "done"]

# The default board (idempotent — created on first run, never deleted).
DEFAULT_BOARD_ID = "default"
DEFAULT_BOARD_NAME = "Default"
DEFAULT_BOARD_DESCRIPTION = "Your first board — rename it from the switcher in the top-right."
DEFAULT_BOARD_ICON = ""
DEFAULT_BOARD_COLOR = "#7aa2ff"

# Slug sanitation: lowercase, hyphens, alphanum. Anything else → "_".
_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_SLUG_DASH_RE = re.compile(r"-+")
_RESERVED_SLUGS = {"_archived", "_deleted", "_lost+found"}

# Cap on the events.json log to keep file size bounded on long-running installs.
MAX_EVENTS = 2000


# ---- Helpers ----------------------------------------------------------------

def _now() -> float:
    return time.time()


def _slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = _SLUG_RE.sub("-", s)
    s = _SLUG_DASH_RE.sub("-", s).strip("-")
    return s[:48]


def _safe_slug(s: str) -> str:
    """Coerce any string into a safe board slug (or empty if none possible)."""
    s = _slugify(s)
    if not s or s in _RESERVED_SLUGS:
        return ""
    return s


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---- Store ------------------------------------------------------------------

class KanbanStore:
    """File-backed kanban store. See module docstring for the schema.

    Reads are unlocked; writes are serialized via an ``asyncio.Lock`` so
    concurrent endpoint handlers never tear a file mid-rewrite. Each file
    is rewritten atomically with ``tempfile + os.replace``.
    """

    def __init__(self, base_dir: str | os.PathLike[str]):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._boards_path = self.base_dir / "boards.json"
        self._tasks_path = self.base_dir / "tasks.json"
        self._events_path = self.base_dir / "events.json"
        self._active_path = self.base_dir / "active_board"
        self._lock = asyncio.Lock()
        # Event id cursor — monotonically increasing across the lifetime of
        # this process. Not persisted; the new WebUI only uses it for
        # "since=X" polling, and a restart that resets to 0 just causes a
        # one-time re-render.
        self._event_counter = 0
        # Track the loaded "max event id seen" so newly-loaded historical
        # events can't accidentally *lower* the cursor.
        self._max_seen_event_id = 0
        # Bootstrap a default board + sample tasks on first run.
        self.bootstrap()

    # ---- low-level IO ------------------------------------------------------

    def _read_json(self, path: Path, default):
        if not path.is_file():
            return default
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"KanbanStore: failed to read {path}: {e}")
            return default

    def _write_json_atomic(self, path: Path, data) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError as e:
            logger.error(f"KanbanStore: failed to write {path}: {e}")
            raise

    def _read_text(self, path: Path) -> str:
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _write_text(self, path: Path, text: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except OSError as e:
            logger.error(f"KanbanStore: failed to write {path}: {e}")

    def _load_boards(self) -> list[dict]:
        data = self._read_json(self._boards_path, [])
        return data if isinstance(data, list) else []

    def _load_tasks(self) -> list[dict]:
        data = self._read_json(self._tasks_path, [])
        return data if isinstance(data, list) else []

    def _load_events(self) -> list[dict]:
        data = self._read_json(self._events_path, [])
        return data if isinstance(data, list) else []

    def _load_active(self) -> str:
        return self._read_text(self._active_path) or DEFAULT_BOARD_ID

    # ---- mutation helpers (sync, must be called under the lock) -----------

    def _save_boards_unlocked(self, boards: list[dict]) -> None:
        self._write_json_atomic(self._boards_path, boards)

    def _save_tasks_unlocked(self, tasks: list[dict]) -> None:
        self._write_json_atomic(self._tasks_path, tasks)

    def _save_events_unlocked(self, events: list[dict]) -> None:
        # Cap the events log so the file doesn't grow unbounded.
        if len(events) > MAX_EVENTS:
            events = events[-MAX_EVENTS:]
        self._write_json_atomic(self._events_path, events)

    def _append_event_unlocked(self, board_id: str, kind: str,
                               summary: str, task_id: str | None) -> int:
        # Allocate the next event id monotonically, even if we just loaded
        # historical events from disk.
        existing = self._load_events()
        max_loaded = max((e.get("event_id", 0) for e in existing), default=0)
        self._max_seen_event_id = max(self._max_seen_event_id, max_loaded)
        self._event_counter = max(self._event_counter, self._max_seen_event_id)
        self._event_counter += 1
        event = {
            "event_id": self._event_counter,
            "board_id": board_id,
            "task_id": task_id,
            "kind": kind,
            "summary": summary,
            "at": _now(),
        }
        existing.append(event)
        if len(existing) > MAX_EVENTS:
            existing = existing[-MAX_EVENTS:]
        self._write_json_atomic(self._events_path, existing)
        return event["event_id"]

    # ---- bootstrap ---------------------------------------------------------

    def bootstrap(self) -> None:
        """Create the default board + sample tasks on first run. Idempotent."""
        boards = self._load_boards()
        if any(b.get("board_id") == DEFAULT_BOARD_ID for b in boards):
            return
        now = _now()
        boards.append({
            "board_id": DEFAULT_BOARD_ID,
            "slug": DEFAULT_BOARD_ID,
            "name": DEFAULT_BOARD_NAME,
            "description": DEFAULT_BOARD_DESCRIPTION,
            "icon": DEFAULT_BOARD_ICON,
            "color": DEFAULT_BOARD_COLOR,
            "columns": list(DEFAULT_COLUMNS),
            "created_at": now,
            "updated_at": now,
            "archived": False,
            "task_count": 0,
        })
        self._save_boards_unlocked(boards)
        # Ensure the active-board pointer is valid.
        if not self._load_active():
            self._write_text(self._active_path, DEFAULT_BOARD_ID)
        # Seed a few example tasks so the panel isn't blank on first launch.
        existing_tasks = self._load_tasks()
        if not existing_tasks:
            samples = [
                ("Explore Hermes features", "Welcome to your kanban! Click a task to see its detail panel, or hit + to create more.\n\n- **Drag tasks** between columns to change status\n- **Click a card** to open the detail view\n- **Edit** from the panel-head to change assignee / tenant / priority", "doing", "default"),
                ("Add real embeddings", "Replace the hash-based pseudo-embedder with sentence-transformers + a small embedding model so the KB semantic search actually finds related chunks.", "todo", "default"),
                ("Wire up cron scheduler", "Add a /api/crons endpoint group and a small asyncio scheduler that runs user-defined jobs at the requested cadence.", "todo", "default"),
                ("Polish dashboard", "Make /api/dashboard reflect live counts (sessions, kanban tasks, recent logs) instead of zeros.", "doing", "default"),
                ("Ship MVP", "When the kanban, crons, settings, and embeddings all work end-to-end, tag a release.", "done", "default"),
            ]
            for title, body, status, assignee in samples:
                self._create_task_unlocked(
                    board_id=DEFAULT_BOARD_ID,
                    title=title,
                    body=body,
                    status=status,
                    assignee=assignee,
                    tenant=None,
                    priority=0,
                    tags=[],
                    due_at=None,
                    emit_event=False,  # silent bootstrap; don't spam the feed
                )
        logger.info(f"KanbanStore: bootstrapped default board at {self.base_dir}")

    # ---- board CRUD --------------------------------------------------------

    async def list_boards(self, *, include_archived: bool = False) -> list[dict]:
        """Return all non-archived boards (plus task counts)."""
        boards = self._load_boards()
        tasks = self._load_tasks()
        counts: dict[str, dict[str, int]] = {}
        for t in tasks:
            bid = t.get("board_id", DEFAULT_BOARD_ID)
            status = t.get("status") or ""
            counts.setdefault(bid, {}).setdefault(status, 0)
            counts[bid][status] += 1
        out = []
        for b in boards:
            if b.get("archived") and not include_archived:
                continue
            bid = b.get("board_id") or b.get("slug")
            b_counts = counts.get(bid, {})
            b["board_id"] = bid
            b["slug"] = bid
            b["task_count"] = sum(b_counts.values())
            b["counts"] = b_counts
            out.append(b)
        # Newest first, default pinned at top.
        out.sort(key=lambda d: (
            0 if d.get("board_id") == DEFAULT_BOARD_ID else 1,
            -(d.get("updated_at", 0) or 0),
        ))
        return out

    async def get_board(self, board_id: str) -> dict | None:
        board_id = (board_id or "").strip() or DEFAULT_BOARD_ID
        boards = self._load_boards()
        for b in boards:
            if b.get("board_id") == board_id or b.get("slug") == board_id:
                if b.get("archived"):
                    return None
                return b
        return None

    async def create_board(self, name: str, *, color: str = "",
                           description: str = "", icon: str = "",
                           slug: str = "", **_: Any) -> dict:
        """Create a new board. Slug auto-derived from name unless given."""
        name = (name or "").strip() or "Untitled"
        async with self._lock:
            boards = self._load_boards()
            target_slug = _safe_slug(slug) or _safe_slug(name) or _new_id("b")
            if not target_slug:
                target_slug = _new_id("b")
            # Dedupe: if the slug collides, append a short suffix.
            existing_slugs = {b.get("board_id") for b in boards}
            base = target_slug
            i = 2
            while target_slug in existing_slugs:
                target_slug = f"{base}-{i}"
                i += 1
            now = _now()
            board = {
                "board_id": target_slug,
                "slug": target_slug,
                "name": name[:64],
                "description": (description or "")[:200],
                "icon": (icon or "")[:4],
                "color": _sanitize_color(color) or DEFAULT_BOARD_COLOR,
                "columns": list(DEFAULT_COLUMNS),
                "created_at": now,
                "updated_at": now,
                "archived": False,
                "task_count": 0,
            }
            boards.append(board)
            self._save_boards_unlocked(boards)
            self._append_event_unlocked(
                target_slug, "board_created",
                f"Board '{name}' created", task_id=None,
            )
        # Make the new board active so the UI immediately renders it.
        await self.set_active(target_slug)
        return board

    async def update_board(self, board_id: str, **fields: Any) -> dict | None:
        board_id = (board_id or "").strip()
        if not board_id:
            return None
        async with self._lock:
            boards = self._load_boards()
            for b in boards:
                if b.get("board_id") == board_id or b.get("slug") == board_id:
                    renamed = False
                    if "name" in fields and fields["name"]:
                        new_name = str(fields["name"]).strip()[:64]
                        if new_name and new_name != b.get("name"):
                            b["name"] = new_name
                            renamed = True
                    if "description" in fields:
                        b["description"] = str(fields.get("description") or "")[:200]
                    if "icon" in fields:
                        b["icon"] = str(fields.get("icon") or "")[:4]
                    if "color" in fields:
                        b["color"] = _sanitize_color(fields.get("color")) or b.get("color") or DEFAULT_BOARD_COLOR
                    if "columns" in fields and isinstance(fields["columns"], list) and fields["columns"]:
                        cols = [str(c).strip() for c in fields["columns"] if str(c).strip()]
                        if cols:
                            b["columns"] = cols[:16]
                    b["updated_at"] = _now()
                    self._save_boards_unlocked(boards)
                    if renamed:
                        self._append_event_unlocked(
                            board_id, "board_renamed",
                            f"Board renamed to '{b['name']}'", task_id=None,
                        )
                    return b
        return None

    async def delete_board(self, board_id: str) -> bool:
        """Soft-archive a board (and its tasks) so it disappears from
        ``list_boards()`` but the data is preserved on disk. Switching the
        active board back to ``default`` is the caller's responsibility —
        we do it here for convenience.
        """
        board_id = (board_id or "").strip()
        if not board_id or board_id == DEFAULT_BOARD_ID:
            return False  # default board is never deletable in this MVP
        async with self._lock:
            boards = self._load_boards()
            target = None
            for b in boards:
                if b.get("board_id") == board_id or b.get("slug") == board_id:
                    target = b
                    break
            if not target or target.get("archived"):
                return False
            target["archived"] = True
            target["updated_at"] = _now()
            self._save_boards_unlocked(boards)
            self._append_event_unlocked(
                board_id, "board_archived",
                f"Board '{target.get('name', board_id)}' archived", task_id=None,
            )
        # Fall back to the default board so subsequent requests don't dangle.
        active = self._load_active()
        if active == board_id:
            await self.set_active(DEFAULT_BOARD_ID)
        return True

    async def set_active(self, board_id: str) -> dict | None:
        board_id = (board_id or "").strip() or DEFAULT_BOARD_ID
        async with self._lock:
            boards = self._load_boards()
            valid = any(
                b.get("board_id") == board_id and not b.get("archived")
                for b in boards
            )
            if not valid:
                # Fall back to default if the requested one doesn't exist.
                board_id = DEFAULT_BOARD_ID
            self._write_text(self._active_path, board_id)
        return await self.get_board(board_id)

    async def get_active(self) -> dict | None:
        active = self._load_active()
        return await self.get_board(active)

    # ---- task CRUD ---------------------------------------------------------

    async def list_tasks(self, board_id: str = "",
                         *, status: str = None, assignee: str = None,
                         tenant: str = None,
                         include_archived: bool = False) -> list[dict]:
        board_id = (board_id or "").strip()
        if not board_id:
            active = await self.get_active()
            board_id = (active or {}).get("board_id") or DEFAULT_BOARD_ID
        tasks = [t for t in self._load_tasks() if t.get("board_id") == board_id]
        if status:
            tasks = [t for t in tasks if t.get("status") == status]
        if assignee:
            tasks = [t for t in tasks if (t.get("assignee") or "") == assignee]
        if tenant:
            tasks = [t for t in tasks if (t.get("tenant") or "") == tenant]
        if not include_archived:
            tasks = [t for t in tasks if not t.get("archived")]
        tasks.sort(key=lambda d: (
            d.get("status") or "",
            -(d.get("priority", 0) or 0),
            -(d.get("updated_at", 0) or 0),
        ))
        return tasks

    async def get_task(self, task_id: str) -> dict | None:
        task_id = (task_id or "").strip()
        if not task_id:
            return None
        for t in self._load_tasks():
            if t.get("task_id") == task_id or t.get("id") == task_id:
                return t
        return None

    async def create_task(self, board_id: str = "", title: str = "",
                          body: str = "", status: str | None = None,
                          assignee: str | None = None,
                          tenant: str | None = None,
                          priority: int = 0,
                          tags: Iterable[str] = (),
                          due_at: float | None = None,
                          **_: Any) -> dict:
        return await self._create_task_async(
            board_id=board_id, title=title, body=body, status=status,
            assignee=assignee, tenant=tenant, priority=priority,
            tags=list(tags or []), due_at=due_at, emit_event=True,
        )

    async def _create_task_async(self, *, board_id: str, title: str,
                                 body: str, status: str | None,
                                 assignee: str | None,
                                 tenant: str | None,
                                 priority: int,
                                 tags: list[str],
                                 due_at: float | None,
                                 emit_event: bool) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("title is required")
        async with self._lock:
            return self._create_task_unlocked(
                board_id=board_id or "",
                title=title,
                body=body or "",
                status=status,
                assignee=assignee,
                tenant=tenant,
                priority=priority,
                tags=tags,
                due_at=due_at,
                emit_event=emit_event,
            )

    def _create_task_unlocked(self, *, board_id: str, title: str,
                              body: str, status: str | None,
                              assignee: str | None,
                              tenant: str | None,
                              priority: int,
                              tags: list[str],
                              due_at: float | None,
                              emit_event: bool) -> dict:
        # Resolve board (default to active / default if empty).
        boards = self._load_boards()
        if not board_id:
            active = self._load_active()
            board_id = active or DEFAULT_BOARD_ID
        # Validate board exists & isn't archived.
        board = None
        for b in boards:
            if b.get("board_id") == board_id or b.get("slug") == board_id:
                board = b
                break
        if not board or board.get("archived"):
            board_id = DEFAULT_BOARD_ID
            for b in boards:
                if b.get("board_id") == DEFAULT_BOARD_ID:
                    board = b
                    break
        # Default status: first column of the board.
        cols = board.get("columns") if board else None
        if not cols:
            cols = list(DEFAULT_COLUMNS)
        if not status or status not in cols:
            status = cols[0]
        now = _now()
        task = {
            "task_id": _new_id("t"),
            "id": "",  # filled in below
            "board_id": board_id,
            "title": title[:500],
            "body": body or "",
            "status": status,
            "assignee": (assignee or None) and str(assignee)[:64] or None,
            "tenant": (tenant or None) and str(tenant)[:64] or None,
            "priority": int(priority) if priority is not None else 0,
            "tags": [str(t)[:32] for t in (tags or [])][:16],
            "blocked": False,
            "blocked_reason": None,
            "due_at": float(due_at) if due_at else None,
            "created_at": now,
            "updated_at": now,
            "archived": False,
        }
        task["id"] = task["task_id"]
        tasks = self._load_tasks()
        tasks.append(task)
        self._save_tasks_unlocked(tasks)
        if emit_event:
            self._append_event_unlocked(
                board_id, "created",
                f"Task '{task['title']}' created", task_id=task["task_id"],
            )
        return task

    async def update_task(self, task_id: str, **fields: Any) -> dict | None:
        task_id = (task_id or "").strip()
        if not task_id:
            return None
        async with self._lock:
            tasks = self._load_tasks()
            for idx, t in enumerate(tasks):
                if t.get("task_id") == task_id or t.get("id") == task_id:
                    old_status = t.get("status")
                    # Apply known fields. Unknown ones are ignored — keeps the
                    # server tolerant of forward-compatible client payloads.
                    if "title" in fields and fields["title"] is not None:
                        t["title"] = str(fields["title"]).strip()[:500] or t["title"]
                    if "body" in fields:
                        t["body"] = str(fields.get("body") or "")
                    if "status" in fields and fields["status"]:
                        new_status = str(fields["status"])
                        # Allow transition to any board column; fall back to
                        # old status if the requested one is unknown.
                        t["status"] = new_status or old_status
                    if "assignee" in fields:
                        t["assignee"] = (fields.get("assignee") or None)
                        if t["assignee"] is not None:
                            t["assignee"] = str(t["assignee"])[:64] or None
                    if "tenant" in fields:
                        t["tenant"] = (fields.get("tenant") or None)
                        if t["tenant"] is not None:
                            t["tenant"] = str(t["tenant"])[:64] or None
                    if "priority" in fields and fields["priority"] is not None:
                        try:
                            t["priority"] = max(-100, min(100, int(fields["priority"])))
                        except (TypeError, ValueError):
                            pass
                    if "tags" in fields:
                        t["tags"] = [str(x)[:32] for x in (fields.get("tags") or [])][:16]
                    if "due_at" in fields:
                        t["due_at"] = float(fields["due_at"]) if fields["due_at"] else None
                    if "archived" in fields:
                        t["archived"] = bool(fields["archived"])
                    # A status write to non-blocked always clears the block.
                    if "status" in fields and fields["status"] and fields["status"] != "blocked":
                        t["blocked"] = False
                        t["blocked_reason"] = None
                    t["updated_at"] = _now()
                    tasks[idx] = t
                    self._save_tasks_unlocked(tasks)
                    kind = "status_changed" if (
                        "status" in fields and fields["status"] and fields["status"] != old_status
                    ) else "updated"
                    self._append_event_unlocked(
                        t.get("board_id", ""), kind,
                        f"Task '{t['title']}' updated", task_id=t["task_id"],
                    )
                    return t
        return None

    async def delete_task(self, task_id: str) -> bool:
        task_id = (task_id or "").strip()
        if not task_id:
            return False
        async with self._lock:
            tasks = self._load_tasks()
            for idx, t in enumerate(tasks):
                if t.get("task_id") == task_id or t.get("id") == task_id:
                    removed = tasks.pop(idx)
                    self._save_tasks_unlocked(tasks)
                    self._append_event_unlocked(
                        removed.get("board_id", ""), "deleted",
                        f"Task '{removed.get('title', task_id)}' deleted",
                        task_id=None,
                    )
                    return True
        return False

    async def set_status(self, task_id: str, status: str) -> dict | None:
        return await self.update_task(task_id, status=status)

    async def block_task(self, task_id: str, reason: str = "") -> dict | None:
        task_id = (task_id or "").strip()
        if not task_id:
            return None
        async with self._lock:
            tasks = self._load_tasks()
            for idx, t in enumerate(tasks):
                if t.get("task_id") == task_id or t.get("id") == task_id:
                    t["blocked"] = True
                    t["blocked_reason"] = (reason or "blocked")[:500]
                    # Move to a blocked-ish column if the board has one; else
                    # leave status untouched.
                    board_id = t.get("board_id", "")
                    boards = self._load_boards()
                    cols = None
                    for b in boards:
                        if b.get("board_id") == board_id or b.get("slug") == board_id:
                            cols = b.get("columns")
                            break
                    if cols and "blocked" in cols:
                        t["status"] = "blocked"
                    t["updated_at"] = _now()
                    tasks[idx] = t
                    self._save_tasks_unlocked(tasks)
                    self._append_event_unlocked(
                        board_id, "blocked",
                        f"Task '{t['title']}' blocked: {t['blocked_reason']}",
                        task_id=t["task_id"],
                    )
                    return t
        return None

    async def unblock_task(self, task_id: str) -> dict | None:
        task_id = (task_id or "").strip()
        if not task_id:
            return None
        async with self._lock:
            tasks = self._load_tasks()
            for idx, t in enumerate(tasks):
                if t.get("task_id") == task_id or t.get("id") == task_id:
                    t["blocked"] = False
                    t["blocked_reason"] = None
                    t["updated_at"] = _now()
                    tasks[idx] = t
                    self._save_tasks_unlocked(tasks)
                    self._append_event_unlocked(
                        t.get("board_id", ""), "unblocked",
                        f"Task '{t['title']}' unblocked",
                        task_id=t["task_id"],
                    )
                    return t
        return None

    async def bulk_update(self, ids: list[str], **fields: Any) -> dict:
        """Apply the same patch to many tasks at once.

        Returns ``{ok: bool, updated: int, ids: [...], errors: [...]}`` so the
        client can show partial-failure toasts.
        """
        if not isinstance(ids, list):
            raise ValueError("ids must be a list")
        updated: list[str] = []
        errors: list[str] = []
        for tid in ids:
            t = await self.update_task(tid, **fields)
            if t:
                updated.append(t.get("task_id"))
            else:
                errors.append(tid)
        return {
            "ok": not errors,
            "updated": len(updated),
            "ids": updated,
            "errors": errors,
        }

    # ---- aggregates --------------------------------------------------------

    async def stats(self, board_id: str = "") -> dict:
        tasks = await self.list_tasks(board_id=board_id, include_archived=True)
        by_status: dict[str, int] = {}
        by_assignee: dict[str, int] = {}
        for t in tasks:
            s = t.get("status") or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
            a = t.get("assignee") or "unassigned"
            by_assignee[a] = by_assignee.get(a, 0) + 1
        return {
            "total_tasks": len(tasks),
            "by_status": by_status,
            "by_assignee": by_assignee,
        }

    async def list_assignees(self, board_id: str = "") -> list[str]:
        tasks = await self.list_tasks(board_id=board_id)
        seen: list[str] = []
        seen_set: set[str] = set()
        for t in tasks:
            a = t.get("assignee")
            if a and a not in seen_set:
                seen.append(a)
                seen_set.add(a)
        seen.sort()
        return seen

    async def list_tenants(self, board_id: str = "") -> list[str]:
        tasks = await self.list_tasks(board_id=board_id)
        seen: list[str] = []
        seen_set: set[str] = set()
        for t in tasks:
            tn = t.get("tenant")
            if tn and tn not in seen_set:
                seen.append(tn)
                seen_set.add(tn)
        seen.sort()
        return seen

    async def get_config(self, board_id: str = "") -> dict:
        """Return the column / default-tenant defaults for the given board.

        The UI uses this to populate the column headers and to seed the
        status dropdown in the new-task modal.
        """
        board = await self.get_board(board_id) if board_id else None
        if not board:
            board = await self.get_active()
        if not board:
            board = {"board_id": DEFAULT_BOARD_ID, "columns": list(DEFAULT_COLUMNS)}
        columns = list(board.get("columns") or DEFAULT_COLUMNS)
        # Common status set the new WebUI expects to find. We expose a few
        # more than the default columns so the status dropdown is fully
        # populated; missing entries are filtered out by the client.
        statuses = list(dict.fromkeys(
            columns + ["triage", "todo", "ready", "doing", "blocked", "done", "archived"],
        ))
        return {
            "board_id": board.get("board_id"),
            "columns": columns,
            "statuses": statuses,
            "default_status": columns[0] if columns else "todo",
            "assignees": await self.list_assignees(board.get("board_id")),
            "tenants": await self.list_tenants(board.get("board_id")),
        }

    # ---- events (read-only, polling-friendly) -----------------------------

    async def list_events(self, board_id: str = "", since: int = 0,
                          limit: int = 200) -> dict:
        events = self._load_events()
        if board_id:
            events = [e for e in events if e.get("board_id") == board_id]
        if since:
            events = [e for e in events if int(e.get("event_id", 0)) > int(since)]
        events = events[-max(1, min(int(limit), 1000)):]
        latest_id = max((e.get("event_id", 0) for e in events), default=since)
        return {
            "events": events,
            "latest_event_id": latest_id,
            "cursor": latest_id,
        }

    # ---- board view (board + tasks grouped by status) --------------------

    async def board_view(self, board_id: str = "", *,
                         assignee: str = "", tenant: str = "",
                         include_archived: bool = False) -> dict:
        """Return the bundle the new UI's ``/api/kanban/board`` expects:

            {
              "board_id": "default",
              "name":     "...",
              "columns":  [ {name, tasks: [...]}, ... ],
              "assignees": [...],
              "tenants":   [...],
              "read_only": False,
              "latest_event_id": <int>,
              "changed":  True,
            }
        """
        if not board_id:
            board = await self.get_active()
            board_id = (board or {}).get("board_id") or DEFAULT_BOARD_ID
        else:
            board = await self.get_board(board_id)
        if not board:
            board = await self.get_board(DEFAULT_BOARD_ID) or {
                "board_id": DEFAULT_BOARD_ID,
                "name": DEFAULT_BOARD_NAME,
                "columns": list(DEFAULT_COLUMNS),
            }
        columns_in_order = list(board.get("columns") or DEFAULT_COLUMNS)
        # Pull all tasks for the board (filtering happens client-side too).
        tasks = await self.list_tasks(
            board_id=board_id,
            assignee=assignee or None,
            tenant=tenant or None,
            include_archived=include_archived,
        )
        # Bucket tasks by status, preserving column order.
        buckets: dict[str, list[dict]] = {c: [] for c in columns_in_order}
        for t in tasks:
            status = t.get("status") or columns_in_order[0]
            if status not in buckets:
                # Unknown status (e.g. from old data) — create a transient
                # bucket so the card doesn't disappear.
                columns_in_order.append(status)
                buckets[status] = []
            buckets[status].append(t)
        # Compute latest event id for polling.
        events = self._load_events()
        latest_event_id = 0
        for e in events:
            if e.get("board_id") == board_id and int(e.get("event_id", 0)) > latest_event_id:
                latest_event_id = int(e["event_id"])
        return {
            "board_id": board.get("board_id"),
            "name": board.get("name", board.get("board_id")),
            "description": board.get("description", ""),
            "icon": board.get("icon", ""),
            "color": board.get("color", ""),
            "columns": [
                {"name": col, "tasks": buckets.get(col, [])}
                for col in columns_in_order
            ],
            "assignees": await self.list_assignees(board_id),
            "tenants": await self.list_tenants(board_id),
            "read_only": False,
            "latest_event_id": latest_event_id,
            "changed": True,
        }

    # ---- task detail view (with comments / events / links / runs) ---------

    async def task_view(self, task_id: str) -> dict | None:
        t = await self.get_task(task_id)
        if not t:
            return None
        # Re-emit the same task shape the UI expects in the detail panel.
        events = [
            e for e in self._load_events()
            if e.get("task_id") == t.get("task_id")
        ]
        # Sort newest-first.
        events.sort(key=lambda e: -(e.get("at", 0) or 0))
        return {
            "task": t,
            "comments": [],   # noop: not implementing comments in this MVP
            "events": events,
            "links": {"parents": [], "children": []},
            "runs": [],
        }


# ---- utility: safe color validator -----------------------------------------

def _sanitize_color(c: str | None) -> str:
    """Block CSS-context injection — same idea as the client-side check in
    panels.js:_kanbanSafeColor. We accept hex codes or simple named colors
    and reject anything else (semicolons, parens, url()s, etc.)."""
    if not isinstance(c, str):
        return ""
    s = c.strip()
    if not s:
        return ""
    if re.match(r"^#[0-9a-fA-F]{3,8}$", s):
        return s
    if re.match(r"^[a-zA-Z]{3,32}$", s):
        return s
    return ""
