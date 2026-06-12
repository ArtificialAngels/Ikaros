r"""
Workspace file browser for Hermes.

Provides a thin, whitelist-gated view of the Hermes project tree so the
new WebUI's right-hand "files" panel can list, read, and serve media from
the agent's own data, docs, and skills directories — without exposing the
rest of the user's filesystem.

Trust model
-----------
- The trust boundary is ``HERMES_ROOT`` (parent of the ``hermes/`` package).
  We never let a request resolve to anything outside of it.
- A *workspace* is a directory under (or equal to) ``HERMES_ROOT`` that the
  user has registered. The default workspace is ``HERMES_ROOT`` itself.
- Within a workspace, only **whitelisted sub-paths** are reachable:

      data/knowledge   data/memory   data/models   data/skills
      data/logs        docs          tests
      README.md        AGENTS.md     (root files)

  Any other path — even if it exists on disk and is inside the workspace —
  returns ``403`` from the public API. This is defense in depth: even if a
  caller finds the endpoint, they cannot escape the curated set of dirs.

- Path traversal (``../../../etc/passwd``) is blocked the same way: the
  resolved absolute path is checked against the whitelist with
  ``Path.is_relative_to``. On Windows we also compare via
  ``os.path.normcase`` so the case-folding filesystem does not give an
  attacker an easy bypass.

Public API
----------
- :class:`WorkspaceManager`:
    - ``list_workspaces() -> list[dict]``
    - ``add_workspace(path) -> dict``            (validates under HERMES_ROOT)
    - ``remove_workspace(path) -> bool``
    - ``resolve(rel_path, workspace_path=None) -> Path``  (raises ``PermissionError``)
    - ``list_dir(rel_path, workspace_path=None) -> list[dict]``
    - ``read_file(rel_path, workspace_path=None, max_bytes=200_000) -> str``
    - ``media_path(rel_path, workspace_path=None) -> Path``  (file path for FileResponse)

Persistence
-----------
The active workspace list lives at ``<hermes>/data/workspaces.json``::

    {
      "active": "default",
      "workspaces": [
        {"name": "default", "path": "E:\\Hermes Agent", "added_at": 1700000000.0}
      ]
    }

On first use the file is created with the default workspace pointing at
``HERMES_ROOT``. Writes are atomic (tempfile + ``os.replace``) and guarded
by an ``asyncio.Lock`` so concurrent ``/api/workspaces/add`` and
``/api/workspaces/remove`` calls do not race.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes.workspace")

# Hermes project root — the trust boundary. Resolved once at import so
# every check goes through the same canonical path. We use ``.resolve()``
# so symlinks, ``..``, and mixed slashes all collapse to one form.
HERMES_ROOT: Path = Path(__file__).resolve().parent.parent

# Whitelist of sub-paths (relative to a workspace root) that are allowed.
# Anything outside this set is 403, even if it exists on disk. The keys
# are display labels; the values are the relative paths.
WHITELIST_DIRS: dict[str, str] = {
    "knowledge": "data/knowledge",
    "memory":    "data/memory",
    "models":    "data/models",
    "skills":    "data/skills",
    "logs":      "data/logs",
    "docs":      "docs",
    "tests":     "tests",
}
WHITELIST_ROOT_FILES: frozenset[str] = frozenset({"README.md", "AGENTS.md"})

# Default cap for text reads (matches the spec). 200 KB is enough to view
# most KB markdown files and config files without choking the UI.
DEFAULT_MAX_READ_BYTES: int = 200_000


def _norm(path: Path) -> Path:
    """Return a case-normalized, fully-resolved absolute ``Path``.

    ``Path.resolve()`` already handles symlinks and ``..``; the extra
    ``normcase`` matters on Windows where ``C:/Foo`` and ``c:/foo`` are
    the same directory but compare unequal as strings.
    """
    resolved = Path(os.path.normcase(str(path.resolve())))
    return resolved


class WorkspaceManager:
    """Manages the list of user-visible workspaces and the read-only file
    browser served by :mod:`hermes.server`.

    The class is intentionally small: no external deps, no caching beyond
    the in-memory workspaces list, and no async I/O for reads. All disk
    activity (list/read) is done synchronously inside request handlers —
    the directories are small (a few MB on a USB stick) and blocking
    briefly is fine.
    """

    def __init__(self, state_file: Path | str | None = None, root: Path | str | None = None):
        self.root: Path = _norm(Path(root) if root else HERMES_ROOT)
        self.state_file: Path = Path(state_file) if state_file else (
            HERMES_ROOT / "hermes" / "data" / "workspaces.json"
        )
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        # asyncio.Lock guards concurrent edits to the JSON file. Reads are
        # lock-free (we tolerate stale-but-valid data for a few ms).
        self._lock = asyncio.Lock()
        self._state: dict[str, Any] = self._load_state()

    # ---- state file --------------------------------------------------------

    def _default_state(self) -> dict[str, Any]:
        return {
            "active": "default",
            "workspaces": [
                {
                    "name": "default",
                    # Always use the resolved trust root, never a hardcoded path —
                    # this keeps the workspace portable across drive letters and
                    # project directory renames.
                    "path": str(self.root),
                    "added_at": time.time(),
                }
            ],
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            state = self._default_state()
            self._save_state_unlocked(state)
            return state
        try:
            raw = self.state_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict) or "workspaces" not in data:
                raise ValueError("malformed state file")
            # Repair: ensure default workspace exists.
            if not any(w.get("name") == "default" for w in data["workspaces"]):
                data["workspaces"].insert(0, {
                    "name": "default",
                    "path": str(self.root),
                    "added_at": time.time(),
                })
                data.setdefault("active", "default")
                self._save_state_unlocked(data)
            return data
        except (json.JSONDecodeError, ValueError, OSError) as e:
            logger.warning("workspaces.json unreadable (%s); resetting", e)
            state = self._default_state()
            self._save_state_unlocked(state)
            return state

    def _save_state_unlocked(self, state: dict[str, Any]) -> None:
        """Atomic write: tempfile + ``os.replace``. Caller must hold ``_lock``."""
        tmp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except Exception:
            # Best-effort cleanup of the tempfile; the real state file is
            # untouched on failure.
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise

    async def _save_state(self) -> None:
        async with self._lock:
            self._save_state_unlocked(self._state)

    # ---- workspace management ---------------------------------------------

    def list_workspaces(self) -> list[dict]:
        """Return the registered workspaces (name, path, added_at).

        Order matches the JSON file. Each entry has string ``name``,
        string ``path``, and float ``added_at`` (epoch seconds).
        """
        out: list[dict] = []
        for w in self._state.get("workspaces", []):
            out.append({
                "name": w.get("name", ""),
                "path": w.get("path", ""),
                "added_at": float(w.get("added_at", 0.0)),
            })
        return out

    def get_workspace(self, name_or_path: str | None) -> dict | None:
        """Look up a workspace by name or absolute path. Returns ``None`` if not found."""
        if not name_or_path:
            return None
        needle = str(name_or_path).strip()
        for w in self._state.get("workspaces", []):
            if w.get("name") == needle or w.get("path") == needle:
                return {
                    "name": w.get("name", ""),
                    "path": w.get("path", ""),
                    "added_at": float(w.get("added_at", 0.0)),
                }
        return None

    async def add_workspace(self, path: str) -> dict:
        """Add a workspace, validating that ``path`` is a directory inside
        ``HERMES_ROOT`` (so a misbehaving caller cannot register
        ``C:\\Windows`` and then list its contents).

        Returns the new workspace entry. Raises ``PermissionError`` if the
        path is outside the trust boundary, ``FileNotFoundError`` if it
        does not exist, or ``ValueError`` on bad input.
        """
        if not path or not isinstance(path, str):
            raise ValueError("path must be a non-empty string")
        raw = Path(path)
        if not raw.is_absolute():
            # Resolve against HERMES_ROOT so relative inputs are unambiguous.
            raw = self.root / raw
        resolved = _norm(raw)
        if not resolved.exists() or not resolved.is_dir():
            raise FileNotFoundError(f"workspace path does not exist or is not a directory: {path}")
        # Must be inside the trust boundary.
        if not self._is_under_root(resolved):
            raise PermissionError(f"workspace path is outside HERMES_ROOT: {path}")

        # Generate a unique name; default to the leaf dir name unless taken.
        base_name = resolved.name or "default"
        name = base_name
        existing = {w.get("name") for w in self._state.get("workspaces", [])}
        if name in existing:
            name = f"{base_name}-{uuid.uuid4().hex[:6]}"
        entry = {
            "name": name,
            "path": str(resolved),
            "added_at": time.time(),
        }
        async with self._lock:
            self._state.setdefault("workspaces", []).append(entry)
            self._save_state_unlocked(self._state)
        logger.info("added workspace %s -> %s", name, resolved)
        return entry

    async def remove_workspace(self, path: str) -> bool:
        """Remove a workspace by name or path. Returns True on success.

        The default workspace is protected: removing ``"default"`` (by name
        or by its path) is a no-op so the system always has at least one
        workspace to browse.
        """
        if not path or not isinstance(path, str):
            raise ValueError("path must be a non-empty string")
        needle = path.strip()
        async with self._lock:
            kept: list[dict] = []
            removed = False
            for w in self._state.get("workspaces", []):
                if not removed and (w.get("name") == needle or w.get("path") == needle):
                    if w.get("name") == "default":
                        logger.info("refusing to remove default workspace")
                    else:
                        removed = True
                        continue
                kept.append(w)
            if removed:
                self._state["workspaces"] = kept
                self._save_state_unlocked(self._state)
            return removed

    # ---- path resolution & whitelist checks --------------------------------

    def _is_under_root(self, resolved: Path) -> bool:
        """True iff ``resolved`` is the trust root or any sub-directory of it."""
        try:
            return resolved == self.root or resolved.is_relative_to(self.root)
        except (ValueError, OSError):
            return False

    def _resolve_workspace(self, workspace_path: str | None) -> Path:
        """Resolve a workspace identifier (name or path) to an absolute,
        case-normalized path. Falls back to the trust root if the input
        is missing or unknown — this matches the "single workspace"
        reality of the portable build.
        """
        if not workspace_path:
            return self.root
        ws = self.get_workspace(workspace_path)
        if ws is None:
            # Unknown workspace — try to use the literal path as long as
            # it lives under the trust boundary. This is convenient for
            # the new UI which sometimes sends a raw path.
            candidate = Path(workspace_path)
            if not candidate.is_absolute():
                candidate = self.root / candidate
            try:
                resolved = _norm(candidate)
            except (OSError, ValueError):
                return self.root
            if not resolved.is_dir():
                return self.root
            if not self._is_under_root(resolved):
                return self.root
            return resolved
        try:
            return _norm(Path(ws["path"]))
        except (OSError, ValueError):
            return self.root

    def _check_whitelist(self, workspace_root: Path, resolved: Path, allow_root: bool = False) -> None:
        """Raise ``PermissionError`` if ``resolved`` is not in the whitelist.

        The whitelist is interpreted relative to the trust root
        (``HERMES_ROOT``) so adding a sub-workspace like
        ``HERMES_ROOT/data`` still works — the caller can browse
        ``data/knowledge`` from there because the resolved absolute path
        still lives under ``HERMES_ROOT/data/knowledge`` which is in the
        allowed set.

        ``allow_root=True`` lets the workspace root itself pass the check
        even though it is not technically a whitelisted subtree. The
        caller is then responsible for filtering the listed contents to
        whitelisted children only (see ``list_dir``).
        """
        if not self._is_under_root(resolved):
            raise PermissionError(f"path escapes trust boundary: {resolved}")
        # Compute the path relative to the trust root; that's what we
        # check against the whitelist.
        try:
            rel = resolved.relative_to(self.root)
        except ValueError:
            raise PermissionError(f"path escapes trust boundary: {resolved}")
        rel_str = rel.as_posix()
        # Workspace root is special: the directory itself is not a
        # whitelisted subtree (it contains bin/ and portable-python/),
        # but listing it should be allowed so the UI can render the
        # top-level whitelisted dirs and README/AGENTS. Deeper validation
        # is enforced by filtering the output in list_dir.
        if allow_root and resolved == _norm(self.root):
            return
        # Root-level allowed files. Compare case-insensitively because
        # Windows is case-insensitive at the filesystem layer and we
        # canonicalized the path with ``normcase``.
        if rel.parent == Path(".") and resolved.is_file():
            lowered = resolved.name.lower()
            if any(lowered == allowed.lower() for allowed in WHITELIST_ROOT_FILES):
                return
            raise PermissionError(f"file is not in whitelist: {resolved.name}")
        # Whitelisted subdirs (and anything below them).
        for wl_rel in WHITELIST_DIRS.values():
            wl_root = _norm(self.root / wl_rel)
            try:
                if resolved == wl_root or resolved.is_relative_to(wl_root):
                    return
            except (ValueError, OSError):
                continue
        # If we reach here, the path is inside the trust root but not in
        # any whitelisted subtree. This catches e.g. ``portable-python``
        # and ``bin`` — both real, both excluded by policy.
        raise PermissionError(
            f"path is not in the workspace whitelist: {rel_str or resolved.name}"
        )

    def resolve(self, rel_path: str, workspace_path: str | None = None) -> Path:
        """Resolve a request-relative path to an absolute path on disk and
        verify the whitelist. Raises ``PermissionError`` on any policy
        violation; ``FileNotFoundError`` if the path does not exist.
        """
        if rel_path is None:
            rel_path = ""
        # Normalize and strip a leading slash so the caller can pass
        # either ``data/knowledge`` or ``/data/knowledge`` interchangeably.
        cleaned = rel_path.replace("\\", "/").lstrip("/")
        ws_root = self._resolve_workspace(workspace_path)
        # Empty / "." means "list the workspace root"; we permit that as
        # a special case (see ``_check_whitelist``), and the caller is
        # responsible for filtering the listed children.
        is_root_request = cleaned in ("", ".")
        if is_root_request:
            candidate = ws_root
        else:
            candidate = (ws_root / cleaned).resolve()
        candidate = _norm(candidate)
        self._check_whitelist(ws_root, candidate, allow_root=is_root_request)
        if not candidate.exists():
            raise FileNotFoundError(f"path does not exist: {rel_path}")
        return candidate

    # ---- list / read / media -----------------------------------------------

    def list_dir(self, rel_path: str, workspace_path: str | None = None) -> list[dict]:
        """List a directory, returning one entry per child.

        Each entry: ``{"name", "type", "size", "modified", "path"}``.

        - ``type`` is ``"dir"`` or ``"file"``.
        - ``size`` is bytes (0 for directories).
        - ``modified`` is epoch seconds (float).
        - ``path`` is the *child-relative* path (POSIX style) suitable
          for feeding back into ``list_dir`` or ``read_file``. We never
          expose the absolute filesystem path to the client.
        """
        abs_path = self.resolve(rel_path, workspace_path)
        if not abs_path.is_dir():
            raise NotADirectoryError(f"not a directory: {rel_path}")
        entries: list[dict] = []
        rel_clean = (rel_path or "").replace("\\", "/").lstrip("/")
        is_root_listing = (rel_clean in ("", "."))
        for child in sorted(abs_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                st = child.stat()
            except OSError as e:
                # Skip entries we cannot stat (broken symlink, perms).
                logger.debug("skip %s: %s", child, e)
                continue
            # When listing the workspace root, only emit whitelisted
            # subdirs and the two allowed root files. This keeps the
            # UI from ever showing the user ``bin/``, ``portable-python/``,
            # or ``__pycache__/`` — even though they live on disk and are
            # inside HERMES_ROOT. Per-entry whitelist is *only* enforced
            # at the root level; deeper directories are protected by the
            # resolve() check on the next navigation.
            if is_root_listing:
                if child.is_dir():
                    # Only keep whitelisted dirs.
                    if not self._is_whitelisted_dir(child):
                        continue
                else:
                    lowered = child.name.lower()
                    if not any(lowered == a.lower() for a in WHITELIST_ROOT_FILES):
                        continue
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": int(st.st_size) if child.is_file() else 0,
                "modified": float(st.st_mtime),
                "path": (Path(rel_clean) / child.name).as_posix() if rel_clean else child.name,
            })
        return entries

    def _is_whitelisted_dir(self, abs_dir: Path) -> bool:
        """True iff ``abs_dir`` is the top of a whitelisted subtree."""
        try:
            resolved = _norm(abs_dir)
        except (OSError, ValueError):
            return False
        for wl_rel in WHITELIST_DIRS.values():
            wl_root = _norm(self.root / wl_rel)
            try:
                if resolved == wl_root:
                    return True
            except (ValueError, OSError):
                continue
        return False

    def _looks_binary(self, data: bytes, sample: int = 8192) -> bool:
        """Sniff the first ``sample`` bytes for NUL / control chars.

        Cheap and good enough for "should I send this as text?" — false
        positives are possible for binary-looking UTF-16 text, but those
        are vanishingly rare in the agent's data tree.
        """
        if not data:
            return False
        chunk = data[:sample]
        # Count "binary-ish" bytes: NUL, other control chars that are not
        # common whitespace (tab, LF, CR, FF, VT).
        weird = sum(1 for b in chunk if b == 0 or (b < 32 and b not in (9, 10, 12, 13)))
        return weird > len(chunk) * 0.10  # >10% weird -> treat as binary

    def read_file(
        self,
        rel_path: str,
        workspace_path: str | None = None,
        max_bytes: int = DEFAULT_MAX_READ_BYTES,
    ) -> str:
        """Read a text file, decoded as UTF-8 (with ``errors='replace'``).

        Raises :class:`PermissionError` for whitelist violations,
        :class:`IsADirectoryError` if the path is a directory,
        :class:`UnicodeDecodeError`/``ValueError`` for binary content,
        and :class:`OSError` if the file is larger than ``max_bytes``.
        """
        abs_path = self.resolve(rel_path, workspace_path)
        if abs_path.is_dir():
            raise IsADirectoryError(f"is a directory: {rel_path}")
        size = abs_path.stat().st_size
        if size > max_bytes:
            raise ValueError(
                f"file too large to read inline: {size} bytes (max {max_bytes})"
            )
        raw = abs_path.read_bytes()
        if self._looks_binary(raw):
            raise ValueError("file appears to be binary; use /api/media instead")
        return raw.decode("utf-8", errors="replace")

    def media_path(self, rel_path: str, workspace_path: str | None = None) -> tuple[Path, str]:
        """Resolve a media file and return ``(absolute_path, mime_type)``.

        Unlike :meth:`read_file`, this does **not** reject binary content
        — the whole point of the media endpoint is to serve images and
        other binary assets to the UI. Whitelist and traversal checks
        still apply.
        """
        abs_path = self.resolve(rel_path, workspace_path)
        if abs_path.is_dir():
            raise IsADirectoryError(f"is a directory: {rel_path}")
        mime, _ = mimetypes.guess_type(str(abs_path))
        return abs_path, (mime or "application/octet-stream")
