# Ikaros — Handoff Card

> Quick-start for any AI Agent picking up the project.
> Full architecture: `docs/ARCHITECTURE.md`. Naming rules: `docs/naming.md`.
> Drift guard: `python docs/lint.py` (run after doc edits).

## Ports (8 active services)

| Port | Service | Component |
|------|---------|----------|
| :9100 | Control panel Web UI | `core/dashboard/server.py` (start: `bin/ikaros-control.bat`) |
| :8080 | Local LLM (Qwen3-1.7B, **lazy-loaded**) | watchdog `bin/ikaros-memory-watchdog.py` |
| :8587 | Embedding (nomic) | watchdog |
| :48911 | Neko main frontend | `core/neko/app/main_server.py` |
| :48912 | Neko memory server | `core/neko/app/memory_server.py` |
| :48915 | Neko agent server | `core/neko/app/agent_server.py` |
| :9119 | Hermes Dashboard (cloud LLM gateway) | `core/hermes/.../web_server.py` |
| :8088 | Hermes-Paw (猫爪) | `bin/hermes_paw_bridge.py` |

Removed (do not re-add): Hermes API gateway (was port 8642), voice bridge (ports 7870 / 7871).

## Startup
- Control panel: `bin/ikaros-control.bat` → opens http://127.0.0.1:9100
- Neko frontend (Electron shell `N.E.K.O.exe`): `bin/neko-start.bat`
- **Distinction**: `core/control-panel/` = Electron desktop shell (pulls up `:9100` + components); `core/neko/` = FastAPI + React **frontend service** (its `N.E.K.O.exe` is the neko shell). Don't conflate the two.

## Soul core
- Renamed: the V5 soul-core dir is now `core/memory_v5/` (the old `v5` subdir under `core` is gone). Python package `v5` → **`memory_v5`** (`import memory_v5`); `sys.path` must include `E:/Ikaros/core`.
- Data still at `core/memory_v5/data/v5/`; DB file **still** `v5.db`. The 40 MCP tools are **still** prefixed `v5_*` (external contract — do NOT rename the db or the tool prefix).

## DO NOT
- ❌ Never run `llama-server.exe` bare — missing CUDA env → SIGSEGV. Always go through the watchdog.
- ❌ Never auto-commit / auto-push without an explicit user instruction.
- ❌ After editing `bin/cloud_chat.py`, restart the control panel — it caches `cloud_chat`, changes won't take effect otherwise.
- ❌ Don't edit `hermes-agent` code (being relocated to `core/hermes` by another agent); docs refer to it as `core/hermes`.

## 9100 panel refactor (2026-07-26)
- Memory watchdog `:8080`/`:8587` split into `local_model` / `memory` cards (both model-switchable).
- Neko's 3 services merged into `neko_group` (ports 48911 + 48912 + 48915), one-click or separate control.
- Removed Hermes API gateway (port 8642) and Person Sync (sync script deleted).
- `hermes` cloud_chat provider now aliases to `dashboard`.

## Doc-drift rule
Any commit touching architecture / ports / components MUST sync `docs/ARCHITECTURE.md` and this file, or carry a `docs:` prefix. (See `docs/README.md`.)
