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
Hermes API gateway (:8642) is ACTIVE again — served by `python -m hermes_cli.main gateway run` (used by dashboard + chat-tree). The legacy `bin/hermes-api-server.py` script is unused; do not confuse the two.

## Startup
- Control panel: `bin/ikaros-control.bat` → opens http://127.0.0.1:9100
- Neko frontend (Electron shell `N.E.K.O.exe`): `bin/neko-start.bat`
- **Distinction**: `core/control-panel/` = Electron desktop shell (pulls up `:9100` + components); `core/neko/` = FastAPI + React **frontend service** (its `N.E.K.O.exe` is the neko shell). Don't conflate the two.

## Soul core
- Renamed: the V5 soul-core dir is now `core/memory_v5/` (the old `v5` subdir under `core` is gone). Python package `v5` → **`memory_v5`** (`import memory_v5`); `sys.path` must include `E:/Ikaros/core`.
- Data still at `core/memory_v5/data/v5/`; DB file **still** `v5.db`. The 40 MCP tools are **still** prefixed `v5_*` (external contract — do NOT rename the db or the tool prefix).
- **统一检索路由（2026-08-01）**：新检索入口 `memory_retrieval.unified_retrieve(query, scope=auto|semantic|lexical|graph|tree|temporal)`（借鉴 cognee recall；auto 语义不足自动补图扩散路）。`memory_api` fuse 路径与 conversation-tree 的 `memory_search` 工具已切换；`rules_retriever` 保持独立意图通道。检索排序新增频率/反馈权重（`frequency_weight`/`reinforcement_weight`/`freshness_weight`/`long_term_boost`，config 可关）。`temporal_graph` supersede 已接进 `dissonance._record_dissonance`（矛盾旧事实 `valid_to` 失效 + `reinforcement` 降权）；`reflect/registry.py` 新增 `memory_promote`（6h 两档桥接）+ `temporal_extract`（24h 时间戳抽取）两个 op；`extensions/ontology_align.py` 为轻量本体对齐（difflib，默认关）。⚠️ `store.conn()` 退出默认 rollback——写操作必须显式 `c.commit()`（temporal_graph 原骨架因此从未生效）。

## DO NOT
- ❌ Never run `llama-server.exe` bare — missing CUDA env → SIGSEGV. Always go through the watchdog.
- ❌ Never auto-commit / auto-push without an explicit user instruction.
- ❌ After editing `bin/cloud_chat.py`, restart the control panel — it caches `cloud_chat`, changes won't take effect otherwise.
- ❌ Don't edit `hermes-agent` code (being relocated to `core/hermes` by another agent); docs refer to it as `core/hermes`.

## 文件搜索优先级 (2026-08-03)
- **首选 MCP everything**（`mcp__everything__search`）：支持 Everything 语法（通配符 / `ext:` / `size:` 等）、`parentPath` 限定目录、全盘索引秒级返回。
- **降级**：everything MCP 服务不可用/报错时，回退默认 `search_files`（ripgrep）。
- 已实测在线（E:\Ikaros 内搜索秒回）。V5 directive #2 同步此规则。

## 9100 panel refactor (2026-07-26)
- Memory watchdog `:8080`/`:8587` split into `local_model` / `memory` cards (both model-switchable).
- Neko's 3 services merged into `neko_group` (ports 48911 + 48912 + 48915), one-click or separate control.
- Person Sync removed (sync script deleted). Hermes API gateway (:8642) is ACTIVE again — served by `python -m hermes_cli.main gateway run` (used by dashboard + chat-tree). The legacy `bin/hermes-api-server.py` script is unused.
- `hermes` cloud_chat provider now aliases to `dashboard`.

## Conversation Tree 面板 (2026-07-28, 2026-08-01 得兼改造)
- 新增 `:48920` 树形对话面板（Explore.poker 风格），由控制面板 `conversation_tree` 组件管理，启动 `core/conversation-tree/server.py --port 48920`。
- 后端引擎 `core/memory_v5/conversation_tree.py`（`ConversationTree`，33 tests）；REST：`fork` / `conclude` / `merge` / `unmerge` / `abandon` / `full_context`。
- 对话内容存 V5（`v5_memory_id` + `summary` + 拓扑落 `core/memory_v5/data/v5/ui_conversation_tree.json`），树 JSON 仅存指针。
- 与 V5 集成：`hermes_provider.push_to_conversation_tree()` 静默推送节点；`bin/import-hermes-to-convtree.py` 可将 Hermes 单会话导入对话树。
- **chat 链路（2026-08-01 得兼）**：ikaros / hermes 双模式统一走 Hermes gateway `:8642`（`/v1/chat/completions`，完整 tools/skills 循环 + MCP 工具）。hermes 模式注入「树域上下文（分支脉络）+ 树域记忆」（不重复注入 SOUL，gateway core 的 SOUL 即人格）；ikaros 模式注入「完整 persona + 树域记忆」。gateway 不可达/空响应 → 降级本地 DeepSeek 直连（`CT_DEEPSEEK_MODEL` 默认 `deepseek-v4-flash`）+ 3 只读工具回路，SSE `warn` 事件提示降级（黄色提示条）。gateway 工具结果经 `api_server._on_tool_complete` 截断 2000 透出（`hermes.tool.progress` completed 事件带 `result`）；thinking / usage / tool_calls / skills_used（工具名近似）全落库。`build_tree_aware_context`（TreePathCompressor）已修复可用（原漏 import 被静默吞掉）。前端单飞（发送期间禁用输入）+ AbortController（切换节点/重置中止在飞请求）。

## Doc-drift rule
Any commit touching architecture / ports / components MUST sync `docs/ARCHITECTURE.md` and this file, or carry a `docs:` prefix. (See `docs/README.md`.)

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Ikaros** (6202 symbols, 11715 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/Ikaros/context` | Codebase overview, check index freshness |
| `gitnexus://repo/Ikaros/clusters` | All functional areas |
| `gitnexus://repo/Ikaros/processes` | All execution flows |
| `gitnexus://repo/Ikaros/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
