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

Added (2026-08-10): herdr agent `pi` = omp (oh-my-pi 17.2.12, go-deepseek 通道)。接入用法见 docs/herdr-integration-design.md §omp。
Added (2026-08-11): **pi 纳入 V5 核心** — `~/.omp/agent/mcp.json` 挂载 ikaros-v5-memory MCP（全量组），pi 干活时可直接检索/存储 V5 记忆（v5_memory_search/store/self_model/relationship 等实测可用）。**分工：Hermes = 助理（对话/记忆/人格），pi = 工作引擎（编码/任务执行）**。

Added (2026-07-28): Conversation Tree 面板 `:48920`.
Removed (do not re-add): voice bridge (ports 7870 / 7871).
Hermes API gateway (:8642) is ACTIVE again — served by `python -m hermes_cli.main gateway run` (used by dashboard + chat-tree). The legacy `bin/hermes-api-server.py` script is unused; do not confuse the two.

## Startup
- Control panel: `bin/ikaros-control.bat` → opens http://127.0.0.1:9100
- Neko frontend (Electron shell `N.E.K.O.exe`): `bin/neko-start.bat`
- **Distinction**: `core/control-panel/` = Electron desktop shell (pulls up `:9100` + components); `core/neko/` = FastAPI + React **frontend service** (its `N.E.K.O.exe` is the neko shell). Don't conflate the two.

## Soul core
- Renamed: the V5 soul-core dir is now `core/memory_v5/` (the old `v5` subdir under `core` is gone). Python package `v5` → **`memory_v5`** (`import memory_v5`); `sys.path` must include `E:/Ikaros/core`.
- Data still at `core/memory_v5/data/v5/`; DB file **still** `v5.db`. The 48 MCP tools are **still** prefixed `v5_*` (external contract — do NOT rename the db or the tool prefix).
- **统一检索路由（2026-08-01）**：新检索入口 `memory_retrieval.unified_retrieve(query, scope=auto|semantic|lexical|graph|tree|temporal)`（借鉴 cognee recall；auto 语义不足自动补图扩散路）。`memory_api` fuse 路径与 conversation-tree 的 `memory_search` 工具已切换；`rules_retriever` 保持独立意图通道。检索排序新增频率/反馈权重（`frequency_weight`/`reinforcement_weight`/`freshness_weight`/`long_term_boost`，config 可关）。`temporal_graph` supersede 已接进 `dissonance._record_dissonance`（矛盾旧事实 `valid_to` 失效 + `reinforcement` 降权）；`reflect/registry.py` 新增 `memory_promote`（6h 两档桥接）+ `temporal_extract`（24h 时间戳抽取）两个 op；`extensions/ontology_align.py` 为轻量本体对齐（difflib，默认关）。⚠️ `store.conn()` 退出默认 rollback——写操作必须显式 `c.commit()`（temporal_graph 原骨架因此从未生效）。

## Hermes 插件外置（2026-08-04）
- **ikaros_v5 上下文引擎 + 记忆提供方已外置为 Hermes 用户插件**，不再存在于 `core/hermes` 仓库内（零源码侵入）。
- 运行时位置 `data/hermes-agent/plugins/ikaros_v5/`（= `$HERMES_HOME/plugins/`，gitignore 数据区，hermes 更新不影响）；规范源 `patches/hermes/plugins/ikaros_v5/`，由 `bin/hermes-update-and-patch.py` 的 `ensure_external_plugins()` 幂等部署。
- 两条原生发现链路：context engine 走通用插件系统（`plugin.yaml` 显式 `kind: standalone` + `register()` 调 `register_context_engine`，须在 config `plugins.enabled` 列表）；memory provider 走 memory 系统 user 目录扫描（`load_memory_provider("ikaros_v5")`）。
- 激活配置（`data/hermes-agent/config.yaml`）：`context.engine: ikaros_v5`、`memory.provider: ikaros_v5`、`plugins.enabled: [ikaros_v5]`。Dashboard 枚举走 upstream `plugins_cmd._discover_context_engines`（自动含插件注册引擎）。
- 补丁 spec 详见 `docs/hermes-ikaros-patches.md` §6b。

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

## Conversation Tree 面板 (2026-07-28, 2026-08-01 得兼改造; 2026-08-04 S1/S2 结构性修复)
- 新增 `:48920` 树形对话面板（Explore.poker 风格），由控制面板 `conversation_tree` 组件管理，启动 `core/conversation-tree/server.py --port 48920`。
- 后端引擎 `core/memory_v5/conversation_tree.py`（`ConversationTree`，93 tests）；REST：`fork` / `conclude` / `merge` / `unmerge` / `abandon` / `full_context` / `set_trunk`（主线提升，废弃分支拒绝）。
- 对话内容存 V5（`v5_memory_id` + `summary` + 拓扑落 `core/memory_v5/data/v5/ui_conversation_tree.json`），树 JSON 仅存指针。
- 与 V5 集成：`hermes_provider.push_to_conversation_tree()` 静默推送节点；`bin/import-hermes-to-convtree.py` 可将 Hermes 单会话导入对话树。
- **chat 链路（2026-08-01 得兼）**：ikaros / hermes 双模式统一走 Hermes gateway `:8642`（`/v1/chat/completions`，完整 tools/skills 循环 + MCP 工具）。hermes 模式注入「树域上下文（分支脉络）+ 树域记忆」（不重复注入 SOUL，gateway core 的 SOUL 即人格）；ikaros 模式注入「完整 persona + 树域记忆」。gateway 不可达/空响应 → 降级本地 DeepSeek 直连（`CT_DEEPSEEK_MODEL` 默认 `deepseek-v4-flash`）+ 只读工具回路，SSE `warn` 事件提示降级（黄色提示条）。gateway 工具结果经 `api_server._on_tool_complete` 截断 2000 透出（`hermes.tool.progress` completed 事件带 `result`）；thinking / usage / tool_calls / skills_used（工具名近似）全落库。`build_tree_aware_context`（TreePathCompressor）已修复可用（原漏 import 被静默吞掉）。前端单飞（发送期间禁用输入）+ AbortController（切换节点/重置中止在飞请求）。
- **S1 主线模型（2026-08-04）**：显式 `trunk_id` 主线终点取代 node_type 时序快照判定（旧逻辑"父节点有无子节点"导致 branch 下继续对话被误标 trunk、主线身份随创建顺序漂移）。`add_turn` 按 `trunk_id` 判定主线延续；`set_trunk(node, cascade)` 显式提升分支为主线；`is_valid_branch`/`__trunk__` 合并查找沿 `trunk_id`（唯一真源）。序列化带 `trunk_id`，旧 JSON 自动按最深 trunk 链推断。前端 trunk 徽标（★）+ 右键"设为主线终点"。
- **S2 降级工具协议（2026-08-04）**：降级链从"纯文本补全"升级为完整工具循环——`_call_llm_tools`（带 `_READONLY_TOOLS`：memory_search / get_current_time / branch_overview，OpenAI function-calling）+ `MAX_TOOL_ROUNDS=4` 多轮，模型可自主调工具；第 0 步保留 memory_search 预检索注入上下文。降级链模型名用 `CT_DEEPSEEK_MODEL`（废弃的 deepseek-chat 别名不再使用）。
- **S4 SSE chunked（2026-08-04）**：`_send_sse` 手动 `Transfer-Encoding: chunked`（HTTP/1.1 标准客户端不再等 EOF 挂起）。
- **存量回填**：`bin/backfill-session-tags.py` 给 7-28~8-01 期间无 `session:` 标签的记忆补标签（H1 会话隔离）。
- **F 系列 2026-08-04**：node_type 继承（branch 下继续仍 branch）/ merge 引用清理（prune/delete 后 merged_from 等无残留）/ 废弃分支不注入上下文 / `_effective_mode` 全局 mode 修复（model_switch 的 hermes 模式生效）/ `_touch_active_session` 传局部 tree / `/api/state` 解析 `inline` 参数 / `_send_sse` 捕 ConnectionAbortedError / fork 标签取实际父节点。

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
