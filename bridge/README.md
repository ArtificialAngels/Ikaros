# Hermes Bridge

The **bridge** is the thin glue layer between the two upstream dependencies
(`hermes-agent` + `hermes-web-ui`) and this project's portable deployment.

## Why it exists

`hermes-agent` (NousResearch, v0.16.0) ships with its own complete agent
runtime — `AIAgent` class, toolsets, plugins, gateway, dashboard, TUI, ACP
adapter, cron, kanban. We do **not** fork it. We use it as a library.

`hermes-web-ui` (EKKOLearnAI, v0.6.12) is a separate Vue 3 + Koa + Socket.IO
chat UI we picked for the Web surface. It talks to local llama-server via
OpenAI-compatible HTTP.

This project's value-add is the **portable, USB-stick-deployable runtime**:
`llama-server` (CPU + CUDA 12.4 + CUDA 11.8 + Vulkan), embedded
`portable-python`, runtime DLLs, and the orchestration scripts in `bin/`.

The bridge is the ~few-hundred-lines of glue that wires these together:
- A FastAPI server that the Web UI talks to (proxies / filters requests)
- A sitecustomize.py that monkey-patches two specific Windows-only bugs
  upstream has not yet merged (`c8d1e0ea8` + `d59d06c2d`)
- Adapter code that injects our local infra into upstream's runtime
  (e.g. `HERMES_HOME = data/hermes-agent`, `HERMES_SANDBOX_DIR` injection)

## What lives here

| Path | Status | Purpose |
|---|---|---|
| `__init__.py` | ✅ | Bridge package marker |
| `README.md` | ✅ | This file |
| `server.py` | ✅ | FastAPI skeleton — Web UI ↔ upstream `AIAgent` proxy |
| `sitecustomize.py` | ✅ | Monkey-patch template for Windows-only fixes |
| `adapters/` | 📝 TODO | Move truly-independent `hermes/*.py` here? Or keep at `hermes/`? |
| `tests/` | 📝 TODO | E2E tests for the bridge layer |

## What does NOT live here

- **Upstream hermes-agent code** → `../hermes-agent/` (do not edit; PR upstream)
- **Upstream hermes-web-ui code** → `../hermes-web-ui/` (do not edit; do not
  need to — bridge module does all proxy / filter work)
- **Truly-independent tools** (gguf.py, gpu.py, workspace.py, ...) → `../hermes/`
- **Launchers** → `../bin/*.bat`, `../bin/*.ps1`
- **State** → `../data/hermes-agent/` (= `HERMES_HOME`)

## Architectural decisions

1. **No fork of hermes-agent.** We pull clean v0.16.0 and use it as a library.
   Local Windows fixes ship as monkey-patches in `sitecustomize.py`, not as
   source edits.

2. **No fork of hermes-web-ui.** WebUI calls go through the bridge, which
   translates upstream WebUI's endpoints onto our `/api/*` + `/v1/*` backends.
   WebUI's `data/webui-new/app/` was the 0.6.11 fork — now deleted.

3. **`hermes/` package stays thin.** It contains only code that has no upstream
   equivalent (gguf parser, GPU detection, workspace whitelist, etc.) and a
   thin shim `__init__.py` + `__main__.py` that re-export from upstream.

4. **`bridge/server.py` is the FastAPI app** that EKKOLearnAI WebUI talks to.
   It listens on `:7860`, exposes `/v1/models`, `/v1/chat/completions`,
   `/api/*` (sessions, kanban, cron, workspaces, settings — all delegated
   to upstream stores), and runs upstream's `AIAgent` as the chat backend.

5. **`HERMES_HOME = ../data/hermes-agent`** so all upstream state lands inside
   this project's data dir. The `bin/*.bat` launchers set this env var.

## Status (2026-06-09)

Skeleton only — `server.py` is a stub that returns 200 OK on `/health`. Real
endpoint wiring lands in next round.

## See also

- `../AGENTS.md` — project memory bank with the full refactor history
- `../hermes-agent/AGENTS.md` — upstream development guide (read this first
  before touching anything in `../hermes-agent/`)
- `../hermes-agent/hermes_cli/web_server.py` — upstream's own FastAPI server
  (we will reuse its handlers via import, not fork)
- `data/_backup/hermes_dups_2026-06-09/` — backup of 13 .py files we removed
  from `hermes/` because upstream v0.16.0 has equivalent implementations