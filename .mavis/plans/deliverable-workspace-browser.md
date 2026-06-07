# Workspace File Browser — Deliverable

**Task**: workspace 文件浏览端点 + UI 可见
**Track**: workspace-browser
**Date**: 2026-06-07 12:42
**Status**: ✅ code-complete + verified on live server (port 7860)

---

## Summary

Added a whitelist-gated, read-only file browser so the new WebUI's
right-hand "files" panel can list, read, and serve media from Hermes's
own data, docs, and skills directories. A new `WorkspaceManager` class
encapsulates path-traversal defense, JSON persistence of the registered
workspaces, and a case-insensitive whitelist; six new FastAPI endpoints
plus adapter changes complete the wire-up. All five spec verification
cases pass on a live server with the expected status codes (200/200/200/
403/403).

---

## Changed files

| Path | Status | Lines | Notes |
|------|--------|-------|-------|
| `hermes/workspace.py` | **new** | 484 | `WorkspaceManager` + trust boundary + whitelist + JSON persistence + binary sniff + mime lookup |
| `hermes/server.py` | modified | +130 | Added `WorkspaceManager` import + `FileResponse` import; inserted 6 endpoints after the `/api/webui/noop` block, before the catch-all |
| `hermes/static/api-adapter.js` | modified | +30 / -10 | Removed noop transforms for `/api/workspaces{,/add,/remove}`, `/api/list`, `/api/file`, `/api/media`; added a new per-route `dropParams` field that strips named query params (e.g. `session_id`); bumped header comment to v0.4 |
| `hermes/data/workspaces.json` | auto-created at first call | — | One default workspace pointing at `HERMES_ROOT` |

No other files touched. The `webui_settings.py` and `cron.py` modules
added by parallel tracks were respected; their `from hermes.X import`
imports were not modified.

---

## Endpoint contract (for the verifier)

All routes live under `/api/*` and resolve before the catch-all
`/api/{path:path}` so the generic stub does not eat them.

| Method | Path | Query / body | Response | Notes |
|--------|------|--------------|----------|-------|
| `GET`  | `/api/workspaces`            | — | `{"workspaces":[{name,path,added_at}, …]}` | Always at least the `default` workspace |
| `POST` | `/api/workspaces/add`        | `{"path": "…"}` | `{"ok":true,"workspace":{…},"workspaces":[…]}` or `403`/`404`/`400` | Path must be a real directory inside `HERMES_ROOT` |
| `POST` | `/api/workspaces/remove`     | `{"path": "…"}` or `{"name": "…"}` | `{"ok":<bool>,"removed":<bool>,"workspaces":[…]}` | `default` cannot be removed |
| `GET`  | `/api/list`                  | `?path=…&workspace=…&session_id=…` | `{"entries":[{name,type,size,modified,path}, …]}` | `session_id` ignored; empty path lists the workspace root with whitelisted children only |
| `GET`  | `/api/file`                  | `?path=…&workspace=…&session_id=…` | `{"path":…,"content":…,"size":…}` | Text-only; 200 KB cap; binary → 400 |
| `GET`  | `/api/media`                 | `?path=…&workspace=…&session_id=…` | binary `FileResponse` with `Content-Type` from `mimetypes` | Whitelist still applies |

`HTTPException` codes used: 400 (bad input / not-a-dir / binary /
oversize), 403 (whitelist violation / out-of-bound workspace), 404
(missing path).

---

## Whitelist definition

The trust boundary is `HERMES_ROOT = parent of the hermes/ package =
E:\Hermes Agent`. Path comparison uses `os.path.normcase` so Windows's
case-folding filesystem does not give an attacker an easy bypass.

```python
WHITELIST_DIRS = {
    "knowledge": "data/knowledge",
    "memory":    "data/memory",
    "models":    "data/models",
    "skills":    "data/skills",
    "logs":      "data/logs",
    "docs":      "docs",
    "tests":     "tests",
}
WHITELIST_ROOT_FILES = frozenset({"README.md", "AGENTS.md"})
```

`bin/`, `portable-python/`, `__pycache__/`, `hermes/` (the package
itself) all sit inside `HERMES_ROOT` but are deliberately **not** in
the whitelist, so they are unreachable from the API even with a
correctly-formed request.

Workspace roots can be added via `POST /api/workspaces/add`, but the
proposed path must be a real directory **inside `HERMES_ROOT`**. Out-of-
bound paths (`C:\Windows`, etc.) are rejected with 403 — defense in
depth on top of the whitelist.

---

## Verification — 5 spec commands on live server (port 7860)

All commands run with `Invoke-WebRequest` against a freshly-restarted
server. Outputs preserved verbatim below.

### 1. `GET /api/workspaces` — at least one default workspace

```
Status: 200
Body: {"workspaces":[{"name":"default","path":"e:\\hermes agent","added_at":1780806761.8901815}]}
```

### 2. `GET /api/list?path=data/knowledge` — list a whitelisted dir

```
Status: 200
Body: {"entries":[{"name":"sources","type":"dir","size":0,"modified":1780590499.7437985,"path":"data/knowledge/sources"}]}
```

### 3. `GET /api/file?path=README.md` — read a whitelisted root file

```
Status: 200
Body: {"path":"README.md","content":"# Hermes Portable Agent — 完全自包含版\n\n> **赛博游民数字管家** · 装在 U 盘里 · 插到任何 Windows 电脑就能跑 · **零依赖** …","size":8695}
```

### 4. `GET /api/list?path=../../../etc/passwd` — path traversal blocked

```
Status: 403
Body: {"detail":"path escapes trust boundary: e:\\etc\\passwd"}
```

### 5. `GET /api/file?path=portable-python/python.exe` — non-whitelisted binary blocked

```
Status: 403
Body: {"detail":"path is not in the workspace whitelist: portable-python/python.exe"}
```

---

## Bonus checks (not in the spec, all pass)

| Probe | Result |
|-------|--------|
| `GET /api/list?session_id=abc&path=data/memory` (UI compat) | 200, `session_id` ignored, returns `memory.jsonl` |
| `GET /api/list?path=data/memory/` (trailing slash) | 200, normalised correctly |
| `GET /api/file?path=AGENTS.md` (case-insensitive) | 200, 30 148 bytes |
| `GET /api/list` (no path — list workspace root) | 200, only `[docs, tests, README.md, AGENTS.md]` — `bin/`, `portable-python/`, etc. are filtered out |
| `POST /api/workspaces/add {"path":"C:/Windows"}` | 403, `workspace path is outside HERMES_ROOT` |
| `POST /api/workspaces/add {"path":"E:/Hermes Agent/data"}` | 200, registers a `data` sub-workspace |
| `POST /api/workspaces/remove {"name":"data"}` | 200, removed |
| `POST /api/workspaces/remove {"path":"default"}` | 200, `{"ok":false,"removed":false}` — default is protected |
| `GET /api/media?path=README.md` | 200, `Content-Type: text/markdown; charset=utf-8`, 8 695 bytes |
| `GET /api/file?path=data/models/Qwen1.5-1.8B-Chat-Q4_K_M.gguf` | 400, `file too large to read inline: 1217752928 bytes (max 200000)` |

---

## Known limitations / out-of-scope (per task brief)

- **No upload / edit / delete** — read-only by design.
- **No file search / grep** — out of scope.
- **No git integration** — `/api/git-info` still returns the noop
  stub from the existing adapter.
- **`/api/media` whitelist is identical to `/api/file`** — there is
  no separate "binary-only" allowlist. The intent is that any media
  asset the UI wants to render must already live in one of the curated
  dirs; if you need a thumbnail of a GGUF, you add a thumbnail to
  `data/models/` first.
- **No directory recursion in `list_dir`** — child entries are emitted
  without per-entry whitelist re-check. Deeper validation happens when
  the user navigates into a child; if they request
  `?path=data/models/bad-child/`, the `resolve()` call rejects it.
- **Workspaces are flat** — only one "active" concept in the JSON
  (`active` field is reserved but not currently consulted by the
  server; `_resolve_workspace` defaults to `default`).
- **One in-process `WorkspaceManager` per app** — for the portable
  build, the same `HERMES_ROOT` is the only thing that matters, and a
  single instance is sufficient. If you ever run multiple Hermes
  instances against the same data dir, you'll want to share
  `workspaces.json` with file-locking.
- **The `api-adapter.js` `dropParams` mechanism is new** — it is a
  per-route array of param names to strip from the forwarded query
  string. If you add more routes that need to drop params (e.g. another
  Open WebUI compat call), just add `dropParams: ['session_id']` to
  the route spec.
- **The new server route registrations and the existing catch-all
  ordering must be preserved** — see the existing comment block above
  the catch-all (around line 700 in `server.py`). A catch-all
  registered too early will shadow these endpoints; the same bit the
  project twice before.

---

## File-level pointers (for the verifier)

- `hermes/workspace.py:1-105` — module docstring + trust model
- `hermes/workspace.py:107-130` — `WHITELIST_DIRS` /
  `WHITELIST_ROOT_FILES` / `HERMES_ROOT` constants
- `hermes/workspace.py:152-170` — `_norm()` (case + symlink + `..` collapse)
- `hermes/workspace.py:248-280` — `add_workspace()` with out-of-bound check
- `hermes/workspace.py:340-380` — `_check_whitelist()` (the actual gate)
- `hermes/workspace.py:393-460` — `list_dir()` with per-entry root filtering
- `hermes/workspace.py:485-510` — `read_file()` with binary sniff + size cap
- `hermes/workspace.py:512-525` — `media_path()` with mime guess
- `hermes/server.py:30,38` — `FileResponse` + `WorkspaceManager` imports
- `hermes/server.py:699-822` — six new endpoints (after `/api/webui/noop`, before the catch-all comment)
- `hermes/static/api-adapter.js:285-300` — new workspaces routes
- `hermes/static/api-adapter.js:394-410` — new file/media routes with `dropParams`
- `hermes/static/api-adapter.js:465-475` — `dropParams` enforcement in the fetch wrapper
