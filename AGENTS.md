# Ikaros — Handoff Card

> Quick-start for any AI Agent picking up the project.
> Full architecture: `docs/ARCHITECTURE.md`. Naming rules: `docs/naming.md`.
> Drift guard: `python docs/lint.py` (run after doc edits).

## Ports (9 active services + 1 named-pipe)

| Port | Service | Component |
|------|---------|----------|
| :9100 | Control panel Web UI | `core/dashboard/server.py` (start: `bin/ikaros-control-panel.bat`) |
| :8080 | Local LLM (Phi-4-mini, **lazy-loaded**) | watchdog `bin/ikaros-memory-watchdog.py` |
| :8587 | Embedding (nomic) | watchdog |
| :48911 | Neko main frontend | `apps/neko/app/main_server/` (包, `python -m app.main_server`) |
| :48912 | Neko memory server | `apps/neko/app/memory_server/` (包, `python -m app.memory_server`) |
| :48915 | Neko agent server | `apps/neko/app/agent_server/` (包, `python -m app.agent_server`) |
| :9119 | Hermes Dashboard (管理面板，**非 LLM 网关**) | `runtime/hermes-agent/.../web_server.py` |
| :8088 | Hermes-Paw (猫爪) | `bin/hermes_paw_bridge.py` |
| :48920 | Conversation Tree 面板 (树形对话面板) | `core/conversation-tree/server.py` (后端引擎 `core/memory_v5/conversation_tree.py`) |
| 命名管道 | Herdr 终端编排 (coding-agent 多路复用器) | `runtime/herdr/herdr.exe`（`\\.\pipe\...`，无 TCP 端口，面板 `herdr` 组件按需启动） |

Added (2026-08-10): herdr agent `pi` = omp (oh-my-pi 17.2.12, go-deepseek 通道)。接入用法见 docs/herdr-integration-design.md §omp。
Added (2026-08-11): **pi 纳入 V5 核心** — `data/omp/agent/mcp.json` 挂载 ikaros-v5-memory MCP（全量组），pi 干活时可直接检索/存储 V5 记忆（v5_memory_search/store/self_model/relationship 等实测可用）。**分工：Hermes = 助理（对话/记忆/人格），pi = 工作引擎（编码/任务执行）**。
Added (2026-08-12): **pi 干活必须带记忆** — 开工先 `v5_project_retrieve`/`v5_memory_search` 检索相关项目决策与教训（跨会话连续性不能只押在手工 summary 上）；收尾把关键决策/坑用 `v5_project_note` 落库（kind=decision|pitfall|convention）。实测：conversation-tree 多卡重构 B+C 决策/降级归位 pitfall/zoom 弃用均已入库（#3179-3184）。
Added (2026-08-13): **omp 便携化（配置迁出 C 盘）** — omp 可执行迁到 `runtime/bun/bin/omp.exe`（bun 全局安装 `BUN_INSTALL=E:\Ikaros\runtime\bun`）；配置目录经 `PI_CODING_AGENT_DIR=%IKAROS_ROOT%\data\omp\agent` 锚定项目（agent.db / mcp.json / models.yml / config.yml / .env 全部迁入，密钥 `OPENCODE_GO_API_KEY` 放 `data/omp/agent/.env`）。⚠️ 用 `PI_CODING_AGENT_DIR`（走 path.resolve，绝对路径覆盖有效）；`PI_CONFIG_DIR` 走 path.join 遇绝对路径不重置、junction 方案已被 bun bug 堵死——勿再尝试。三处注入：`bin/ikaros-env.sh|bat`（shell 权威源）、`bin/start-omp.bat`（TUI 直启）、`core/dashboard/server.py build_env()`（面板→herdr→omp pane 链路）。旧 `C:\Users\PZS0X\.omp\` 现为兜底，确认新链路稳定后可删。

Added (2026-07-28): Conversation Tree 面板 `:48920`.
Removed (do not re-add): voice bridge (ports 7870 / 7871).
Hermes API gateway (:8642) is ACTIVE again — served by `python -m hermes_cli.main gateway run` (used by dashboard + chat-tree). The legacy `bin/hermes-api-server.py` script is unused; do not confuse the two.

## Startup
- Control panel: `bin/ikaros-control-panel.bat` → opens http://127.0.0.1:9100 (panel-only; components started from the panel UI. Hermes gateway :8642: `python bin/hermes-gateway.py start`)
- Neko frontend (Electron shell `N.E.K.O.exe`): `bin/neko-start.bat`
- **Distinction**: `core/control-panel/` = Electron desktop shell (pulls up `:9100` + components); `apps/neko/` = FastAPI + React **frontend service** (its `N.E.K.O.exe` is the neko shell). Don't conflate the two.

## 便携环境 (2026-08-11, 学秋叶整合包)
- IKAROS_* 全部变量收敛到 **`bin/ikaros-env.sh` / `bin/ikaros-env.bat`**（自锚定 `BASH_SOURCE[0]`/`%~dp0`，移动文件夹后仍正确）
- **Hermes 侧上游**：`data/hermes-agent/.env` 内含同源 32 个 IKAROS_* 变量（Hermes 进程加载 → bash 子进程继承 → snap 捕获新值，覆盖旧 snap；BASH_ENV 方案被 snap 后置覆盖，弃用）
- 注册表 IKAROS_* 已清零（勿再 setx IKAROS_*，改 .env / ikaros-env.*）
- 权威源链：`bin/ikaros-env.sh|bat` ↔ `data/hermes-agent/.env`（手工同步，生成脚本见 commit 说明）；`model_config.json` 决定本地 LLM 模型
- ⚠️ **HERMES_NODE / HERMES_TUI_DIR 必须在 `.env` 显式声明**（值指向 `runtime/node/node.exe` / `runtime/hermes-agent/ui-tui`）。snap 的旧快照曾把 HERMES_TUI_DIR 指向不存在的 `core/hermes/ui-tui`，导致 dashboard `/chat` 的 `_make_tui_argv` 走 npm install/build 失败 → `sys.exit(1)` → 前端红字 "Chat unavailable: 1"。`.env` override 会覆盖父进程传入的旧值，任何启动方式（hub/控制面板/手动）都安全。

## Soul core
- Renamed: the V5 soul-core dir is now `core/memory_v5/` (the old `v5` subdir under `core` is gone). Python package `v5` → **`memory_v5`** (`import memory_v5`); `sys.path` must include `E:/Ikaros/core`.
- Data still at `core/memory_v5/data/v5/`; DB file **still** `v5.db`. The 48 MCP tools are **still** prefixed `v5_*` (external contract — do NOT rename the db or the tool prefix).
- **统一检索路由（2026-08-01）**：新检索入口 `memory_retrieval.unified_retrieve(query, scope=auto|semantic|lexical|graph|tree|temporal)`（借鉴 cognee recall；auto 语义不足自动补图扩散路）。`memory_api` fuse 路径与 conversation-tree 的 `memory_search` 工具已切换；`rules_retriever` 保持独立意图通道。检索排序新增频率/反馈权重（`frequency_weight`/`reinforcement_weight`/`freshness_weight`/`long_term_boost`，config 可关）。`temporal_graph` supersede 已接进 `dissonance._record_dissonance`（矛盾旧事实 `valid_to` 失效 + `reinforcement` 降权）；`reflect/registry.py` 新增 `memory_promote`（6h 两档桥接）+ `temporal_extract`（24h 时间戳抽取）两个 op；`extensions/ontology_align.py` 为轻量本体对齐（difflib，默认关）。⚠️ `store.conn()` 退出默认 rollback——写操作必须显式 `c.commit()`（temporal_graph 原骨架因此从未生效）。

## Hermes 插件外置（2026-08-04）
- **ikaros_v5 上下文引擎 + 记忆提供方已外置为 Hermes 用户插件**，不再存在于 `runtime/hermes-agent` 仓库内（零源码侵入）。
- 运行时位置 `data/hermes-agent/plugins/ikaros_v5/`（= `$HERMES_HOME/plugins/`，gitignore 数据区，hermes 更新不影响）；规范源 `patches/hermes/plugins/ikaros_v5/`，由 `bin/hermes-update-and-patch.py` 的 `ensure_external_plugins()` 幂等部署。
- 两条原生发现链路：context engine 走通用插件系统（`plugin.yaml` 显式 `kind: standalone` + `register()` 调 `register_context_engine`，须在 config `plugins.enabled` 列表）；memory provider 走 memory 系统 user 目录扫描（`load_memory_provider("ikaros_v5")`）。
- 激活配置（`data/hermes-agent/config.yaml`）：`context.engine: ikaros_v5`、`memory.provider: ikaros_v5`、`plugins.enabled: [ikaros_v5]`。Dashboard 枚举走 upstream `plugins_cmd._discover_context_engines`（自动含插件注册引擎）。
- 补丁 spec 详见 `docs/hermes-ikaros-patches.md` §6b。

## DO NOT
- ❌ Never run `llama-server.exe` bare — missing CUDA env → SIGSEGV. Always go through the watchdog.
- ❌ Never auto-commit / auto-push without an explicit user instruction.
- ❌ After editing `core/dashboard/server.py` / `bin/ikaros-memory-watchdog.py` / `core/conversation-tree/server.py`, restart the corresponding service — the panel caches component states, changes won't take effect otherwise.
- ❌ Don't edit `runtime/hermes-agent` directly — it's the hermes-agent upstream worktree (git clone, kept 100% 纯净). Ikaros 定制改走 `patches/hermes/`（`bin/hermes-update-and-patch.py` 幂等重打）或 `core/hermes-bridge/`（0 侵入包装层）。
- ✅ hermes 迁移已完成（2026-08-12 验证）：源码 `runtime/hermes-agent/`（venv 同步，editable install），HERMES_HOME 数据 `data/hermes-agent/`，env 与全部启动器已指向新位置；健康测试全绿（gateway :8642 / dashboard :9119 / CLI v0.20.0 / paw :8088）。旧树残留已清理。

## 2026-08-14 接手审计修复记录（记忆恢复资料）

- **embed 模型已恢复**：`core/memory_v5/models/nomic-embed-text-v2-moe.f32.gguf`（1.8GB，`nomic-ai/nomic-embed-text-v2-moe-GGUF` 仓库，`scripts/fetch-upstreams.py` 的 URL 已修正）；`models/Phi-4-mini-instruct-Q4_K_M.gguf` 已从 `data/models/` 归位；`data/config/panel_models.json` 已同步 Phi-4。
- **v5.db 历史合并已执行（2026-08-14，决策 A）**：8/13 21:26 重建丢失的历史记忆已恢复——
  - 合并结果：**1686 行** = 实时 6 行 + 7/31 备份 1186 行 + chroma 独有 494 条（8/1-8/12，含全部树节点对话）；FTS 同步、chroma 重建 1686==1686 校验通过、13 个树指针重映射（旧 id → 100000+）全部解析
  - 合并前备份：`tmp/v5-merged-prep/applied-backup/`（v5.live.before-merge.db + 树 JSON）
  - 合并源资料：7/31 备份 `tmp/backup-v5del-20260731-094553/v5.db`；chroma 元数据导出 `tmp/chroma-docs-backup-20260814.json`
- **记忆→对话树推送链**（旧 `hermes_provider.push_to_conversation_tree`）当前无实现，恢复/删承诺待定；`:48920 /api/add_turn` 端点仍在，恢复补丁草稿 `tmp/push-chain-restore-draft.txt`。

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
- 与 V5 集成：~~`hermes_provider.push_to_conversation_tree()` 静默推送节点~~ ⚠️ 2026-08-14 审计：`hermes_provider.py` 已随重构删除，该函数代码中不存在；记忆写入→对话树推送链当前**无实现**（插件 sync_turn 只存 V5）。恢复或删承诺待定。`bin/import-hermes-to-convtree.py` 可将 Hermes 单会话导入对话树。
- **chat 链路（2026-08-01 得兼）**：ikaros / hermes 双模式统一走 Hermes gateway `:8642`（`/v1/chat/completions`，完整 tools/skills 循环 + MCP 工具）。hermes 模式注入「树域上下文（分支脉络）+ 树域记忆」（不重复注入 SOUL，gateway core 的 SOUL 即人格）；ikaros 模式注入「完整 persona + 树域记忆」。gateway 不可达/空响应 → 降级本地 DeepSeek 直连（`CT_DEEPSEEK_MODEL` 默认 `deepseek-v4-flash`）+ 只读工具回路，SSE `warn` 事件提示降级（黄色提示条）。gateway 工具结果经 `api_server._on_tool_complete` 截断 2000 透出（`hermes.tool.progress` completed 事件带 `result`）；thinking / usage / tool_calls / skills_used（工具名近似）全落库。`build_tree_aware_context`（TreePathCompressor）已修复可用（原漏 import 被静默吞掉）。前端单飞（发送期间禁用输入）+ AbortController（切换节点/重置中止在飞请求）。
- **S1 主线模型（2026-08-04）**：显式 `trunk_id` 主线终点取代 node_type 时序快照判定（旧逻辑"父节点有无子节点"导致 branch 下继续对话被误标 trunk、主线身份随创建顺序漂移）。`add_turn` 按 `trunk_id` 判定主线延续；`set_trunk(node, cascade)` 显式提升分支为主线；`is_valid_branch`/`__trunk__` 合并查找沿 `trunk_id`（唯一真源）。序列化带 `trunk_id`，旧 JSON 自动按最深 trunk 链推断。前端 trunk 徽标（★）+ 右键"设为主线终点"。
- **S2 降级工具协议（2026-08-04）**：降级链从"纯文本补全"升级为完整工具循环——`_call_llm_tools`（带 `_READONLY_TOOLS`：memory_search / get_current_time / branch_overview，OpenAI function-calling）+ `MAX_TOOL_ROUNDS=4` 多轮，模型可自主调工具；第 0 步保留 memory_search 预检索注入上下文。降级链模型名用 `CT_DEEPSEEK_MODEL`（废弃的 deepseek-chat 别名不再使用）。
- **S4 SSE chunked（2026-08-04）**：`_send_sse` 手动 `Transfer-Encoding: chunked`（HTTP/1.1 标准客户端不再等 EOF 挂起）。
- **存量回填**：`bin/backfill-session-tags.py` 给 7-28~8-01 期间无 `session:` 标签的记忆补标签（H1 会话隔离）。
- **F 系列 2026-08-04**：node_type 继承（branch 下继续仍 branch）/ merge 引用清理（prune/delete 后 merged_from 等无残留）/ 废弃分支不注入上下文 / `_effective_mode` 全局 mode 修复（model_switch 的 hermes 模式生效）/ `_touch_active_session` 传局部 tree / `/api/state` 解析 `inline` 参数 / `_send_sse` 捕 ConnectionAbortedError / fork 标签取实际父节点。
- **2026-08-12 zoom 弃用（残影根治）**：移除 `.mini-thread.l3{zoom:0.8}`（border-radius 22.5px→18px）与全部 ×1.25 补偿（topbar/empty-brand/applyTransform innerZoom-mz）；L3 与 L2 内容同尺寸实时渲染；残影实测 0 像素（根源 = zoom 位图缓存纹理缩放），zoom 计算样式全 "1"。
- **2026-08-12 de-globalization B+C（切卡不牵动他卡）**：reflowAroundCard 增量推挤（删 anyCardPairOverlap 全量触发 + clearReflow 全局复位——从当前显示位只推实际重叠卡；降级 L3→L2 仅目标卡回基础位再按新尺寸重排，他卡保持）；miniUpgradeToL3 去全局 clearReflow（降级在改类前 delete displayPositions[id]）；closeMiniCard 重构（wasFocus 前置 + applyModeClass + 非焦点仅删自身 displayPositions，焦点卡关闭仍全量 clearReflow）；`_focusLevel()`（焦点卡 class 唯一真源）取代全局 cardLevel 读取面 20+ 处；`applyModeClass()` 收敛 mode 类更新三处；cardLevel 降级为写缓存+瞬态 fallback；拖动碰撞连锁推挤收敛不变。
- **H 系列 2026-08-12（多 L3 激活错乱）**：根因 = **hover 自动升主卡 + 布局变动组合**——mini 卡 mouseenter 300ms → `setFocusCard` 抢焦点，而激活卡时 autoCenter 视口平移 / reflow 推挤会把其他 mini 卡**被动滑到鼠标下** → 无意悬停抢焦点 → 拉锯/空壳卡（L3 无 mini 无 card）+ 布局错乱；内容残留 = openMiniCard hydrate 回调 `h2=null`（节点无消息）无 else + 无 `.catch` → 「正在加载」永久残留。修复：**移除 hover 自动升主卡**（mouseenter 定时器删除——现仅手动路径：点击 mini 对话区 `bindMiniScrollDrag` wasClick → setFocusCard，双击卡 = openNodeCard L2↔L3 切换）；openMiniCard hydrate 回调 h2=null → 空态品牌 + `.catch` 空态品牌；refreshMiniCards null → 空态品牌。
- **多 L3 并存修复（2026-08-12）**：「无法同时存在多个 L3」根因 = `reflowAroundCard` 把升级卡与焦点卡都当锚点且互不推挤 → 第二个 L3 直接叠在焦点 L3 上（DOM/功能其实都在）。三处修复：① 新增升级卡↔焦点卡互斥约束（焦点锚定、新卡让位）+ 写回段包含升级卡（否则落回 nodePositions 旧位）；② `setFocusCard` 对已是 L3 的 mini 不再 toggle 降级（原无条件 `miniUpgradeToL3(from)` 会把 L3 mini 降回 L2，等级保持失效）；③ `doJump` 后补 `scheduleReflow(currentId, level)`（跳转打开新焦点 L3 不推挤旧 mini 卡）。`p0` 改为优先 `displayPositions[id]`（升级卡从当前可见位开始推挤）。
- **hover 零刷新（2026-08-12 续）**：鼠标从一个 L 卡移入另一 L3 卡（300ms 停驻快切）曾必触发全局位置刷新——`openMiniCard` 无条件 `scheduleReflow` + `reflowAroundCard` 无碰撞也跑 clearReflow/40 轮推挤/ensureCardsInViewport。修复：① `reflowAroundCard` 入口加 `overlapsAnyCard(id,level)` 无碰撞快速路径（无碰撞仅 ensureCardsInViewport，零位置变动）；② `focusCardOverlaps` 委托同一判定（改用显示位置）；③ `openMiniCard` 保留宿主等级——焦点 L3 降 mini 直接建 L3 mini（含输入框），不再先降 L2 再升 L3（消除尺寸抖动与多余 reflow）。
- **mini L3 溢出修复（2026-08-12 再续）**：多个 L3 并存时 mini 卡输入框/底缘掉出卡外显示不全——`.mini-thread.l3{zoom:0.8;width:125%;height:125%}` 的 125% 补偿是错的：Chromium 对 zoom 元素的百分比尺寸按**未缩放**包含块解析（净效果 = 百分比不变），125% 实际渲染 ≈124%，mini 恒比卡高 24%（实测卡 642 / mini 796），输入框整块落在卡外。改回 `width:100%;height:100%`（与主卡 `#card` zoom:0.8 + 100% 同机理，渲染恰为卡尺寸）。⚠️ 教训：zoom 元素上不要做 1/zoom 百分比补偿，除非基准来自未 zoom 祖先。
- **L2 盖 L3 根因修复（2026-08-12 续 4）**：「L2 状态极不稳定、有概率覆盖在 L3 上」= 两个叠加缺陷。① **快速路径重叠残留**：`reflowAroundCard` 无碰撞快速路径只查目标卡自身（`overlapsAnyCard`），不查其他展开卡之间——`clearReflow`（降级/升级触发）把多卡恢复到紧凑树布局位后，卡间重叠残留到下一次真正有碰撞的 reflow。修复：快速路径条件加 `!anyCardPairOverlap(id)`（除 id 外任意两张展开卡两两检测，O(n²) n<8 可忽略），有重叠则走全量推挤。② **焦点卡带 stale mini-thread**：开 mini 卡时鼠标恰在卡上（真实用户常态）→ 300ms hover 快切 `setFocusCard` 升焦点 → 焦点卡同时挂 #card 和 mini-thread（mini 被 #card 覆盖仍残留）→ 后续 `openNodeCard` 因 `_cards.has(id)` 误走 miniUpgradeToL3 分支（双击焦点卡变「降级 L2」而非「关闭回 L1」）。修复：`setCardLevel` 非 L1 分支挂 #card 前、L1 分支还原前，均清理目标节点残留 `.mini-thread` 并 `_cards.delete`（焦点形态 = #card，与 mini 互斥）。验证：强制 A/C 数据层重叠 → reflow(B) 全量推挤分开零重叠；hover 快切后焦点卡无 mini 残留；双击焦点卡正确关闭。
- **架构重构 A+B+C（2026-08-12 焦点≠currentId 解耦 + 双击关闭修复）**：① **焦点卡 ≠ `tree.currentId` 系统性解耦**（空壳面板根因）——`tree.currentId` 经 installState 重置为后端当前节点（root），与焦点卡解耦，**所有焦点判定统一 `_focusId()`（data-fid + `_transferFocusIds` 按焦点面板转移），currentId 仅 fallback**，共 11 处：openNodeCard 关闭判定 / setCardLevel prevId-newId / closeMiniCard 焦点回退 / miniUpgradeToL3 焦点同步 / setFocusCard 幂等+from / mini 对话区点击快切 / headerDragStart2 顶栏切换 / collidePushCards+reflowAroundCard 锚点（fid） / openMiniCard keepL3 / renderAll+renderThread+renderHeader 焦点优先 / sendMessage 挂载（parentId/depth/branchLabel 用 fcNode）。回归教训：openNodeCard 曾含 `id===tree.currentId` 分支 → 双击 root（currentId 但非焦点）误关焦点卡，已删。② **双击卡语义**（350ms 两次 mousedown <6px → openNodeCard）：焦点卡双击关闭回 L1（实测 panels=0、无 shell）；非焦点卡双击 openMiniCard 开 mini；mini 卡双击 miniUpgradeToL3。点击 mini 对话区（bindMiniScrollDrag wasClick）→ setFocusCard 切主卡（实测焦点转 root、旧卡保留 mini 无重叠）。③ **doJump 焦点面板迁移**：跳转后 `_focusId()!==jid` → setCardLevel(cardLevel, jid)（新 currentId 成焦点面板、旧焦点降 mini），已焦点仅 scheduleReflow（实测跳转 root：焦点迁移、level 保持、cards 保留、零重叠）。④ **L1 容器化（消灭 `#card` 迁移空壳）**：`_panelInnerHTML` + `ensurePanel`（幂等守卫）+ `_panelOf`——卡片 = 容器内面板，无 `#card` 迁移；空壳 = 焦点判定用 currentId 导致焦点面板被清而容器残留。滚动引擎 scrollTop 统一 + bindScrollDrag appRoot 委托 + capture wheel（惯性续动实测 500→1462→2067→2081 衰减曲线）。⑤ **组视图**：组卡类名 `.group-card`（非 `.topic-card`）；组折叠时组内 `.node-card` 不渲染（先 `toggleGroup('grp_...')` 展开再操作节点）；holdsPanel 强制展开（组内焦点/卡节点）。

## Doc-drift rule
Any commit touching architecture / ports / components MUST sync `docs/ARCHITECTURE.md` and this file, or carry a `docs:` prefix. (See `docs/README.md`.)

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Ikaros** (5795 symbols, 10497 relationships, 286 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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
