# Ikaros — Handoff Card

> Quick-start for any AI Agent picking up the project.
> Full architecture: `docs/ARCHITECTURE.md`. Naming rules: `docs/naming.md`.
> Drift guard: `python docs/lint.py` (run after doc edits).

## Ports (9 active services + 1 named-pipe)

| Port | Service | Component |
|------|---------|----------|
| :9100 | Control panel Web UI | `core/dashboard/server.py` (start: `bin/ikaros-control.bat`) |
| :8080 | Local LLM (Qwen3-1.7B, **lazy-loaded**) | watchdog `bin/ikaros-memory-watchdog.py` |
| :8587 | Embedding (nomic) | watchdog |
| :48911 | Neko main frontend | `core/neko/app/main_server/` (包, `python -m app.main_server`) |
| :48912 | Neko memory server | `core/neko/app/memory_server/` (包, `python -m app.memory_server`) |
| :48915 | Neko agent server | `core/neko/app/agent_server/` (包, `python -m app.agent_server`) |
| :9119 | Hermes Dashboard (cloud LLM gateway) | `core/hermes/.../web_server.py` |
| :8088 | Hermes-Paw (猫爪) | `bin/hermes_paw_bridge.py` |
| :48920 | Conversation Tree 面板 (树形对话面板) | `core/conversation-tree/server.py` (后端引擎 `core/memory_v5/conversation_tree.py`) |
| 命名管道 | Herdr 终端编排 (coding-agent 多路复用器) | `runtime/herdr/herdr.exe`（`\\.\pipe\...`，无 TCP 端口，面板 `herdr` 组件按需启动） |

Added (2026-07-28): Conversation Tree 面板 `:48920`.
Removed (do not re-add): voice bridge (ports 7870 / 7871).
Hermes API gateway (:8642) is ACTIVE again via `bin/hermes-api-server.py` (used by dashboard + chat-tree); do not remove.

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
- Person Sync removed (sync script deleted). Hermes API gateway (:8642) is ACTIVE again via `bin/hermes-api-server.py` (used by dashboard + chat-tree) — do not remove.
- `hermes` cloud_chat provider now aliases to `dashboard`.

## Conversation Tree 面板 (2026-07-28)
- 新增 `:48920` 树形对话面板（Explore.poker 风格），由控制面板 `conversation_tree` 组件管理，启动 `core/conversation-tree/server.py --port 48920`。
- 后端引擎 `core/memory_v5/conversation_tree.py`（`ConversationTree`，33 tests）；REST：`fork` / `conclude` / `merge` / `unmerge` / `abandon` / `full_context`。
- 对话内容存 V5（`v5_memory_id` + `summary` + 拓扑落 `core/memory_v5/data/v5/ui_conversation_tree.json`），树 JSON 仅存指针。
- 与 V5 集成：`hermes_provider.push_to_conversation_tree()` 静默推送节点；`bin/import-hermes-to-convtree.py` 可将 Hermes 单会话导入对话树。
- 已知限制：前端 `/api/chat` 的 system prompt 写死为通用「Explore」助手，**未接入 Ikaros 人格**（SOUL.md / axiom / V5 self_model）；LLM 直连 DeepSeek（不经 Hermes 三层路由）；`/api/chat` 不记录 `skills_used` / `tool_calls`。

## Doc-drift rule
Any commit touching architecture / ports / components MUST sync `docs/ARCHITECTURE.md` and this file, or carry a `docs:` prefix. (See `docs/README.md`.)
