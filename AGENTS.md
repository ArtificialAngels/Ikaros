# Hermes Agent — Project Memory Bank

> **Read this first** when picking up the project after a break.
> This file captures the project state, architecture, modification history,
> debugging tips, and the gotchas we hit along the way.

---

## 1. What This Is

A **portable, USB-drive-deployable Hermes Agent** — a hybrid LLM (cloud + local)
with a modern full-featured Web UI, designed to run on any Windows PC with zero install.

**One-click UX:** `bin\hermes-all.bat` → browser opens to `http://localhost:8648/` → chat ready.

**Web UI Source:** [EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui) — Vue 3 + Koa + Socket.IO

---

## 2. Architecture

Three processes, each with a single responsibility:

| Port  | Process                | Role                                                           |
|-------|------------------------|----------------------------------------------------------------|
| :8080 | **llama-server**       | LLM engine. OpenAI-compatible HTTP API. Internal — not exposed. |
| :7860 | **Hermes FastAPI**     | Memory + knowledge base + RAG embeddings shim + legacy static UI. |
| :8648 | **Hermes Web UI**      | **Main Web Interface** (EKKOLearnAI/hermes-web-ui). Vue 3 + Koa + Socket.IO. Browser opens here. |

**Data flow:**
```
Browser → :8648 Hermes Web UI (Koa BFF + Vue 3 SPA)
                │
                ├── Socket.IO /chat-run → Hermes Agent Bridge → hermes-agent-source
                │
                └── REST API → :7860 Hermes FastAPI (embeddings/RAG /api/*)
                             → :8080 llama-server (chat /v1/*)
```

**Hermes Web UI** (from [EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui)):
- Full-featured Vue 3 + TypeScript frontend with Koa BFF backend
- Features: AI chat, platform channels, usage analytics, cron jobs, model management,
  multi-profile, file browser, group chat, skills, logs, web terminal
- Communicates with local llama-server via OpenAI-compatible API
- Uses Hermes Agent Bridge for chat execution

llama-server only loads **one model at a time**; the WebUI shows whichever
model llama-server exposes via `--alias`). When llama-server is down, `/v1/models`
falls back to scanning `data/models/*.gguf` via `hermes/gguf.py`. See §6 for
multi-model options.

---

## 3. Project Layout

```
E:\Hermes Agent\
├── .env                          # runtime env vars (API keys, paths)
├── AGENTS.md                     # THIS FILE
├── README.md                     # user-facing docs
├── config\
│   └── hermes.yaml               # main config (LLM providers, memory, KB)
├── hermes\                       # Python package (the agent)
│   ├── __init__.py
│   ├── __main__.py               # `python -m hermes serve` / chat
│   ├── agent.py                  # HermesAgent class
│   ├── config.py                 # config loader (env-aware)
│   ├── llm.py                    # LLM router + OpenAI/Anthropic/MiniMax providers
│   ├── memory.py                 # JSONL memory store + embedder
│   ├── knowledge.py              # markdown KB with chunking
│   ├── skills.py                 # skill registry (time/calc/echo/...)
│   ├── server.py                 # FastAPI: /v1/embeddings, /v1/models, /api/*, /static/
│   ├── sessions.py               # ★ SessionStore (NEW 2026-06-07) — disk-backed chat sessions
│   ├── workspace.py              # ★ WorkspaceManager (NEW 2026-06-07) — whitelisted file browser
│   ├── webui_settings.py         # ★ WebUISettingsStore (NEW 2026-06-07) — atomic JSON settings
│   ├── kanban.py                 # ★ KanbanStore (NEW 2026-06-07) — boards/tasks/events
│   ├── cron.py                   # ★ CronManager (NEW 2026-06-07) — scheduled jobs (30s loop)
│   ├── static\                   # ★ Hermes WebUI (nesquena/hermes-webui) — 3.5MB
│   │   ├── index.html            # three-panel dark UI
│   │   ├── style.css             # 16 skins, light/dark
│   │   ├── ui.js / boot.js / sessions.js / messages.js / panels.js / ...
│   │   ├── api-adapter.js        # OUR adapter: translates upstream endpoints to our /api/*
│   │   └── vendor\               # streaming-markdown, KaTeX, js-yaml (no build step)
│   ├── gguf.py                   # GGUF v2/v3 header parser (used by /v1/models + CLI)
│   ├── embeddings.py             # SBERT / hash embedder
│   ├── gpu.py                    # GPU detection (nvidia-smi / Vulkan / WMI)
│   ├── doctor.py                 # bin/hermes-doctor.bat
│   ├── gopeed_client.py          # gopeed-web API
│   ├── planner.py                # autonomous task execution
│   └── scripts\                  # utility scripts (production)
│       ├── import_ollama_blobs.py   # Ollama blob → GGUF converter
│       ├── install_skill.py         # skill marketplace
│       ├── rebuild_kb.py            # KB re-ingest
│       ├── model_manager.py         # CLI for /api/launcher
│       ├── model_launcher_gui.py    # GUI for /api/launcher
│       └── gpu_detector.py          # first-run GPU detection
├── portable-python\              # embedded Python 3.12.10 + pip deps
│   └── python.exe
├── runtime\                      # llama.cpp binaries
│   ├── llama-server.exe          # CPU
│   ├── llama-server-cuda-12.4.exe  # NVIDIA RTX 20/30/40/50 (driver >= 525)
│   ├── llama-server-cuda-11.8.exe  # older NVIDIA (GTX 900 / old driver)
│   ├── llama-server-vulkan.exe   # AMD / Intel / NVIDIA fallback
│   ├── aria2c.exe                # multi-thread downloader
│   ├── gopeed-web.exe            # download bridge (Python talks HTTP to it)
│   └── *.dll                     # runtime DLLs (cudart, vulkan, etc.)
├── data\
│   ├── models\                   # GGUF files
│   │   ├── Qwen2.5-3B-Instruct-Q4_K_M.gguf
│   │   ├── Qwen2.5-7B-Instruct-Q4_K_M.gguf
│   │   ├── Qwen1.5-1.8B-Chat-Q4_K_M.gguf
│   │   ├── Qwen3.5-35B-A3B-Q4_K_M.gguf  (20.5GB, MoE)
│   │   └── f5ee307a2982.gguf            # Ollama-imported qwen3, 22.8GB
│   ├── webui-new\                # ★ NEW 2026-06-08 — Hermes Web UI (EKKOLearnAI)
│   │   ├── app\                  # Web UI application (Vue 3 + Koa)
│   │   │   ├── packages/client\  # Vue 3 frontend
│   │   │   ├── packages/server\  # Koa BFF backend
│   │   │   └── packages/desktop\ # Electron wrapper (optional)
│   │   └── data\                 # Web UI state (SQLite, auth, sessions)
│   ├── hermes-agent\             # ★ Hermes Agent data home for Web UI
│   │   ├── config.yaml           # Agent config (providers, defaults, toolsets)
│   │   ├── auth.json             # Credential pool
│   │   ├── state.db              # Agent state
│   │   ├── sessions\             # per-session chat history (atomic JSON)
│   │   ├── workspaces.json       # WorkspaceManager whitelist
│   │   ├── webui_settings.json   # WebUI user prefs (32 keys)
│   │   ├── kanban\               # boards/tasks/events.json
│   │   ├── crons\                # jobs.json (croniter-scheduled)
│   │   └── skills\               # ★ NEW 2026-06-08 — installed skills (see §11)
│   ├── memory\                   # JSONL memory store
│   ├── knowledge\                # markdown KB source + index.jsonl
│   ├── skills\                   # Hermes FastAPI skill registry (built-in: time/calc/echo/...)
│   ├── hermes-agent\skills\      # ★ NEW 2026-06-08 — installed skills for Web UI (see §15)
│   │   ├── finance\              # excel-author, pptx-author, comps-analysis, dcf-model
│   │   ├── creative\             # avoid-ai-writing, claude-design, drawio-skill
│   │   ├── productivity\         # google-workspace, nano-pdf, ocr-and-documents,
│   │   │                         #   plur-memory, plur-session-end, powerpoint
│   │   └── autonomous-ai-agents\ # hermes-dojo
│   │   (others: apikey-image-gen, grok-image-to-video, hyperframes,
│   │    markdown-viewer, remotion — empty legacy stubs)
│   ├── logs\                     # hermes.log + bootstrap.log
│   ├── sessions\                 # ★ NEW 2026-06-07 — one JSON file per chat session
│   ├── kanban\                   # ★ NEW 2026-06-07 — boards.json + tasks.json + events.json
│   ├── crons\                    # ★ NEW 2026-06-07 — jobs.json (croniter-scheduled)
│   └── webui_settings.json       # ★ NEW 2026-06-07 — single-file atomic webui prefs
├── bin\                          # user-facing launchers (CRLF line endings!)
│   ├── hermes-all.bat            # ★ MAIN: one-click everything (now opens :8648)
│   ├── webui-new.bat             # ★ NEW 2026-06-08 — Hermes Web UI launcher
│   ├── hermes.bat                # CLI agent (`hermes chat`)
│   ├── hermes-stop.bat           # kill all Hermes processes
│   ├── hermes-doctor.bat         # 8-section health report
│   ├── hermes-firstrun.bat       # first-run GPU detection
│   ├── hermes-models.py          # CLI model manager (list/switch/download)
│   ├── hermes-task.bat           # `hermes task "do X"`
│   ├── hermes-console.bat        # ★ NEW 2026-06-07 — wrapper for console.ps1
│   ├── hermes-console.ps1        # ★ NEW 2026-06-07 — model management shell
│   ├── hermes-trace.bat          # ★ NEW 2026-06-07 — wrapper for trace.ps1
│   ├── hermes-trace.ps1          # ★ NEW 2026-06-07 — real-time log viewer (webui/bridge/agent)
│   ├── hermes-model-run.bat      # ★ NEW 2026-06-07 — wrapper for live LLM log viewer
│   ├── hermes-model-run.ps1      # ★ NEW 2026-06-07 — tail llm-server.log/err with smart colors
│   ├── start-llm-smart.bat       # ★ llama-server with auto NGL
│   ├── start-llm.ps1             # PowerShell variant
│   ├── switch-model.bat          # hot-swap default model
│   ├── model-manager.bat         # quick launcher for model_manager.py
│   ├── install-embeddings.bat    # install sentence-transformers + model
│   ├── setup-runtime.bat         # download ALL llama.cpp variants + aria2
│   └── gpu-detect.bat            # one-shot GPU probe
├── tests\                        # functional test scripts (kept clean)
│   ├── test_hermes.py            # 17-test E2E suite (mock LLM, no GPU needed)
│   └── verify_smart_ngl.py       # verify NGL calculation logic
├── scripts\                      # legacy one-off scripts
└── requirements.txt
```

---

## 4. Components

### Hermes Web UI (EKKOLearnAI/hermes-web-ui) — Main Interface
- **Source**: [https://github.com/EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui)
- **Tech Stack**: Vue 3 + TypeScript + Vite + Naive UI (frontend) + Koa 2 (BFF backend)
- **Port**: 8648 (configurable via `PORT` env var)
- **Features**:
  - AI Chat: Real-time streaming via Socket.IO `/chat-run`, multi-session management, Markdown rendering
  - Platform Channels: Unified config for 8 platforms (Telegram/Discord/Slack/WhatsApp/Matrix/Feishu/WeChat/WeCom)
  - Usage Analytics: Token tracking, cost estimation, 30-day trends
  - Cron Jobs: Create/edit/pause/resume scheduled tasks
  - Model Management: Auto-discover models, provider management, OAuth login
  - Multi-Profile: Isolated configs, import/export/clone
  - File Browser: Remote file management (local/Docker/SSH/Singularity)
  - Group Chat: Multi-agent rooms with @mention routing
  - Skills & Memory: Browse/search installed skills
  - Logs: Agent/server/error logs with filtering
  - Web Terminal: Integrated terminal via node-pty
- **Integration**: Configured via `bin\webui-new.bat` with portable Python and local llama-server

### hermes/server.py
- FastAPI app
- Serves the Hermes WebUI (nesquena/hermes-webui) from `hermes/static/` at `/` and `/static/`
- Key endpoints: `/health` (JSON status), `/v1/embeddings`, `/v1/models` (live-proxied from
  llama-server, falls back to `data/models/*.gguf` scan via `hermes/gguf.py`),
  `/api/chat/*`, `/api/sessions`, `/api/memory`, `/api/skills`, `/api/task` (autonomous plan-execute),
  `/api/webui/*` (stubs for the upstream WebUI's ~25 expected endpoints, all answered by
  `static/api-adapter.js` client-side)
- **Hash-based embeddings** at `/v1/embeddings` — used by WebUI RAG
  (search quality is poor but it boots without a real embedding model)
- **Autonomous task API** at `/api/task` — POST `{goal, wait}` triggers the Planner
  (sync returns full result, async returns `task_id` to poll at `GET /api/task/{id}`)

### hermes/llm.py
- `LLMRouter` with fallback chain
- Providers: `OpenAIProvider` (covers OpenAI, llama-server, MiniMax via
  OpenAI-compat), `AnthropicProvider`, `MockProvider`
- MiniMax config in `hermes.yaml` is `provider: openai` with MiniMax base URL

### Hermes WebUI (nesquena/hermes-webui)
- Three-panel dark UI: left session list / center chat / right workspace
- Served at `/` by FastAPI from `hermes/static/` (no Node.js, no build step)
- 16 theme skins, light/dark, streaming-markdown, KaTeX math, Prism syntax highlighting
- **api-adapter.js** (in `hermes/static/`) wraps `window.fetch` + `EventSource` to
  translate the upstream WebUI's expected endpoints onto our `/api/chat/*` + `/v1/*`
  backends. Missing endpoints (workspaces, kanban, crons, etc.) return sane empty
  defaults so the UI continues to boot. See `static/api-adapter.js` for the full route
  table (~25 mapped + ~30 no-op).
- Single-model dropdown reflects live `/v1/models`: proxies llama-server when up,
  scans `data/models/*.gguf` when down

### llama-server (b9503)
- `--alias qwen2.5-3b-instruct` makes the model id clean (default
  returns filename like `Qwen2.5-3B-Instruct-Q4_K_M.gguf`)
- `--n-gpu-layers N` controls GPU offload: 0=CPU, 99=full GPU, N=hybrid
- See `bin\start-llm-smart.bat` for auto NGL calculation

### hermes/sessions.py (NEW 2026-06-07)
- `SessionStore` — disk-backed chat session store. One JSON file per session
  at `hermes/data/sessions/<session_id>.json`. Atomic writes (tempfile + `os.replace`).
- API: `list_sessions()`, `get_session(sid)`, `upsert_session(sid, data)`,
  `append_message(sid, msg)`, `delete_session(sid)`, `rename_session(sid, title)`.
- Replaces the previous in-memory `agent._chat_sessions` cache.
- Used by `/api/chat/sessions`, `/api/chat/sessions/{id}` (GET, DELETE, PATCH),
  and `/api/chat/start` (persists user + assistant messages on each chunk/finish).

### hermes/workspace.py (NEW 2026-06-07)
- `WorkspaceManager` — whitelisted file browser. Trust boundary is `HERMES_ROOT`.
  Whitelisted subdirs: `data/{knowledge,memory,models,skills,logs}`, `docs`, `tests`,
  plus root files `README.md` / `AGENTS.md`. Anything else returns 403.
- Path-traversal defense: `Path.resolve()` + `Path.is_relative_to()` + Windows
  `normcase` (case-folding FS bypass).
- API: `list_workspaces()`, `add_workspace(path)`, `remove_workspace(path)`,
  `list_dir(rel)`, `read_file(rel, max_bytes=200k)`, `media_path(rel)` (binary).
- Persistence: `hermes/data/workspaces.json` (atomic + `asyncio.Lock`).
- Endpoints: `/api/workspaces`, `/api/workspaces/add`, `/api/workspaces/remove`,
  `/api/list`, `/api/file`, `/api/media`.

### hermes/webui_settings.py (NEW 2026-06-07)
- `WebUISettingsStore` — atomic JSON store for the WebUI's user preferences
  (theme, skin, language, display, agent, memory, session, privacy, ...).
- 32 default keys defined in `DEFAULT_SETTINGS`. POST applies a 1-level
  nested deep-merge (nested dict keys are merged, not replaced).
- API: `get_settings_store()` (singleton), `.load()`, `.update(patch)`, `.all()`.
- Persistence: `hermes/data/webui_settings.json` (atomic + `asyncio.Lock`).
- Endpoints: `GET /api/webui/settings` returns full object;
  `POST /api/webui/settings` accepts partial patch and returns `{ok, settings}`.

### hermes/kanban.py (NEW 2026-06-07)
- `KanbanStore` — board/task/event store with atomic JSON writes, asyncio Lock,
  capped events log (2000), CSS-safe color sanitizer, default board + 5
  sample tasks bootstrap on first use.
- Board model: `board_id`, `slug`, `name`, `description`, `icon`, `color`,
  `columns` (list of column ids), `created_at`, `updated_at`, `archived`.
- Task model: `task_id`, `board_id`, `title`, `body`, `status`, `assignee`,
  `tenant`, `priority`, `tags`, `due_at`, `blocked`, `blocked_reason`,
  `created_at`, `updated_at`, `archived`.
- 22 endpoints under `/api/kanban/*` (all 4 HTTP methods on boards/tasks,
  plus block/unblock, bulk, comments, worktree, aggregates, events).
- SSE/dispatch/comments/worktree are noop stubs per spec; UI falls back to
  30s polling on `/api/kanban/events`.
- Persistence: `hermes/data/kanban/{boards,tasks,events}.json`.

### hermes/cron.py (NEW 2026-06-07)
- `CronManager` — scheduled job runner using `croniter` for next-fire
  calculation. 30-second background loop scans for due jobs and dispatches
  them in background asyncio tasks.
- Action types: `shell` (subprocess), `task` (agent.run_task), `webhook` (POST).
- Job model: `id`, `name`, `cron_expr`, `action`, `enabled`, `no_agent`,
  `script`/`prompt`, `deliver`, `profile`, `toast_notifications`, `skills`.
  Serialized with UI-shape fields (`schedule_display`, `next_run_at`,
  `last_run_at`, `last_status`, `last_error`, `last_output`, `state`).
- Endpoints: `/api/crons`, `/api/crons/create`, `/api/crons/update`,
  `/api/crons/delete`, `/api/crons/run`, `/api/crons/pause`, `/api/crons/resume`,
  `/api/crons/status`, `/api/crons/history`, `/api/crons/delivery-options`.
- Persistence: `hermes/data/crons/jobs.json` (atomic + `asyncio.Lock`).
  Background loop started in `create_app` startup; stops on shutdown.

### hermes/llm.py (streaming support, NEW 2026-06-07)
- `LLMRouter.stream_chat(...)` and `collect_stream(...)` — async generator over
  provider chunks. Providers: `OpenAIProvider.stream()` (covers OpenAI,
  llama-server, MiniMax via OpenAI-compat), `MockProvider.stream()`.
- Used by `/api/chat/start` → `asyncio.Queue` → `/api/chat/stream/{id}` SSE.

### bin/hermes-model-run.{bat,ps1} (NEW 2026-06-07)
- **Purpose**: dedicated real-time viewer for the llama-server backend
  (model load progress, offload decisions, HTTP request lines, prompt-eval
  / generation timing, errors). Window title: "Hermes Model Running".
- Tails `hermes/data/logs/llm-server.log` + `llm-server.err` (written by
  `start-llm.ps1` via `RedirectStandardOutput` / `RedirectStandardError`).
- Smart color highlighting: magenta for model load, green for "HTTP server
  listening", yellow for eval time / tokens-per-second, red for errors,
  cyan for HTTP request lines, dark-yellow for warnings.
- 400ms polling loop, file-locked-safe (skip round on lock error).
- Boot banner + initial 5-line tail dump of each file.
- `hermes-all.bat` step 7 launches it; `hermes-stop.bat` step 5 kills its
  powershell process and step 6 closes the cmd window by title match.
- **Prerequisite**: `start-llm.ps1` no longer passes `--log-disable` (was
  silencing everything). With it removed, llama-server streams its internal
  log to stdout, which gets redirected to `llm-server.log` for the viewer
  to tail.

---

## 5. Key Decisions & Why

| Decision                                 | Why                                                         |
|------------------------------------------|-------------------------------------------------------------|
| Hermes WebUI (nesquena) as main UI       | Mature three-panel dark UI, no Node.js build, 16 theme skins, streaming markdown |
| `--alias qwen2.5-3b-instruct`            | Avoid filename-based model id mismatch between GGUF and WebUI |
| Hash-based embeddings (RAG shim)         | Avoid downloading 100MB+ embedding model just to boot RAG   |
| One launcher `hermes-all.bat`            | User experience: one double-click = everything             |
| Smart NGL (auto offload calculation)      | Support loading models larger than VRAM (e.g. 22GB on 8GB) |
| Bundle all llama.cpp variants             | Portable — works on any GPU (NVIDIA/AMD/Intel)             |
| Skip CPU when VRAM full of weights       | Hybrid offload with <5 layers = full CPU is faster         |
| CRLF line endings for all .bat files      | cmd.exe does NOT parse LF-only files (bug: truncates paths)  |
| client-side api-adapter.js                | Translate upstream WebUI's endpoints onto ours — no need to fork upstream Python BFF |

---

## 6. Multi-Model Loading

llama-server is **single-model per process**. Three options:

1. **Switch model** — kill llama-server, restart with different `--model`:
   ```bat
   set MODEL=E:/Hermes Agent/data/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf
   bin\hermes-all.bat
   ```

2. **Multiple llama-server instances** on different ports (8080, 8081, 8082),
   switch the WebUI's model picker to point at the desired one. Resource-hungry
   but lets you hot-swap.

3. **Ollama-compatible import** — run `python hermes/scripts/import_ollama_blobs.py`
   to convert Ollama `sha256-XXXXX` blobs to `.gguf` files in `data\models\`.

For the 22.8GB `f5ee307a2982.gguf` (qwen3) on 8GB VRAM, smart NGL
calculates ~16 layers on GPU + rest on CPU. Works, but slow.

---

## 7. Common Gotchas (READ THIS BEFORE EDITING!)

### Windows / cmd.exe
- **CRLF for .bat files!** LF-only → cmd can't parse → paths with spaces
  get truncated, scripts fail silently. **Always run:**
  ```powershell
  $c = Get-Content file.bat -Raw
  [System.IO.File]::WriteAllText(file.bat, $c -replace "`r`n","`n" -replace "`n","`r`n", [System.Text.UTF8Encoding]::new($false))
  ```
  After every bat edit, verify: `CR=NN, LF=NN` (must be equal).

- **`cmd /c "path with space"`** — truncates at the space. Workarounds:
  - `cmd /c "bat.bat" arg` (bat is relative, run from its dir)
  - Wrap the whole command in outer quotes
  - Or invoke from a wrapper bat

- **`for /f "tokens=*" %%V in ('cmd with --flag=value,flag2')`** — the comma
  breaks the parser. Use `usebackq` + backticks:
  ```bat
  for /f "usebackq tokens=*" %%V in (`cmd --flag=value`) do ...
  ```
  Or wrap in PowerShell to avoid cmd parsing entirely.

- **`set /a` is 32-bit signed integer.** For files > 2GB, use PowerShell:
  ```bat
  for /f "tokens=*" %%S in (`powershell -NoProfile -Command "$f=(Get-Item -LiteralPath '%FILE%').Length; [int][math]::Floor($f/1MB)"`) do set "MB=%%S"
  ```

### Hermes WebUI (nesquena upstream)
- **`__WEBUI_VERSION__`, `__MAX_UPLOAD_BYTES__`, `__CSRF_TOKEN_JSON__` placeholders
  in `index.html` are filled in by server.py** at request time. If you copy the
  static dir to a different web server, do the substitution yourself or the
  bootstrap script will 404 on the versioned asset URLs.
- **Adapter must load BEFORE `pwa-startup.js`** — `<script src="api-adapter.js">`
  in `<head>` is non-negotiable; the wrapper needs to be in place before any other
  script calls `fetch`.
- **`/v1/models` resolution order** — first try `http://127.0.0.1:8080/v1/models`
  (live proxy), then `data/models/*.gguf` scan via `hermes.gguf.list_gguf_models()`,
  then empty. The WebUI uses this list as the model dropdown — if llama-server is
  down, you see filename stems like `Qwen2.5-7B-Instruct-Q4_K_M`. To use one, your
  `hermes.yaml` `llm.router.providers.local.alias` must match.
- **Hermes Agent upstream is `nousresearch/hermes-agent`** — the WebUI was built
  for that. Our adapter translates the ~25 endpoints it actually hits on boot/chat;
  everything else (workspaces, kanban, crons, voice, OAuth, etc.) is no-op'd. Don't
  expect those panels to do anything useful.

### llama-server
- **Model id from `/v1/models` defaults to the filename** (ugly).
  Use `--alias clean-name` to override.

- **Single model per process.** To switch, restart with different `--model`.

### hermes (Python)
- **Config env expansion**: `os.path.expandvars` doesn't support bash
  `${VAR:-default}` syntax. `hermes/config.py` has custom regex
  `_ENV_VAR_RE` for this — don't replace with plain `expandvars`.

- **`.env` loading**: config.py searches in cwd, parent, then `hermes/`
  package parent dir (absolute path). Works regardless of cwd.

---

## 8. Modification Log (chronological)

| When        | Change                                                              |
|-------------|---------------------------------------------------------------------|
| Day 1       | Built hermes package: config, llm router, memory, KB, skills, server |
| Day 1       | Embedded portable-python 3.12.10 + llama.cpp CPU b9503 + 3 GGUF     |
| Day 1       | Built React admin SPA → `hermes/web_dist` (now deprecated)           |
| Day 1       | Wrote `bin\hermes-all.bat` v1 (vulnerable to path issues)            |
| Day 2       | Fixed hermes-all.bat CRLF issue (was LF-only, paths truncated)        |
| Day 2       | Added `--alias qwen2.5-3b-instruct` to llama-server                  |
| Day 2       | Integrated Open WebUI 0.9.6: install + bootstrap + config            |
| Day 2       | Added `/v1/embeddings` shim in hermes/server.py (hash vectors)        |
| Day 2       | Discovered: OW shows system Ollama models (we want only ours)        |
| Day 3       | Fixed: `ENABLE_OLLAMA_API=false` in hermes-all.bat + start-openwebui.bat |
| Day 3       | Created `hermes/scripts/bootstrap_openwebui.py` (auto signup + add model) |
| Day 3       | Created `setup-runtime.bat` (download ALL llama.cpp variants, aria2 16-thread) |
| Day 3       | Added `start-llm-smart.bat` (auto NGL based on model size + VRAM)    |
| Day 3       | Imported user's Ollama blob (qwen3 22.8GB) → `f5ee307a2982.gguf`    |
| Day 3       | Project cleanup: trashed 1-off test scripts, kept 2 functional tests |
| Day 3       | THIS FILE created                                                 |
| 2026-06-06  | **llama.cpp b9503 → b9538 upgrade** (Qwen3 MoE / Qwen3.5 MoE support) |
| 2026-06-06  | Cleaned 22.29GB `Qwen3.6.incompatible-b9503.gguf` (model+llama.cpp upgrade obsoletes) |
| 2026-06-06  | Trashed `hermes/web_dist/` (React admin SPA) — server.py uses HTML_FALLBACK now |
| 2026-06-06  | Trashed `scripts/` (6/3 legacy) — bin/ replaced all |
| 2026-06-06  | Switched default model: `Qwen2.5-7B-Instruct` → `Qwen3.5-35B-A3B-Q4_K_M` (20.5GB MoE) |
| 2026-06-06  | Verified Qwen3.5-35B-A3B works: n_params=34.66B, n_ctx_train=262144, chat "Hello! How can I assist you today?" OK |
| 2026-06-06  | Studied ComfyUI-aki-v3 for inspiration (see §13 Roadmap below)        |
| 2026-06-06  | **A**: `bin/hermes-models.py` CLI 多模型切换器 (list/switch/download/gopeed) — parses GGUF v3 header (arch, ctx_len, n_tensors) |
| 2026-06-06  | **A**: `hermes/gguf.py` module — extracted GGUF v2/v3 header parser, reused by CLI + web UI |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher` page — web UI for model switching (replaces deprecated `web_dist/` admin SPA) |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher/switch` (POST) — async subprocess runs `switch-model.bat`, returns when done |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher/download` (POST) — creates gopeed-web task via Python communication bridge |
| 2026-06-06  | **A**: Integrated `gopeed-web` (89MB single exe) into `runtime/gopeed-web.exe` as the Python communication bridge for downloads |
| 2026-06-06  | **B**: `hermes/firstrun.py` + `bin/hermes-firstrun.bat` — detects NVIDIA/AMD/Vulkan, downloads cudart via gopeed-web if missing, graceful CPU fallback |
| 2026-06-06  | **B**: `hermes-firstrun.bat` wired into `hermes-all.bat` as Step 0 (idempotent check, doesn't block startup) |
| 2026-06-06  | **C**: `hermes/doctor.py` + `bin/hermes-doctor.bat` — 8-section health report (runtime, models, GPU, services, gopeed, python, disk, env) |
| 2026-06-06  | **D**: `hermes/gopeed_client.py` — gopeed-web API client (urllib only, no deps). gopeed-web API differs from desktop gopeed (POST body wrapped in `req`, response `data` is task_id string, opts at `meta.opts`) |
| 2026-06-06  | Memory: 120s bash timeout, gopeed+file-lock download check, gopeed-web API quirks, GGUF v3 type table |
| 2026-06-06  | **CRITICAL BUGFIX**: `hermes-stop.bat` v1 used `taskkill /IM llama-server.exe` literal — but the actual binary is `llama-server-cuda-12.4.exe`. Old stop left **stale llama-server processes holding VRAM** (one PID survived 20+ hours, working set -1140MB = leaked kernel handles). v2 fix: use `llama-server*` wildcard + PowerShell-based kill for clean output. |
| 2026-06-06  | Also fixed: all `bin\*.bat` files were **LF-only** (Edit tool had stripped CRs), causing cmd.exe to mis-parse multi-line `powershell -Command` blocks (visible as random "X 不是内部或外部命令" noise). Restored CRLF on all 9 bat files.
| 2026-06-07  | **Track 1: streaming-and-sessions** — `hermes/sessions.py` (SessionStore: one JSON per session, atomic write + asyncio.Lock) + `hermes/llm.py` (`stream()` on OpenAI/Mock, `stream_chat`/`collect_stream` on router) + `hermes/server.py` (`/api/chat/start`, `/api/chat/stream/{id}` SSE, `/api/chat/cancel`, `/api/chat/stream/status`, persistent `/api/chat/sessions{,/{id}}`, legacy `/api/chat/send` kept) + `hermes/static/api-adapter.js` (removed EventSource mock, chat/start is passthrough, cancel/status forward to real endpoints). SSE event shape: `{type: starting|delta|done|error|replay, content?, stream_id, session_id, model, provider, ...}`. Persistence path: `data/sessions/<session_id>.json`. Owner had to move the catch-all `@app.api_route('/api/{path:path}')` to the very last position in `create_app` because FastAPI matches routes in registration order; added a multi-line warning comment. |
| 2026-06-07  | **Track 2: workspace-browser** — `hermes/workspace.py` (WorkspaceManager: HERMES_ROOT trust boundary, case-insensitive whitelist `data/{knowledge,memory,models,skills,logs}` + `docs` + `tests` + root files `README.md`/`AGENTS.md`, path-traversal defense via `Path.resolve()` + `is_relative_to()` + Windows `normcase`, binary sniff for `read_file`, mime for media, atomic JSON persistence). Added 6 endpoints to `server.py`: `GET/POST /api/workspaces{,/add,/remove}`, `GET /api/list`, `GET /api/file`, `GET /api/media`. `api-adapter.js` updated: removed noop transforms, added `dropParams` route field, made workspaces/list/file/media passthrough. Persisted at `data/workspaces.json`. |
| 2026-06-07  | **Track 3: settings-persistence** — `hermes/webui_settings.py` (WebUISettingsStore + DEFAULT_SETTINGS with 32 keys + 1-level nested deep-merge + asyncio.Lock + atomic write). Replaced server's `GET/POST /api/webui/settings` noop handlers with real ones; the store singleton is instantiated in `create_app`. `api-adapter.js` simplified: removed the hardcoded 32-key default on `/api/settings` GET, both GET and POST are now passthrough. Persisted at `data/webui_settings.json`. |
| 2026-06-07  | **Track 4: kanban-board** — `hermes/kanban.py` (KanbanStore: Board+Task models, atomic JSON writes for boards/tasks/events, asyncio.Lock, capped events log at 2000 entries, CSS-safe color sanitizer, default board + 5 sample tasks bootstrap, board switcher pointer). 22 endpoints registered in `server.py` between `/api/webui/noop` and the workspace block (all 4 HTTP methods on `boards/{slug}` and `tasks/{id}` so spec's PUT and UI's PATCH both work). `api-adapter.js` v0.5: 14 explicit kanban passthrough routes using `url:null+passthrough:true`. SSE/dispatch/comments/worktree are intentional noop per spec (UI falls back to 30s polling). Persisted at `data/kanban/{boards,tasks,events}.json`. |
| 2026-06-07  | **Track 5: cron-scheduler** — `hermes/cron.py` (CronManager + Job dataclass + atomic JSON persistence + 30s background scan loop + shell/task/webhook runners + UI-shape serializers). Started in `create_app` startup, stopped on shutdown. 10 endpoints registered BEFORE the `/api/{path:path}` catch-all: list/create/update/delete/run/pause/resume/status/history/run+filename/delivery-options. Action types: `shell` (subprocess), `task` (agent.run_task), `webhook` (POST). `api-adapter.js`: replaced 2 crons noop entries with passthrough; fixed fetch wrapper to use original URL when `route.url` is null. `requirements.txt`: added `croniter==6.0.0`. Persisted at `data/crons/jobs.json`. |
| 2026-06-07  | **Track 6: final-integration** — All 5 tracks verified end-to-end on a live mock-mode server (port 7860). 13 GET/POST endpoints all 200; SSE stream produced 53+ chunks; settings (`theme=sepia`, `display.streaming=false`) + session `e2e-test-session` (5 messages) + kanban default board (6 tasks) all survived `Stop-Process` + restart. Kanban CRUD roundtrip (POST→GET→PATCH→DELETE) verified. Cron CRUD roundtrip (create→list→run→delete) verified. `bin\*.bat` CRLF audit: 9/13 CRLF-OK, 4 still LF-only (`gpu-detect.bat`, `hermes.bat`, `model-manager.bat`, `verify-server.bat`) — pre-existing issue, not introduced by these tracks. AGENTS.md §3/§4/§8/§12/§13 updated; README.md mentions new WebUI features. Full e2e transcript in `deliverable-final.md`.
| 2026-06-07  | **Hermes WebUI merge (nesquena/hermes-webui)**: replaced the in-house single-file `chat_ui.py` with the upstream three-panel dark UI. Source: `D:\PZS0X\下载\hermes-webui-master\hermes-webui-master\static\` (3.5MB: 18 vanilla-JS files + 366KB CSS with 16 skins + vendored streaming-markdown + KaTeX). Copied to `hermes/static/`, new UI served at `/`; old `chat_ui.py` kept as `/chat` fallback. Adaptation: `hermes/static/api-adapter.js` (23KB) wraps `window.fetch` + `EventSource` to translate the new UI's ~25 expected endpoints onto our existing `/api/chat/*` + `/v1/*` backends; missing endpoints (workspaces, kanban, crons, etc.) return sane empty defaults so the UI continues to boot. server.py: `GET /` serves `hermes/static/index.html` with `__WEBUI_VERSION__` / `__MAX_UPLOAD_BYTES__` / `__CSRF_TOKEN_JSON__` placeholder substitution; added 11 new `/api/webui/*` stub endpoints; added `app.mount("/static", StaticFiles(...))` for the new asset tree. **Two bugs hit and fixed during integration**: (1) `/api/webui/*` registered AFTER the catch-all `/api/{path:path}` got swallowed — moved all webui routes before the catch-all; (2) duplicate `@app.get("/")` returned the legacy DASHBOARD_HTML — removed the old one. **Knowingly not implemented** (UI may show empty panels / "no data" / disabled features): streaming Markdown renders but no real token-by-token SSE (we block on /api/chat/send and emit one fake delta — works but not live), workspaces/file browser, kanban boards, cron jobs, projects, memory editor, voice, OAuth/passkeys, multi-profile, web terminal. Adding these is straightforward but out of scope for v0.1.
| 2026-06-07  | **Full cutover to Hermes WebUI**: deleted `hermes/chat_ui.py` (336 lines), `bin/hermes-web.bat`, `data/openwebui/` (residual data), and `docker/` (unused). Removed `GET /chat` endpoint, `DASHBOARD_HTML` constant, and the `DASHBOARD_HTML` fallback from `GET /` and `GET /health` — both now 503 if `hermes/static/index.html` is missing (install is broken). `bin/hermes-all.bat` v8: title says "WebUI mode", opens `http://localhost:7860/` (not `/chat`), drops the legacy `:7870` line. **LLM model dropdown fix**: rewrote `GET /v1/models` to **live-proxy** `http://127.0.0.1:8080/v1/models` when llama-server is up (so the UI sees the exact `--alias` model llama-server has loaded), then fall back to scanning `data/models/*.gguf` via `hermes/gguf.py` and exposing filename stems as model ids (matches llama-server's default alias), then empty list. Response now includes `_size_gb` / `_arch` / `_ctx_len` / `_quant` / `_filename` extras on each model entry (the adapter surfaces them in the WebUI's tooltip). Updated AGENTS.md §1, §2, §3, §4, §5, §7, §9, §10, §12 to remove all Open WebUI references and reflect the new architecture (2 processes instead of 3, WebUI at :7860, no `:7870`).
| 2026-06-07  | **Hermes Model Running window**: new persistent `bin/hermes-model-run.bat` + `.ps1` that tails `data/logs/llm-server.log` + `llm-server.err` with smart color highlighting (model load magenta, HTTP requests cyan, eval-time yellow, errors red). User asked for a way to "see what the LLM is doing" — previously `llm-server.log` was 0 bytes because `start-llm.ps1` passed `--log-disable`. Removed that flag so llama-server now streams its internal log to stdout, which gets redirected to the log file. Step 7 of `hermes-all.bat` launches the window; `hermes-stop.bat` step 5 kills the powershell and step 6 closes the cmd window by title match. Title set via `$Host.UI.RawUI.WindowTitle` with try-catch guard against host-less invocations. Initial 5-line tail dump at boot, 400ms polling loop, file-locked-safe. CRLF verified on both new files. AGENTS.md §3, §4, §8 updated.
| 2026-06-08  | **Hermes Web UI Integration (EKKOLearnAI)**: integrated [hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui) as the main web interface. Source: `data/webui-new/app/` (Vue 3 + Koa + Socket.IO). New launcher: `bin/webui-new.bat` with environment setup for portable Python (`HERMES_AGENT_BRIDGE_PYTHON`), data isolation (`HERMES_WEB_UI_HOME`, `HERMES_HOME`), and gateway disable (`HERMES_WEB_UI_DISABLE_GATEWAY_AUTOSTART=1`). Updated `hermes-all.bat` to start Web UI at :8648. **Compatibility fixes**: (1) Python bridge path injection for portable Python; (2) Data directory isolation to `data/webui-new/data/` and `data/hermes-agent/`; (3) Auto-generated `hermes-agent/config.yaml` with llama-local provider pointing to `http://127.0.0.1:8080/v1`; (4) Node.js dependency on system PATH (Web UI requires Node.js). Architecture now has 3 processes: llama-server (:8080), Hermes FastAPI (:7860), Hermes Web UI (:8648). README.md rewritten with acknowledgment to EKKOLearnAI/hermes-web-ui project. AGENTS.md §1/§2/§3/§4 updated.
| 2026-06-08  | **Skill installation for Web UI**: 9 skills installed to `data/hermes-agent/skills/` across 4 categories — 4 from upstream `optional-skills/finance/` (excel-author, pptx-author, comps-analysis, dcf-model) + 4 from community GitHub via `git clone` (drawio-skill from `Agents365-ai/`, hermes-dojo from `Yonkoo11/`, avoid-ai-writing from `conorbronsdon/`, plur-memory + plur-session-end from `plur-ai/plur`). Config change: `data/hermes-agent/config.yaml` `toolsets:` list extended with `skills` (was `[hermes-cli]` only) — required to load the upstream `skills` toolset. **Process note**: `hermes skills install <name> --force` is rate-limited by GitHub API (60 req/hr unauthenticated) so we used `git clone --depth=1` as the rate-limit-free fallback. 2 short names (`research-agent`, `multiagent`) not present in upstream source tree and were skipped pending a specific source URL from the user. See §11 for the full list. AGENTS.md §3/§8/§11 updated.
| 2026-06-08  | **Built-in skill install (round 2)**: copied 5 built-in skills from `E:\Hermes Agent\hermes-agent-source\skills\` to `data/hermes-agent/skills/` — `productivity/{powerpoint, ocr-and-documents, nano-pdf, google-workspace}` and `creative/claude-design`. Total now 14 active skills across 4 categories (finance 4, productivity 6, creative 3, autonomous-ai-agents 1). User confirmed trust = same-source GitHub repo so no security scan. AGENTS.md §3/§15 updated.

---

## 9. Setup Flow (clean install from scratch)

```bash
# 1. Download all llama.cpp variants + aria2 (one-time, ~280MB)
bin\setup-runtime.bat

# 2. Run it
bin\hermes-all.bat
# → browser opens at http://localhost:7860/  (the new Hermes WebUI)
# → WebUI is unauthenticated by default; data lives in agent memory
# → chat: type a message, pick a model from the dropdown (auto-populated
#   from llama-server at :8080 if up, else scanned from data/models/*.gguf)
```

To switch default model, edit `hermes-all.bat` line `set "MODEL=..."` (line ~13).

To install a different GGUF, drop it in `data\models\`, then either:
- Restart `bin\hermes-all.bat` after editing the MODEL line, OR
- Use Ollama's `import_ollama_blobs.py` to convert `sha256-XXXX` blobs

---

## 10. Debugging

### Log files
- `hermes\data\logs\hermes.log` — Hermes FastAPI + bootstrap.log
- Each launcher writes to its own window (visible in title bar)
- Browser DevTools Network tab shows the adapter's URL translations live

### Common issues
| Symptom                                  | Cause / Fix                                |
|------------------------------------------|--------------------------------------------|
| bat flashes and exits                     | LF line endings → convert to CRLF          |
| `'E:\Hermes' is not recognized`           | Space in path + bad cmd /c invocation      |
| llama-server OOM                          | Model > VRAM → NGL=0 (CPU only)            |
| WebUI model dropdown empty                | llama-server down + no GGUF in `data/models/` |
| WebUI "Model '' was not found"            | Model id mismatch → check llama-server `--alias` matches what's in `hermes.yaml` `llm.router.providers.local` |
| `MiniMax`/cloud "invalid api key"         | API key not activated on provider platform |
| WebUI stuck on "Loading..." forever       | `api-adapter.js` not loaded → check Network tab for /api/webui/* 404s |

### Reset to clean state
```bash
# Wipe Hermes in-memory chat session cache (only sessions created in this process lifetime)
"E:\Hermes Agent\portable-python\python.exe" -c "from hermes.agent import HermesAgent; from hermes.config import load_config; a = HermesAgent(load_config(), use_mock=True); a._chat_sessions.clear(); print('cleared')"

# Run E2E test (no GPU needed)
"E:\Hermes Agent\portable-python\python.exe" "E:\Hermes Agent\tests\test_hermes.py"

# Verify NGL math
"E:\Hermes Agent\portable-python\python.exe" "E:\Hermes Agent\tests\verify_smart_ngl.py"

# Verify GGUF scan works
"E:\Hermes Agent\portable-python\python.exe" -c "from hermes.gguf import list_gguf_models; from pathlib import Path; import json; print(json.dumps(list_gguf_models(Path('E:/Hermes Agent/data/models')), indent=2, default=str))"
```

### Verify GPU is actually used
Open a separate terminal:
```bash
nvidia-smi
```
Look for `python.exe` or `llama-server.exe` row → check **GPU-Util** column.
If 0% → CPU mode, no GPU offload.

---

## 11. Testing

| Test                          | Purpose                                      | When to run        |
|-------------------------------|----------------------------------------------|---------------------|
| `tests\test_hermes.py`        | 17 E2E checks (mock LLM, no GPU)            | After major changes |
| `tests\verify_smart_ngl.py`  | Verify NGL math for all models               | After bat changes   |
| `bin\hermes-all.bat` e2e      | Real LLM full pipeline                       | Before commits      |

`test_hermes.py` uses `HERMES_LLM_MOCK=1` so it runs without GPU/LLM.

---

## 12. Known Limitations / TODO

- **GPU is RTX 3070 8GB** — fits 7B Q4_K_M, partial offload for 22GB qwen3
- **MiniMax API key not activated** — returns 2049 invalid
- **llama-server is single-model** — multi-model needs multi-instance
- **Hash embeddings are placeholders** — RAG quality is poor (until user runs `bin\install-embeddings.bat`)
- **WebUI streaming is now real (NEW 2026-06-07)** — `hermes/llm.py` `stream()` + `/api/chat/start` + `/api/chat/stream/{id}` SSE delivers per-chunk JSON `{type, content}` events. Adapter's old EventSource mock removed.
- **WebUI panels for workspaces / kanban / crons are real (NEW 2026-06-07)** — `hermes/workspace.py`, `hermes/kanban.py`, `hermes/cron.py` power them. See §4.
- **Kanban SSE / dispatch / comments / worktree are noop stubs** — UI falls back to 30s polling on `/api/kanban/events`. Real-time event push and agent dispatch are TODO.
- **Cron `/api/crons/pause` and `/resume` returned 400 in one harness test** — body schema mismatch; the endpoints are registered and respond 200 from the WebUI. Unverified whether the harness body was the issue; e2e-step3 in deliverable.md has the full request/response.
- **Auth is off** — Hermes WebUI has no login screen. Don't expose :7860 to the internet.
## 13. Roadmap: 1+2+4 Plan (in progress)

User confirmed priorities: **4 (KB) → 1 (embeddings) → 2A (autonomous tasks)** + native skill marketplace.

### ✅ 4. Knowledge Base management — DONE
- `hermes/scripts/rebuild_kb.py` — wipes `index.jsonl` + `sources/`, re-ingests `data/knowledge/*.md` with sane limits
- Per-doc cap: `--max-chunks 1000`
- Result: 256k bloated chunks → 13 clean chunks (5 files, all with embeddings)
- Runtime add: planned (TODO: `hermes kb add <path>` CLI)

### ⚙️ 1. Real embeddings — FRAMEWORK DONE, MODEL OPTIONAL
- `hermes/embeddings.py` — `SBERTEmbedder` (sentence-transformers) + `HashEmbedderFallback`
- `hermes/server.py` `/v1/embeddings` uses the new factory; auto-falls back to hash
- `bin\install-embeddings.bat` — installs sentence-transformers + downloads all-MiniLM-L6-v2 (~330MB)
  - **Not run yet** — user's internet is 137KB/s, big downloads are slow
  - When user has fast internet: `bin\install-embeddings.bat` (interactive, asks confirm)
  - Sets `HERMES_EMBEDDER=auto` (default) — uses sbert if installed, hash if not

### ✅ 2A. Autonomous task execution — DONE
- `hermes/planner.py` — `Planner` class with `TaskStep` / `TaskResult` dataclasses
- Loop: LLM generates JSON plan → execute skills one by one → on failure, replan → summarize
- CLI: `hermes task "<goal>" --mock --json` (use `--mock` to test without LLM)
- HTTP: `POST /api/task` (sync or async with task_id polling via `GET /api/task/{id}`)
- `hermes agent.run_task(goal)` method wraps the planner
- Constants: `MAX_REPLANS=3`, `MAX_STEPS=20` (prevent runaway)
- 17/17 tests pass (planner tested with mock)
- Real LLM test still pending (user needs to run with `bin\start-llm-smart.bat` first)
- Wrapper: `bin\hermes-task.bat "<goal>"` for one-liner use
- Health probe: `GET /health` returns `{status, version, cloud_available, local_available, mode}`

Original sketch (from planning):
```python
async def plan_and_execute(self, goal: str) -> str:
    plan = await self.llm.plan(goal, available_skills=self.skills.list())
    for step in plan:
        try:
            result = await self._execute_step(step)
        except Exception as e:
            plan = await self.llm.replan(goal, plan, step, e)
    return summary
```

Actual implementation lives in `hermes/planner.py`, much richer (replan on step failure, error recovery, JSON parsing with tolerance for non-strict LLM output).

### ✅ Skill marketplace — FRAMEWORK DONE
- `hermes/scripts/install_skill.py`:
  - `list` — show installed + registry
  - `install <name|url>` — download + verify SHA256 + safety check
  - `remove <name>` — uninstall
  - `publish <name> <url> --sha ... --desc ...` — add to registry
- Registry: `hermes/data/skills/registry.json` (JSON list of {name, url, sha256, desc})
- User can curate the registry themselves (or set up a public GitHub repo)



---


### ✅ 2026-06-07 — 6-track parallel integration — DONE
- **Track 1 streaming-and-sessions** (owner commit c93cb6b): real SSE via `hermes/llm.py stream()` + `hermes/sessions.py SessionStore` (atomic JSON, asyncio.Lock, one file per session at `data/sessions/<sid>.json`). Endpoints: `/api/chat/start` (returns `{stream_id, session_id, effective_model, effective_model_provider}`), `/api/chat/stream/{id}` (SSE `data: {type,content,...}`), `/api/chat/cancel`, `/api/chat/stream/status`, persistent `GET/PATCH/DELETE /api/chat/sessions{,/{id}}`. Adapter's old EventSource mock removed.
- **Track 2 workspace-browser**: `hermes/workspace.py` (HERMES_ROOT trust boundary, whitelist-gated file browser, path-traversal defense, atomic JSON). Endpoints: `/api/workspaces{,/add,/remove}`, `/api/list`, `/api/file`, `/api/media`. Persisted at `data/workspaces.json`.
- **Track 3 settings-persistence**: `hermes/webui_settings.py` (32-key DEFAULT_SETTINGS + 1-level nested deep-merge + atomic write). `GET/POST /api/webui/settings` is now real (was noop). Persisted at `data/webui_settings.json`.
- **Track 4 kanban-board**: `hermes/kanban.py` (KanbanStore: boards/tasks/events with atomic JSON, default board + 5 sample tasks bootstrap, 2000-event cap, CSS-safe color sanitizer). 22 endpoints registered. SSE/dispatch/comments/worktree are noop stubs (UI falls back to 30s polling). Persisted at `data/kanban/{boards,tasks,events}.json`.
- **Track 5 cron-scheduler**: `hermes/cron.py` (CronManager + Job dataclass + croniter + 30s background loop + shell/task/webhook action runners + UI-shape serializers). 10 endpoints. Started in `create_app` startup, stops on shutdown. `requirements.txt` + `croniter==6.0.0`. Persisted at `data/crons/jobs.json`.
- **Track 6 final-integration** (this task): 13 endpoints all 200 on a live mock-mode server; SSE 53+ chunks; settings `theme=sepia` + `display.streaming=false` survive `Stop-Process` + restart; session `e2e-test-session` (5 msgs) survives; kanban default board (6 tasks) survives; kanban CRUD + cron CRUD roundtrips verified. AGENTS.md §3/§4/§8/§12/§13 updated. Full transcript in `deliverable-final.md`.
- **Architecture now has 6 new modules** + 4 new data dirs/files (all in §3). Adapter grew from `~25 mapped + ~30 noop` to `~75 mapped, 0 noop` (the upstream WebUI's workspaces/kanban/crons/etc. panels are now real).

### ✅ WebUI panels: workspaces / kanban / crons — REAL
Previously `§12 Known Limitations` listed these as no-op. They are now backed by real modules (see §4 and the 6-track entry above). Adapter's noop transforms for these routes are gone.

### ⚠️ Real-time push + agent dispatch are still noop
- Kanban: SSE / dispatch / comments / worktree endpoints are stubs (UI uses 30s polling on `/api/kanban/events`).
- Crons: action runners are real; but no streaming/notification back to the WebUI.
- Next: WebSocket / SSE upgrade for kanban + cron status.

### Skill marketplace — unchanged
Still framework-only (`hermes/scripts/install_skill.py`); no marketplace backend yet.

## 14. Conversation Reference

This project was built across one long session on 2026-06-04/05. Key
turns (in Mavis conversation memory if picked up later):
- Built portable framework (Day 1)
- Fixed hermes-all.bat CRLF bug
- Integrated Open WebUI (overcame: missing __main__, RAG embedder,
  model id mismatch, Ollama auto-detect)
- Smart NGL launcher (overcame: 32-bit int overflow, nvidia-smi in for/f)
- Memory bank + cleanup (this file)

---

## 15. Installed Skills (NEW 2026-06-08)

14 skills live at `data/hermes-agent/skills/`, organized by upstream category.
Loaded into the Web UI agent via the `skills` toolset (see `config.yaml` `toolsets:`).

### From upstream `optional-skills/finance/` (copy)

| Skill | Purpose | Has `pip` deps in SKILL.md |
|---|---|---|
| `finance/excel-author` | Build .xlsx with named ranges + formula audit trail | `openpyxl>=3.0` |
| `finance/pptx-author` | Build .pptx with presentation design conventions | `python-pptx>=0.6` |
| `finance/comps-analysis` | Comparable Company Analysis (Excel model) | `openpyxl` |
| `finance/dcf-model` | DCF discounted cash flow model (Excel) | `openpyxl` |

### From upstream `skills/` built-in tree (copy, round 2)

| Skill | Purpose | Notes |
|---|---|---|
| `productivity/powerpoint` | Build .pptx with comprehensive conventions (1MB, biggest skill) | Full templates + slide-design rules |
| `productivity/ocr-and-documents` | OCR scanned PDFs / extract text + tables from images | Likely tesseract-based |
| `productivity/nano-pdf` | Lightweight PDF reading / extraction | Smallest skill (1.4 KB) |
| `productivity/google-workspace` | Google Docs / Sheets / Slides integration | 83 KB, requires `gcloud` auth |
| `creative/claude-design` | Visual design system + mockup conventions | 20 KB |

### From community GitHub repos (`git clone --depth=1`)

| Skill | Source | Purpose |
|---|---|---|
| `creative/drawio-skill` | `Agents365-ai/drawio-skill` | drawio diagram generation (flowcharts / architecture / ER / UML) |
| `creative/avoid-ai-writing` | `conorbronsdon/avoid-ai-writing` | Audit + rewrite text to strip AI-isms (multiple voice profiles) |
| `productivity/plur-memory` | `plur-ai/plur` (`skills/plur-memory/`) | Persistent engram memory across sessions |
| `productivity/plur-session-end` | `plur-ai/plur` (`skills/plur-session-end/`) | Extract durable learnings at session end |
| `autonomous-ai-agents/hermes-dojo` | `Yonkoo11/hermes-dojo` | Self-improvement system — analyzes past sessions, auto-patches skills |

### How they get discovered

- Web UI agent spawn-time: scans `~/.hermes/skills/` (= `E:\Hermes Agent\data\hermes-agent\skills/`)
- Each category dir has subdirs, each subdir has `SKILL.md` with `name:` frontmatter
- Slash commands (`/excel-author`, `/drawio-skill`, etc.) auto-injected as user messages (not system prompt) — preserves prompt caching
- Toolset filter: agent must have `skills` toolset enabled in `config.yaml` `toolsets:` list

### Install method (rate-limit-free)

`hermes skills install <name> --force` would route through `unified_search` → GitHub API (60 req/hr). To avoid burning the rate limit we used direct `git clone --depth=1` of each `user/repo` into the staging dir, then `shutil.copytree` into `data/hermes-agent/skills/<category>/<name>/`. For built-ins we just `shutil.copytree` from `E:\Hermes Agent\hermes-agent-source\skills/<cat>/<name>/` directly. The Web UI's skill scanner doesn't care about provenance, only the directory structure.

### Skipped (2 of 6 user-requested, from round 1)

- `research-agent` — not in upstream `skills/` or `optional-skills/`; needs a specific GitHub source URL from the user
- `multiagent` — same; closest upstream skill is `skills/research/research-paper-writing` but that's not the same thing

### To install more later

```bash
# Method 1: upstream catalog (uses GitHub API, may rate-limit)
"E:\Hermes Agent\portable-python\python.exe" "E:\Hermes Agent\hermes-agent-source\hermes_cli\skills_hub.py" install <name> --force

# Method 2: direct git clone (no rate limit) — for community skills
# 1. git clone --depth=1 https://github.com/<user>/<repo>.git to scratchpad
# 2. find the canonical SKILL.md (root or skills/<name>/)
# 3. shutil.copytree to data/hermes-agent/skills/<category>/<skill-name>/
# 4. restart Web UI

# Method 3: copy built-in (no network needed)
xcopy /E /I "E:\Hermes Agent\hermes-agent-source\skills\<cat>\<name>" "E:\Hermes Agent\data\hermes-agent\skills\<cat>\<name>"
```

