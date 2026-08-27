# Ikaros Architecture (post-dsh) — 2026-08-27 总览

> **目的**：给所有新接入本项目的 AI Agent（本地 IDE / dsh 工作引擎 / 子代理 / Web 子会话）一份
> **5 分钟可读懂的现状全景**，附指针指向 `docs/ARCHITECTURE.md` 等深读文档。
>
> **最后更新**：2026-08-27（dsh 接入对话树 + OpenViking 借鉴 5 项 + ikaros 启动器嵌套子命令批次落地）
>
> **事实优先级**：`code > config/components.yaml > bin/ikaros-env.* > docs`。本文档是"事实源 + 必要修订"，
> 与 `docs/ARCHITECTURE.md` 互补：本文讲"现在是什么"，ARCHITECTURE 讲"为什么这么设计"。

---

## 1. 一句话

Ikaros 是**装在 U 盘里的自包含 AI 数字管家**——V5 灵魂核心（记忆 / 人格 / 反思）+ dsh 工作引擎
（DeepSeek Harness）+ 对话树面板，云端 DeepSeek 为主、本地 bge-m3 embedding 兜底语义检索，
**3 组件进程 / 3 个端口 / 零系统依赖**。

---

## 2. 当前生效服务（事实表，与 `docs/ARCHITECTURE.md §1.2` + `config/components.yaml` 一致）

| 端口 | 组件 | 进程入口 | watchdog | 启动方式 | 关键契约 |
|------|------|---------|----------|---------|---------|
| **:8587** | Embedding (bge-m3, 1024 dim) | `runtime/llama/b10000-cuda/llama-server.exe` | 各组件脚本自带 `self` | `bin/ikaros embed` | 模型 = `core/memory_v5/models/bge-m3-q8_0.gguf`（已被 :3080 / :48920 双重依赖） |
| **:3080** | **dsh (DeepSeek Harness)** | `node runtime/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js web --no-open` | `bin/start-dsh-ikaros.bat` self-respawn | `bin/ikaros web` | 必须传 `IKAROS_ROOT` 环境变量；`--no-open` 防 dsh 自动开 Edge（**2026-08-27 兄弟 commit `d0052c3` 修复双弹窗**） |
| **:48920** | **对话树面板（卡片图）** | `python core/conversation-tree/server.py --port 0` | **`dsh` 插件 `ikaros-conversation-tree` 每 3s 探活 + 拉起**（2026-08-24 起从集中 watchdog 切换） | `bin/ikaros tree` | 动态端口：`server.py` 把实际端口写 `tmp/ct-port.json`，`components.yaml` 用 `healthcheck.type=port_file` 健康检查；**兄弟 commit `593bda8` 落地** |
| ~~`:8080`~~ | ~~本地 LLM (Phi-4)~~ | — | — | — | **🚫 已退役 2026-08-18**；`model_config.json` `initial_model=""` 显式禁用；恢复方法 = 放 gguf 进 `core/memory_v5/models/` + 设 `initial_model` |

> **数据真相源链路**：`v5.db` (SQLite+FTS5) = 唯一真相源 / `chroma/` = 派生可重建 / JSON 状态 = 灵魂状态（自_model/affect/relationship/vitality）。

---

## 3. 三层架构（合并 ARCHITECTURE §1.1 + 2026-08-24 批次的 dsh plugin 层）

```
┌──────────────────────────────────────────────────────────────────────┐
│  L3 表现层                                                           │
│    dsh web :3080              ← DeepSeek Harness (dsh)               │
│    对话树 :48920              ← server.py + 卡片图前端                │
├──────────────────────────────────────────────────────────────────────┤
│  L2 智能体层 (V5 灵魂核心)                                            │
│    core/memory_v5/            ← V5 自我认知引擎 (包名 memory_v5)      │
│      ├── mcp_server.py        ← 50 个 v5_* MCP 工具                   │
│      ├── memory_retrieval    ← unified_retrieve (唯一对外检索入口)    │
│      ├── store / search       ← v5.db + Chroma 双索引                 │
│      ├── extensions/          ← token_compressor / temporal_graph    │
│      ├── reflect/             ← consolidate / distill / reflect       │
│      └── recall_ledger / recall_budget / freshness / extract         │
│                              ← OpenViking 借鉴 5 项 (2026-08-24)      │
├──────────────────────────────────────────────────────────────────────┤
│  L1 基础设施层                                                        │
│    runtime/dsh/               ← DeepSeek Harness (npm 本地安装)        │
│    core/ikaros-dsh/                                                       │
│      ├── cordis.patch.yml     ← overlay: MCP + terminal + LSP +       │
│      │                           persona + ikaros-memory plugin +     │
│      │                           ikaros-conversation-tree plugin      │
│      └── plugins/                                                          │
│          ├── ikaros-memory          ← turn-stopping 自动沉淀 +        │
│          │                            pre-step should_recall +         │
│          │                            compaction 捕获 + maintenance    │
│          │                            tick (6h reflect.run_all)        │
│          └── ikaros-conversation-tree ← Node 看门狗 + Client 双面     │
│                                          (sidebar 按钮 + shell.overlay │
│                                          iframe 全屏面板)              │
│    Embed :8587 (bge-m3)      ← llama.cpp b10000-cuda                  │
├──────────────────────────────────────────────────────────────────────┤
│  L0 运行时层                                                          │
│    runtime/portable-python/ 3.12.10                                    │
│    runtime/node/                                                       │
│    runtime/llama/b10000-cuda/                                          │
│    bin/ikaros-env.{sh,bat,ps1} ← IKAROS_ROOT 自锚定权威源 (路径发现 + │
│                                    变量注入)                            │
│    core/env/ ← Python 侧 ikaros-paths.json 镜像副本                    │
└──────────────────────────────────────────────────────────────────────┘
```

> ⚠️ ARCHITECTURE §1.1 强调：**L0-L3 不是结构边界，是逻辑分组**。Ikaros 是组件化微服务，
> 组件间仅通过 HTTP 接口 + 环境变量耦合。dsh :3080 与对话树 :48920 是两个独立进程。

---

## 4. dsh 工作引擎 + overlay（dsh 时代的核心接入点）

### 4.1 overlay 规范源：`core/ikaros-dsh/cordis.patch.yml`

5 个 `- insert` 段叠加到 dsh base 上：

| 块 | 内容 | 备注 |
|----|------|------|
| `memory-ikaros-v5` | MCP stdio → `runtime/portable-python/python.exe` 起 `core/memory_v5/mcp_server.py`，env `V5_MCP_TOOL_GROUPS=memory,self,care,vitality,relationship,skill,project` | **50 个 `v5_*` 工具**（2026-08-24 新增 `v5_recall` 预算感知召回，兄弟 commit `b62bd16`+`e09b3a7`） |
| `ikaros-memory` | `@ikaros/dsh-ikaros-memory`（裸包名，**Entry.name 不走 `!!js` interpolate**，仅 config 字段走） | turn-stopping 自动沉淀 + pre-step `should_recall` 注入 + compaction 捕获 + **maintenance tick（6h `reflect.scheduler.run_all`，2026-08-24 补上 watchdog 退役后生命周期调度器的触发空缺）** |
| `ikaros-conversation-tree` | `@ikaros/dsh-conversation-tree`（**2026-08-24 新增**），Node 侧看门狗 + client 侧 sidebar 按钮 + `shell.overlay` iframe 全屏 | 兄弟 commit `717c763` 入仓；详见 §5 |
| `terminal / terminal-bash / tool-terminal` | 持久 PTY 终端 | enableRunInBackground: true |
| `lsp / lsp-stdio / tool-lsp` | typescript LSP（python 默认不挂：pyright 缺失会中止启动） | |
| `system-prompt` | Ikaros persona 覆盖 + 推荐优先用 `v5_recall` token 预算召回 | 兄弟 commit `17b110f` |

### 4.2 启动参数关键约束

| 约束 | 原因 |
|------|------|
| **`--no-open`**（web 模式） | dsh 默认会开系统浏览器，2026-08-27 哥哥确认双弹窗问题 |
| **`--patch`** 仅 headless 模式 | web 模式自动从用户层 `~/.dsh/profiles/web/cordis.patch.yml` 加载；启动脚本不传避免 duplicate loader |
| **环境变量 `IKAROS_ROOT`** 必须由 `bin/start-dsh-ikaros.bat` 注入 | `cordis.patch.yml` 用 `!!js process.env.IKAROS_ROOT + "\\runtime\\portable-python\\python.exe"` 推导路径，0 盘符硬编码 |

### 4.3 插件装配（pnpm file:，**2026-08-24 起落地**）

```
cd ~/.dsh/profiles/web
pnpm remove @ikaros/dsh-ikaros-memory
pnpm add file:"${IKAROS_ROOT}/core/ikaros-dsh/plugins/ikaros-memory"
```

⚠️ **pnpm `file:` 是复制非符号链接，必须 `remove/add` 才带新 dist**（memory note 已沉淀）。

---

## 5. 对话树面板（:48920）—— 2026-08-24 起由 dsh 插件接管

### 5.1 服务侧

- **后端引擎**：`core/conversation-tree/server.py`（ThreadingHTTPServer，~2400 行，零三方依赖）
- **后端逻辑**：`core/memory_v5/conversation_tree.py`（ConvTree + ConvCard 引擎）
- **前端**：`index.html` + `assets/poker-input.css`（卡片图风格）
- **数据存储**：节点（回合）= 事实源存 V5；UI 结构存 `core/memory_v5/data/v5/ui_conversation_tree.json`
- **LLM 路由**：DeepSeek 直连 `CT_DEEPSEEK_MODEL=deepseek-v4-flash`，单模式（hermes 模式已废），**只读工具回路** `_READONLY_TOOLS = {memory_search, get_current_time, branch_overview}`，`MAX_TOOL_ROUNDS=6`
- **退化链**：DeepSeek 不可达 → 继续走本地三层 chat 补全 + SSE `warn` 事件
- **动态端口**：`--port 0` 由 OS 分配，写入 `tmp/ct-port.json`，`ikarosctl.py` 读取后做端口探测（兄弟 commit `593bda8`）

### 5.2 dsh 插件侧（`ikaros-conversation-tree`）

**Node 侧**（`src/index.ts`）：
- 每 3s 探活 :48920，死了用便携 Python 拉起 `server.py --port 0`
- `ctx.conversationTree { healthy, url, lastChecked }` 给其他插件消费
- `adoptot(p)` 写 `status.port` + `status.url` + `ct-port.json` + `patchClientJs(p)`（正则代端口，避免老 URL no-op）
- **5s fallback 探测**（防 silent bind-fail；python stdout 缓冲丢 PORT= 时由 fallback 兜底）
- **BOOT-SPAWN 复用路径同样调 `adoptot`**（修 2026-08-27 兄弟 commit `593bda8` 发现的 sync MISMATCH bug）

**Client 侧**（`src/client.tsx`）：
- `sidebar.footer.action` 注入（IKAROS 大写 + 思源字体 + 主题色 `var(--ct-brand)` + 9s 28% 概率彩虹流光彩蛋）
- `shell.overlay` 注册 `TreeView` (order 8000, 全屏 iframe) + `CloseDialog` (order 9999)
- 监听 tree page `postMessage`：
  - `ikaros-ct-close-dialog` → 弹关闭确认框
  - `ikaros-ct-theme` → 同步主题色到 `document.documentElement.style.setProperty('--ct-brand', ...)`
- `useSyncExternalStore` 用 `getSnapshot` + `getDialogSnapshot` 独立订阅（防双态耦合）
- **双击 sidebar 按钮 = 空**（哥哥 2026-08-26 显式要求），关闭仅由 tree 页面 I 徽标 dblclick 触发

### 5.3 双 frame 信号桥

```
[对话树 iframe]
  applyTheme(pref) -> broadcastTheme() -> parent.postMessage({type:'ikaros-ct-theme', color:c}, '*')
  dblclick I logo -> notifyHostCloseDialog(e) -> parent.postMessage({type:'ikaros-ct-close-dialog'}, '*')

[dsh TreeView]
  message listener:
    ikaros-ct-close-dialog -> store.showDialog()
    ikaros-ct-theme -> --ct-brand 更新
```

harmless when page opened standalone (no parent listener → no-op；wrap in try/catch)。

---

## 6. 启动器（`bin/ikaros` 三壳入口 + `core/ikarosctl.py` 调度核心）

### 6.1 三壳入口

```
bin/ikaros         ← bash (MSYS / Git-Bash / WSL)
bin/ikaros.bat     ← cmd (ASCII only, GBK safe)
bin/ikaros.ps1     ← PowerShell (UTF-8)
core/ikarosctl.py  ← Python 调度核心
config/components.yaml ← 3 组件元数据 (dsh / conversation-tree / embedding)
```

### 6.2 子命令契约（实测，与 `argparse` choices 元组一致）

| 子命令 | 行为 |
|--------|------|
| `ikaros web` | 拉起 dsh web :3080（兄弟 commit `593bda8` 已加 `--no-open`） |
| `ikaros tree` | 拉起对话树 :48920 |
| `ikaros embed` | 拉起 embedding :8587 |
| `ikaros all` | 拓扑序 `embedding → conversation-tree → dsh` |
| `ikaros dsh {status,open,sync,restart,stop}` | **兄弟 commit `593bda8` 新增嵌套子命令**，dsh 配套管理（status 一屏看清 3080+CT 端口+node PID+client.js URL 同步状态） |
| `ikaros status / ps / logs / stop / restart` | 通用组件管理 |
| `ikaros doctor` | 诊断（读 components.yaml + runtime 缺失检查） |
| `ikaros update` | 拉 upstream（TODO） |

⚠️ `headless` **非一级子命令**——是 `start_component` 对 dsh 的内部 `web|headless` 分支（`ikarosctl.py:172`）。
一级走 `web`（web GUI），headless one-shot 走 `bin/start-dsh-ikaros.bat headless <task>`（薄壳转 `ikaros web`）。

### 6.3 关键设计

- **IKAROS_ROOT 自锚定**：bash 用 `${BASH_SOURCE[0]}`、cmd 用 `%~dp0`、PS 用 `$PSScriptRoot`，复用 `bin/ikaros-env.*` 的环境权威源
- **dsh 组件的递归防护**：dsh 的 `start_script` 是 thin wrapper（调 ikaros），ikaros 又会调 start_script 启动 dsh → 直接派 `node bin.js web --no-open` 真启动 dsh（兄弟 commit `593bda8`）
- **`port_file` 健康检查**（兄弟 commit `593bda8`）：`server.py --port 0` 绑定后写 `tmp/ct-port.json`，启动器从文件读实际端口再探测（取代死等固定 48920）
- **thin wrapper 不删**：旧 `bin/start-dsh-ikaros.bat` / `restart-dsh-ikaros.ps1` / `core/memory_v5/services/start-embedding.bat` 全部调 `ikaros` 启动器
- **新薄壳 4 件**（`bin/dsh-{open,status,sync,restart}.bat`）：每个动作独立 `.bat`，`Win+R` 即可调，背后全走 `ikaros dsh <sub>`；CRLF 正确（pre-commit hook 验证过）

---

## 7. V5 灵魂核心关键模块（速查，详细见 `docs/ARCHITECTURE.md` §4.1 + §5.2）

| 模块 | 行数 | 职责 |
|------|------|------|
| `memory_retrieval.py` | **939** | **唯一对外检索入口** `unified_retrieve(scope=auto\|semantic\|lexical\|graph\|tree\|temporal)` |
| `store.py` | **1174** | v5.db SQLite WAL + 异步 chroma 同步 + json_lock，**唯一真相源** |
| `search.py` | 643 | Chroma 向量索引 + 三路融合计分 + thread-local `_embed_conn`（dsh 多线程竞态根治，2026-08-24） |
| `conversation_tree.py` | — | ConvTree / ConvCard 引擎 |
| `validation.py` | 728 | `V5-0109` 结构化内容守卫（拦截旁白/裸 JSON/超长） |
| `reflect/registry.py` | — | 反思调度器（注册 extract_experiences / refresh_freshness / retention 等 op） |
| `extensions/` | — | token_compressor / temporal_graph / ontology_align / tree_adapter |
| `mcp_server.py` | 205 | **50 个 v5_* MCP 工具**（含 `v5_recall` 预算感知召回，2026-08-24） |
| `recall_ledger.py` / `recall_budget.py` / `freshness.py` | 171 / 207 / — | **OpenViking 借鉴 5 项 (2026-08-24)** |
| `entity_graph.py` / `project_edges.py` / `graph_rank.py` | — | 实体图 + 类型化项目边 + PPR 社区发现（V5.7） |

### 7.1 OpenViking 借鉴 5 项（VikingMem VLDB 2026，兄弟 commit `b62bd16`）

| ID | 模块 | 作用 |
|----|------|------|
| F1 | `recall_ledger.py` | 每会话 JSON `recall_log_<session>.json` 记近 5 轮已展示正文；召回时跳过（`dedup_turns=5`）。**核心：bare-URI 不冷却** |
| F2 | `recall_budget.py` | `plan_entries(candidates, max_tokens)` 广度后深度 + 超限降级不截断（full→abstract→uri）+ body-hash dedup |
| F3 | `reflect/extract_experiences.py` | 24h 严格 JSON schema 抽取 lesson/decision/preference/fact；写入走 `store.upsert()`（合并强化，不堆积雷同） |
| F4 | `v5_recall` 返回 `{context, stats}` | 补 P8 `explain_result` 缺的每查询聚合轨迹（retrieved/cooled/placed/dropped/deduped/tier_counts/fill） |
| F5 | `freshness.py` | cluster_freshness 表按 bucket(type) 维护 watermark+pending；治反思欠账 |

### 7.2 检索调用链（最终裁定，P5 + P6 收敛后）

```
外部 (dsh MCP / CT tool / tree_adapter)
  → unified_retrieve(scope)
      ├── semantic:  retrieve()  (FTS5 + Chroma + 时间)
      ├── lexical:   retrieve() 纯关键词
      ├── graph:     _graph_retrieve()  (实体图 + project_edges + graph_min 过滤)
      ├── tree:      tree_adapter.retrieve (树域加权)
      └── temporal:  retrieve_temporal()  (valid_to < now 剔除过期事实)
```

⚠️ **已删除调用点**（2026-08-14 P1-P6 收敛）：`search.fused_search`（旧双路）、`rules_retriever`（孤儿）、`gated_retrieval`（骨架）、`mr.retrieve`（外部已不直连）。

### 7.3 MCP 工具契约（不变量）

- **外部前缀**：`v5_*`（db 文件名仍 `v5.db`），**禁止重命名**
- **50 个工具分组**（`V5_MCP_TOOL_GROUPS` 过滤）：
  - `memory` 22（含 v5_recall）/ `self` 14 / `care` 2 / `vitality` 2 / `relationship` 2 / `skill` 5 / `project` 3

---

## 8. 测试现状（兄弟 commit `3145f7e` AGENTS 段注：全量 **364 passed**）

- **离线**（:8587 离线）：333 passed（基线）
- **在线**（:8587 在线）：364 passed（333 + 31 新）
- **新增 31 项**：test_recall_budget 9 + test_recall_ledger 9 + test_freshness 7 + test_extract_experiences 6
- **关键回归测试 fix**：`test_search_roundtrip` 历史偶发 flake（autouse fixture 切独立 temp DB + temp chroma dir，与 live 库/:8587 完全解耦，线上线下均 364 全绿）

⚠️ 哥哥 2026-08-27 指令"测试脚本等不提交，只提交核心文件"——本次提交组仅含核心文件；
测试脚本（`tests/`, `core/{conversation-tree,memory_v5}/tests/`）**未跟随 8 个 commit 入仓**，
由哥哥后续独立决策。

---

## 9. 关键事实源 + 必看指针

| 主题 | 文档 |
|------|------|
| **架构总览**（设计动机） | `docs/ARCHITECTURE.md`（40KB, 549 行） |
| **启动器设计** | `docs/ikaros-launcher-design.md` |
| **组件插件接口规范** | `docs/COMPONENT-PLUGIN-SPEC.md` |
| **dsh 插件架构**（评估草案，含 P1-P4 迁移 Phase） | `docs/ikaros-dsh-plugin-architecture.md` |
| **V5 架构收敛**（P1-P8 全部裁决） | `docs/v5-architecture-convergence.md` |
| **命名规则** | `docs/naming.md` |
| **Handoff Card** | `AGENTS.md`（47KB, 210 行） |
| **变更日志** | `docs/CHANGELOG.md` |
| **组件元数据（SSOT）** | `config/components.yaml` |
| **dsh overlay 规范源** | `core/ikaros-dsh/cordis.patch.yml` |
| **dsh 插件源码** | `core/ikaros-dsh/plugins/{ikaros-memory,ikaros-conversation-tree}/` |
| **V5 包根** | `core/memory_v5/` |
| **启动器 bash 入口** | `bin/ikaros`（17 行 thin） |
| **环境权威源** | `bin/ikaros-env.{sh,bat,ps1}` |
| **漂移检查** | `python docs/lint.py`（兄弟 commit `3145f7e` 验证通过） |

---

## 10. ⚠️ 已知风险 / 待决策

1. **memory_v5 未独立组件化**：`config/components.yaml` 仅 3 组件（dsh / conversation-tree / embedding），
   `memory_v5` 仍作为 dsh 的子模块（依赖 `embedding`）。**修正建议**：要么把 memory_v5 升级为
   `category: memory` 独立条目，要么把 conversation-tree 的 `dependencies` 改为 `[embedding]`。
   详见 `docs/COMPONENT-PLUGIN-SPEC.md` §6 open questions。
2. **`watchdog: self` 缺口**：embedding 由各组件脚本自带 watchdog 拉起，但脚本无 self-respawn；
   死了没人拉起。dsh `bin/start-dsh-ikaros.bat` 是 self（实测）。
3. **`process_marker` 命名规范**：当前 `conversation-tree` 的 marker 是 `conversation_tree`（下划线），
   与 id 不同形。Windows tasklist 实际命令行含 `-`（dash）—— tasklist 匹配可能失败。
4. **本地 LLM 恢复路径**：若恢复，把 gguf 放进 `core/memory_v5/models/` + 设 `model_config.json` 的
   `initial_model` 即可（其余链路按 config 自动生效，AGENTS §8 已记录）。
5. **测试脚本入库策略**：本批 commit 未跟随测试脚本入库，由哥哥后续决策。

---

## 11. 兄弟 commit 索引（2026-08-24 批次，2026-08-27 提交）

| Commit | 主题 |
|--------|------|
| `d0052c3` | fix(launcher): dsh web 启动加 --no-open 防止重复开系统浏览器 |
| `593bda8` | feat(launcher): ikaros dsh 嵌套子命令 (status/open/sync/restart/stop) + 动态端口支持 |
| `17b110f` | feat(dsh-overlay): 注册 ikaros-conversation-tree 插件段 + v5_recall persona 提示 |
| `717c763` | feat(dsh-plugin): ikaros-conversation-tree 插件 (Node 看门狗 + Client 双面) |
| `b62bd16` | feat(memory_v5): OpenViking 借鉴 5 项 — recall/ledger/budget/freshness/extract |
| `e09b3a7` | feat(memory_v5): v5_recall 第 50 工具接入 MCP + reflect 调度器 + config 同步 |
| `d33ec60` | feat(ct): 双击 I 徽标通知 dsh 弹关闭确认框 + applyTheme 广播主题色 |
| `3145f7e` | docs(AGENTS): 2026-08-24 审计修复 + OpenViking 借鉴章节 |

**未跟随本批入库**（哥哥 2026-08-27 指令）：所有 `tests/` + `tests/test_*` + `core/conversation-tree/tests/test_*` +
`core/memory_v5/tests/test_*` 测试文件改动；`.cache/` `projects/` `tmp_start.*` `.hermes/` 临时目录；
`bin/dsh-{open,restart,status,sync}.bat` + `bin/ikaros-launcher.bat` 新薄壳（untracked，CRLF 已修对）。