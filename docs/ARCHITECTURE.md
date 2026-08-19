# Ikaros 架构文档

> **目标读者**: 所有接入本项目的 AI Agent
> **核心原则**: 便携性（零系统依赖）+ 路径统一管理（一处注册，全局可查）
> **最后更新**: 2026-08-18（hermes / N.E.K.O / 9100 面板退役，dsh 底座）

---

## 第一章：项目概览

Ikaros 是一个**完全自包含的 AI 数字管家系统**，核心引擎为 V5 灵魂核心（记忆/人格），工作引擎为 DeepSeek Harness (dsh)，运行时环境全部打包在项目目录内，零系统依赖。

### 1.1 组件化微服务 + 逻辑职责分层

> **重要**：下面的 L0–L3 **不是结构边界，而是逻辑职责分组**。Ikaros 是一组**相互独立的进程（组件化微服务）**，组件间仅通过 **HTTP 接口 + 环境变量** 耦合，**不存在被强制执行的跨层接口**。每个组件可单独启动/停止，层与层之间不共享内存或调用栈。下列分层只是为了阅读与职责划分方便。

```
┌─────────────────────────────────────────────────────────────┐
│      L3: 表现层 (Presentation) — 逻辑分组                       │
│  dsh web :3080 — 工作引擎 Web GUI (DeepSeek Harness)          │
│  对话树 :48920 — 树形对话面板 (conversation-tree)               │
├─────────────────────────────────────────────────────────────┤
│      L2: 智能体层 (Soul) — 逻辑分组                             │
│  core/memory_v5/ — V5 自我认知引擎 (包名 memory_v5)            │
│  对话树 (ikaros) → DeepSeek 直连 + 只读工具回路   │
│  self_model / affect / relationship / narrative / dissonance │
├─────────────────────────────────────────────────────────────┤
│      L1: 基础设施 (Runtime Infrastructure) — 逻辑分组           │
│  runtime/dsh/ — 工作引擎 (DeepSeek Harness, npm 本地安装)                    │
│  ikaros-dsh overlay — memory_v5 MCP(48 工具) + terminal + lsp + persona     │
│  Embed :8587 — 各组件启动脚本自带 watchdog (本地 LLM 已退役)              │
├─────────────────────────────────────────────────────────────┤
│      L0: 运行时层 (Portable Runtime) — 逻辑分组                │
│  runtime/portable-python/ — Python 3.12.10                  │
│  runtime/llama/ — llama.cpp（b10000-cuda / b10000-cuda-12.4，按设备 CUDA 自动选择）                   │
│  runtime/node/ — Node.js  |  runtime/bun/ — Bun（omp.exe）   │
│  runtime/herdr/ — Herdr coding-agent 多路复用器 (命名管道)    │
│  bin/ikaros-env.sh|bat — 环境权威 (路径发现 + 变量注入)       │
│  core/env/ — Python 侧环境引导副本 (ikaros-paths.json 等)     │
└─────────────────────────────────────────────────────────────┘
```

> **环境变量权威源（2026-08-11 便携化后）**：`IKAROS_*` 变量的**唯一权威**是 `bin/ikaros-env.sh`（bash）与 `bin/ikaros-env.bat`（cmd），两者自锚定 `BASH_SOURCE[0]` / `%~dp0`，项目整体移动后仍正确；`core/env/` 下保留的是 Python 侧副本（`ikaros-paths.json` + `llama_resolver.py`），**注册表 `IKAROS_*` 已清零**，新增变量以 `bin/ikaros-env.*` 为准（单一权威源，路径自锚定 IKAROS_ROOT 推导）。

> **2026-08-18 底座收敛**：`core/control-panel/`（Electron 壳）、`core/dashboard/`（9100 面板）、`apps/neko/`（N.E.K.O 前端）均已退役删除；表现层由 dsh web + 对话树承接。

### 1.2 核心端口一览

| 端口 | 服务 | 路径 | 启动方式 |
|------|------|------|---------|
| :8587 | Embedding (bge-m3, 本地) | 各组件启动脚本自带 watchdog | 自动拉起 |
| :3080 | dsh 工作引擎 (DeepSeek Harness web) | `runtime/dsh/`（npm 本地安装）+ overlay `core/ikaros-dsh/cordis.patch.yml` | `bin/start-dsh-ikaros.bat web` |
| :48920 | 对话树面板 (Conversation Tree) | `core/conversation-tree/server.py`（后端引擎 `core/memory_v5/conversation_tree.py`） | `python core/conversation-tree/server.py --port 48920` |
| 命名管道 | Herdr 终端编排 (coding-agent 多路复用器) | `runtime/herdr/herdr.exe`（headless server，命名管道 `\\.\pipe\...`，无 TCP 端口） | 按需启动 |
| :8080 | 本地 LLM | **已退役 2026-08-18**（`model_config.json` `initial_model` 空串=禁用；恢复=放 gguf + 设模型名） | 禁用 |

> **端口状态**：上述 TCP 端口为当前生效服务。**2026-08-18**：控制面板 :9100、Hermes 底座（`:8642` gateway / `:8650` Bridge / `:9119` Dashboard / `:8088` QwenPaw）、N.E.K.O `:48911-48915` 全部退役删除；工作引擎 = dsh (DeepSeek Harness) :3080。更早移除：语音桥 7870/7871（07-24）。

### 1.3 控制面板 9100 重构 (2026-07-26) — ⚠️ 历史，9100 已退役 2026-08-18

控制面板（`:9100`）在此次重构后调整如下：

- **local_model / memory 拆分**：原看门狗统一持有的 `:8080`（本地 LLM）与 `:8587`（Embedding）拆分为两个独立卡片 `local_model` 与 `memory`，**两者均可在面板内切换模型**。
- **neko_group 合并**：Neko 原来的 3 个服务（`:48911` / `:48912` / `:48915`）合并为单一 `neko_group`，支持**一键启动**或分别控制。
- **Hermes API 网关（:8642）已重新启用**：由 `python -m hermes_cli.main gateway run` 提供，dashboard 与 chat-tree 复用；Person Sync（人设同步脚本）已删除。旧的 `bin/hermes-api-server.py` 脚本未启用。
- **hermes → dashboard 别名（2026-08-05 作废）**：原 `cloud_chat` 的 `hermes` 云端 provider 别名指向 Dashboard `:9119` 的路由已随 9119 网关用途移除而作废；对话树主链路改经 Hermes Bridge `:8650` → 纯净 gateway `:8642`（见 §1.5）。

### 1.3.1 面板布局与自我思考卡片 (2026-08-01) — ⚠️ 历史，随面板退役

- **整页统一自由画布**：左侧组件卡（local_model / memory / neko_group / hermes_dashboard / conversation_tree / herdr 等）与右侧仪表带（情感 PAD / 生命活力 / 自我思考 / V5 记忆 + 实时事件流）合并进同一个 `#canvas` 绝对定位画布。所有面板可**自由拖拽移动、八向缩放、拖拽时吸附对齐（容器四边+中线、其他面板四边+中线）、localStorage 记忆布局**；刷新/重载自动恢复上次布局。「重置布局」按钮清记忆恢复默认停靠。
- **自我思考卡片（替代原"内心独白"）**：原读 `pending_thought.json`（不存在、无写入者）的「内心独白」卡片已废弃；现改为读 metacog 真实产出的 `latest_thought.json`（经 `/api/state` 的 `state.thought` 暴露），显示伊卡洛斯最近一次元认知反思（text + kind + 好奇度 + 时间）。
- **metacog 仍为事件驱动、无定时循环**：`core/memory_v5/metacog.py` 保留并运行，但**不再有后台定时 think 线程**；其实际触发点为：① agent 调 `v5_self_reflect` MCP 工具（唯一会写 `latest_thought.json` 的活路径）；② 用户问"你在想什么"时只读注入；③ 每轮对话后 `mark_interaction()` 降探索欲；④ 手动 CLI。旧文档中"25min / 45min 自动内省循环"已不存在。
- **桌面 + 移动端**：PanelManager 用 Pointer Events 统一鼠标与触摸；≤720px 画布自动降级为纵向堆叠流（面板 `static`、隐藏缩放手柄、关闭拖拽），保证可读可滚动不误触。

### 1.4 对话树面板 (Conversation Tree, :48920)

新增于 2026-07-28，由控制面板 `conversation_tree` 组件管理（启动 `core/conversation-tree/server.py --port 48920`）：

- **定位**：Explore.poker 风格的**卡片图对话面板**——把多轮 / 分支对话按「卡片 = 一段连续会话」聚合，卡片间用显式连线（links）连成图（贝塞尔连线 + 拖拽连线 + 缩放 + 右键菜单 + 双主题 + splitter + localStorage）。
- **后端引擎**：`core/memory_v5/conversation_tree.py`（`ConversationTree` + `ConvCard`，引擎 47 tests + 服务 62 tests）；REST 接口 `fork` / `conclude` / `merge` / `unmerge` / `abandon` / `full_context` / `set_trunk`（主线提升，废弃分支拒绝）+ 卡片接口 `card/create|read|parent|link|unlink|branch_point` / `card_branch_chain`；`build_context_v2`（L0 祖先 + L1 兄弟 + L2 合并，MAX 50）。
- **S1 主线模型（2026-08-04）**：显式 `trunk_id` 主线终点取代 node_type 时序快照判定（旧逻辑会把 branch 下继续对话误标 trunk）；`add_turn` 按 `trunk_id` 判定主线延续，`set_trunk(node, cascade)` 显式提升分支为主线，序列化带 `trunk_id`，旧 JSON 自动按最深 trunk 链推断。前端 trunk 徽标（★）+ 右键「设为主线终点」。
- **S2 降级工具协议（2026-08-04）**：降级链从「纯文本补全」升级为完整工具循环——`_call_llm_tools`（带 `_READONLY_TOOLS`：memory_search / get_current_time / branch_overview，OpenAI function-calling）+ `MAX_TOOL_ROUNDS=4` 多轮；模型名用 `CT_DEEPSEEK_MODEL`（废弃的 deepseek-chat 别名不再使用）。
- **S4 SSE chunked（2026-08-04）**：`_send_sse` 手动 `Transfer-Encoding: chunked`（HTTP/1.1 标准客户端不再等 EOF 挂起）。
- **数据布局**：对话内容存 V5，`v5_memory_id` + `summary` + 拓扑落 `core/memory_v5/data/v5/ui_conversation_tree.json`（`super-conv-2.0` schema）；树 JSON 只存指针（节点 + `cards_meta` 手动卡片元数据 + `links` 显式连接），真实记忆在 `v5.db`。
- **与 V5 集成**：`memory_provider.sync_turn` step 7 内联 HTTP POST `:48920 /api/add_turn` 推送树节点（2026-08-14 恢复）。
- **LLM 路由（2026-08-05 更新；2026-08-18 单模式）**：`/api/chat` **ikaros 单模式**，注入「完整 persona（axiom+SOUL+心绪）+ 树域记忆」。**主链路 = DeepSeek 直连**（`CT_DEEPSEEK_MODEL` 默认 `deepseek-v4-flash`），先预检索记忆（memory_search）再把结果作为 system 前缀喂给 LLM，工具回路为**只读工具集**（memory_search 等），模型可自主选择调用工具（`MAX_TOOL_ROUNDS` 上限）；DeepSeek 不可达时降级本地三层 chat 补全链路，并通过 SSE `warn` 事件（黄色提示条）向前端提示降级。SSE 透出 `content / reasoning / tool 生命周期(含结果) / usage`；工具结果截断 2000 透出。
- **触控/平板模式（2026-08-10）**：全局 touch-action 策略（手势区 none：画布/节点/卡组头/白板/3D 查看器；滚动区 pan-y + overscroll-behavior:contain）；画布单指平移/双指捏合 + 可交互元素分流（不拦截、保留原生 mouse 合成）；长按手势（~500ms：树节点/组卡 → 右键菜单、L1 小卡 → 直接 L3；位移 10px 取消；吃原生 contextmenu/click 防双菜单）；卡组头部 pointer 事件拖拽（鼠标/触控/笔统一）；触控最小目标 ≥40px（`@media (pointer:coarse)`）；软键盘避让（visualViewport → `--kb-h` 输入区上移）；safe-area-inset 边距（刘海屏）；平板竖屏树面板收窄（--tree-w:220px）。
- **已知限制（2026-08-01 更新）**：`skills_used` 用「本轮工具名列表」近似落库（gateway 无 skill 专属事件源，精确元数据待 gateway 侧补事件）；`build_tree_aware_context` 树感知压缩已修复可用（原漏 import 致 NameError 被静默吞掉，实际一直走线性回退）；`MemoryRetriever._node_memories` 已持久化（`memory_ids` 字段）。Ikaros 人格由 `build_system_prompt` / chat tree 使用（dsh overlay persona 独立）。
- **万用工具卡组（Artifact Deck，2026-08-10）**：agent 在 markdown 正文输出 `:::card TYPE` 块（`key: value` 属性行 + 可选正文，`:::` 闭合），前端**抽取进独立卡组** `#cardDeck`（不在 chat 正文内嵌，正文只留 chip 占位链接）。卡组悬浮画布右侧、可拖拽，与 chat 卡同构三态：**L1 = 90×60 小卡**（未调用态，堆叠成卡组，>3 张时只露 3 张 +N 徽章）、**L2 = 中等面板**（只读展示）、**L3 = 全功能面板**（可交互，如 browser 地址栏）。自动布局：1~3 张全 L2 展开；>3 张时被调用的卡展开、其余收缩 L1。被调用卡（active）环绕**淡蓝→粉流光阴影**动画。卡组展开时**让位**：chat 卡（L2/L3）宽度收缩 `--deck-w` 被往左挤。类型：`browser`（Mini 浏览器 iframe）/ `file`（image/text/pdf/audio/video/markdown 预览）/ `whiteboard`（SVG 白板，DSL：`node id 标签` / `link a -> b 说明`）/ `emoji`（大表情）/ `animation`（typing/float/pulse/spin/bounce/rainbow/wave/heartbeat）/ `model`（3D glb 预览卡）/ `audio` / `video` / `code`（大代码框）/ `note|info|warn|ok`（提示条）。安全：属性全 escapeHtml，iframe/媒体 src 走 http(s) 白名单，白板 DSL 走 textContent，零脚本执行；与正文同走 `mdRender` 抽取链路（流式实时 + 持久化重渲染双路径自动生效，降级链路同样可用）。工具生命周期卡（`tool_call`/`tool_result` 三态 running/ok/fail，emoji 取工具事件）仍在 chat 卡 `renderExtras`/`extrasHtml`。语法参考见 `docs/conversation-tree-cards.md`。
- **卡片图重构（2026-08-15/16，poker 对齐）**：把「节点树」升级为「卡片图」——`ConvCard`（卡片 = 一段多轮会话，`messages` 数组；底层 `node=回合` 仍是事实源，V5 store 零改动）。卡片由节点链按**分叉点切分自动聚合**（卡片头 = ROOT + 每个分叉点 ≥2 子的孩子）；手动建卡（child/parallel/branching）与分支点/未读经 `cards_meta` 持久化合并。**显式连接图**：卡片独立存在，关系 = 显式 `links`（多对多，可断开，`link_cards`/`unlink_cards`），前端出锚点(右/下)→入锚点(左/上)拖线连接（`onAnchorDown`/`onLinkMove`/`onLinkUp`）；`viewMode` 收敛为单一 `card` 视图（旧 node/group 模式移除）。**分支继承链**：branching 卡经 `get_branch_chain` 回溯继承链（`branching_source_message_id` 定位分支点），`build_branch_chain_block` 注入 system prompt（ikaros 单模式）。消息 id（`_ensure_message_ids`/`_strip_msg_ids`）供分支点/发散点定位；LLM 上下文与导出剥离 id（OpenAI 兼容 + 稳定格式，导出-导入往返闭环）。前端 `TreeView`（`cards`+`links`）+ `installState` snake→camel 规范化 + `renderCardEdges` 连线渲染（端点几何对齐实测 0.00px，62 服务测试 + 47 引擎测试全绿）。

### 1.5 工作引擎接入：dsh (DeepSeek Harness)（2026-08-18 起）

agent 底座 = **DeepSeek Harness (dsh)**，Ikaros 定制全部走 **overlay**（0 源码侵入）：

- **overlay**：`core/ikaros-dsh/cordis.patch.yml`（路径经 `!!js process.env.IKAROS_ROOT` 推导，0 硬编码）——注入 memory_v5 MCP（stdio，48 个 `v5_*` 工具）+ 持久 PTY 终端（terminal / terminal-bash / tool-terminal）+ LSP 精确导航（lsp / lsp-stdio / tool-lsp）+ Ikaros 工作引擎 persona（system-prompt 覆盖）。
- **插件**：`core/ikaros-dsh/plugins/ikaros-memory`（recallMemory / writeMemory，替代旧 hermes ikaros_v5 插件职责）。
- **启动**：`bin/start-dsh-ikaros.bat web|headless`；重启 `bin/restart-dsh-ikaros.ps1`（杀旧 dsh web + --patch 重启，日志 `data/logs/ikaros-dsh-restart.log`）。⚠️ 重启中断当前 Web 会话，刷新 :3080 从持久化会话恢复。
- **架构参考**：`docs/ikaros-dsh-plugin-architecture.md`；退役历史：`docs/hermes-retirement-inventory.md`。

### 2.1 核心设计：零系统依赖

Ikaros 运行所需的所有运行时环境**全部打包在项目目录内**，不依赖系统安装的 Python/Node/Rust。

```
E:\Ikaros\runtime\              ← 便携运行时根目录
├── portable-python\            ← Python 3.12.10 (自带 pip / site-packages)
│   ├── python.exe              ← 主解释器
│   └── Scripts\                ← pip 安装的可执行脚本
├── node\                       ← Node.js (node.exe, v26.3.0)
├── bun\                        ← Bun (omp/oh-my-pi 可执行在 bin\omp.exe, 2026-08-12 便携化)
├── llama\b10000-cuda\          ← llama.cpp (CUDA 13.x)
│   ├── llama-server.exe
├── llama\b10000-cuda-12.4\     ← llama.cpp (CUDA 12.4，低版本驱动设备)
│   └── llama-server.exe
├── rust\bin\                   ← 便携 Rust 工具
│   └── cargo.exe
├── herdr\                      ← Herdr coding-agent 终端多路复用器 (headless, 命名管道)
├── hermes-agent\               ← Hermes Agent 工作树 (2026-08-05 从仓库根迁入)
│   └── venv\                   ← Hermes 专属 venv (python -m hermes_cli)
└── MCPServe\                   ← MCP 服务套件
    ├── gitnexus\               ← GitNexus 代码智能 (图索引, CLI + MCP)
    ├── everything\             ← Everything 搜索 (es.exe)
    ├── graphify\               ← 图谱服务 (MCP, 启动慢)
    ├── playwright\
    └── codebase-memory\
```

> **注意**：`runtime/hermes-agent` 是**工作树**（含 venv 与 clone），Hermes 用户态数据（配置/会话/插件/记忆）在 `data/hermes-agent/`（=`$HERMES_HOME`，gitignore）。升级经 `bin/hermes-update-and-patch.py --apply`（克隆更新 + 补丁重放 + 插件外置）。

### 2.2 根路径发现规则（IKAROS_ROOT）

所有脚本不得硬编码 `E:\Ikaros`。根路径通过以下优先级链路发现：

```
1. 环境变量 IKAROS_ROOT（由启动器设置）→ 最高优先级
2. 脚本自身位置推导（__file__ / %~dp0）
   - bat: %~dp0 的父目录
   - Python: Path(__file__).resolve().parent.parent
3. 兼容变量 HERMES_ROOT（旧版兼容）
```

**禁止**: 在任何脚本中写 `root = r"E:\Ikaros"` 这样的硬编码。
**必须**: 使用环境变量或 `__file__` 推导。

### 2.3 环境变量注册表

所有 Agent 可依赖的 `IKAROS_*` 环境变量由 **`bin/ikaros-env.sh` / `bin/ikaros-env.bat`**（权威源，自锚定、可随目录移动）统一设置；Hermes 侧上游同源镜像在 **`data/hermes-agent/.env`**（32 个变量，手工同步）；`core/env/` 保留 Python 侧副本（含 `ikaros-paths.json`）。**注册表 `IKAROS_*` 已清零，勿再 `setx`**。

| 变量名 | 值示例 | 说明 |
|--------|--------|------|
| `IKAROS_ROOT` | `E:\Ikaros` | 项目根（所有路径的锚点） |
| `IKAROS_PYTHON` | `%IKAROS_ROOT%\runtime\portable-python\python.exe` | 便携 Python |
| `IKAROS_NODE` | `%IKAROS_ROOT%\runtime\node\node.exe` | 便携 Node |
| `IKAROS_RUNTIME` | `%IKAROS_ROOT%\runtime` | 运行时根 |
| `IKAROS_RUST` | `%IKAROS_ROOT%\runtime\rust` | 便携 Rust |
| `IKAROS_BIN` | `%IKAROS_ROOT%\bin` | 启动脚本 |
| `IKAROS_CONFIG` | `%IKAROS_ROOT%\config` | 配置 |
| `IKAROS_DATA` | `%IKAROS_ROOT%\data` | 数据 |
| `IKAROS_LOGS` | `%IKAROS_ROOT%\data\logs` | 统一日志目录 |
| `IKAROS_MODULES` | `%IKAROS_ROOT%\modules` | 模块目录（扩展挂载点） |
| `IKAROS_MEMORY` | `%IKAROS_ROOT%\core\memory_v5` | V5 代码根 |
| `IKAROS_MEMORY_DATA` | `%IKAROS_ROOT%\core\memory_v5\data` | V5 数据根（`v5.db`、JSON 拓扑） |
| `IKAROS_MEMORY_MODELS` | `%IKAROS_ROOT%\core\memory_v5\models` | 本地模型目录 |
| `IKAROS_MEMORY_SCRIPT` | `%IKAROS_ROOT%\core\memory_v5\store.py` | V5 存储脚本入口 |
| `IKAROS_MODEL_EMBEDDING` | `...\models\nomic-embed-text-v2-moe.f32.gguf` | Embedding 模型 |
| `IKAROS_MODEL_LLM` | `...\models\Phi-4-mini-instruct-Q4_K_M.gguf` | 本地 LLM 默认模型（实际由 `model_config.json` 决定） |
| `IKAROS_LLAMA_DIR` | `%IKAROS_ROOT%\runtime\llama\b10000-cuda` | llama.cpp 目录（`.bat` 默认 CUDA13；跨设备自动选择见 `core/env/llama_resolver.py`） |
| `IKAROS_LLAMA_SERVER` | `...\b10000-cuda\llama-server.exe` | llama-server 可执行 |
| `IKAROS_LLAMA_VERSION` | `b10000-cuda` | 当前 CUDA 版本标签 |
| `IKAROS_NEKO` | `%IKAROS_ROOT%\apps\neko` | Neko 前端服务根 |
| `IKAROS_NEKO_PYTHON` | `%IKAROS_ROOT%\apps\neko\.venv\Scripts\python.exe` | Neko 独立 venv |
| `IKAROS_NEKO_SERVER` | `app.main_server` | Neko 主服务模块 |
| `IKAROS_NODE_MODULES` | `%IKAROS_ROOT%\runtime\node\node_modules` | 便携 Node 模块 |
| `IKAROS_HERMES_AGENT` | `%IKAROS_ROOT%\runtime\hermes-agent` | Hermes Agent 代码根（relocated from `hermes-agent`） |
| `IKAROS_HERMES_HOME` | `%IKAROS_ROOT%\data\hermes-agent` | Hermes 用户态数据 / 会话 / 插件目录 |
| `IKAROS_LABEL_EMOTION_PROVIDER` | `local` / `deepseek` | 情感标注 LLM |
| `IKAROS_PORT_LLM` | `8080` | 本地 LLM 端口 |
| `IKAROS_PORT_LLAMA` | `8080` | llama-server 端口 |
| `IKAROS_PORT_EMBEDDING` | `8587` | Embedding 端口 |
| `IKAROS_PORT_BRIDGE` | `7860` | 桥接端口（遗留定义，勿与 :8650 Hermes Bridge 混淆） |

### 2.4 PYTHONHOME 安全门

```bat
set "PYTHONHOME="           ← 关键！防止系统 Python 干扰
set "PATH=%IKAROS_RUST%\bin;%IKAROS_LLAMA_DIR%;...;%IKAROS_ROOT%\runtime\portable-python\Scripts;%IKAROS_ROOT%\runtime\portable-python;%PATH%"
```

便携 Python 始终在 PATH 首位，且 `PYTHONHOME` 被显式清空。

### 2.5 Neko 的独立 Venv — ⚠️ 历史，N.E.K.O 已退役 2026-08-18（apps/neko 已删）

`apps/neko/` 使用**独立的 venv**（非 portable-python）：

```
apps/neko/.venv/Scripts/python.exe
```

这是因为 Neko 的依赖（fastapi 0.115, websockets 15.0, SQLAlchemy 等）与 portable-python 的依赖不同。启动 Neko 相关组件时**必须**使用此 venv。

---

## 第三章：路径统一管理（Path Registry）

### 3.1 问题背景

项目早期存在大量分散的路径引用方式：
- 部分代码用 `IKAROS_ROOT` 环境变量
- 部分代码用 `__file__` 相对路径
- 部分代码硬编码 `E:\Ikaros`
- `ikaros-paths.json` 中另有一套绝对路径

这导致 2026-07-24 的目录重组时需要扫描 23,683 个文件来更新 127 处路径。

### 3.2 路径规范（所有 Agent 必须遵守）

#### 规则 A：Python 模块内部路径

V5 模块内使用 `V5_ROOT` 推导模式：

```python
# core/memory_v5/xxx.py
V5_ROOT = Path(__file__).resolve().parent.parent  # → core/memory_v5/
_SELF_PATH = V5_ROOT / "data" / "v5" / "xxx.json"
```

#### 规则 B：bin/ 启动脚本路径

使用 `Parent / "core/memory_v5"` 模式：

```python
# bin/xxx.py
_ROOT = Path(__file__).resolve().parent.parent   # → E:\Ikaros
_v5_path = _ROOT / "core" / "memory_v5"          # → E:\Ikaros\core\memory_v5
```

#### 规则 C：Neko 集成代码路径 — ⚠️ 已失效（N.E.K.O 退役 2026-08-18，apps/neko 已删）

使用 `IKAROS_ROOT` 环境变量：

```python
# apps/neko/main_logic/ikaros_integration.py
_IKAROS_ROOT = Path(os.environ.get("IKAROS_ROOT", os.environ.get("HERMES_ROOT", "E:\\Ikaros")))
_IKAROS_MEMORY = _IKAROS_ROOT / "core" / "memory_v5"
```

#### 规则 D：跨模块引用（sys.path）

```python
# 把 E:/Ikaros/core 插入 sys.path 以便 import memory_v5.xxx
sys.path.insert(0, str(V5_ROOT))   # V5_ROOT = core/memory_v5/
```

### 3.3 路径生成 → 注册 → 查询 流程

```
┌─────────────────────────────────────────────────────────────────┐
│  1. 路径生成                                                      │
│     bin/ikaros-env.sh / bin/ikaros-env.bat（权威源，自锚定）       │
│     → 启动时自动推导 IKAROS_ROOT                                  │
│     → 设置所有 IKAROS_* 环境变量                                   │
│     → core/env/ 镜像副本 + ikaros-paths.json (供 Python 直接读取) │
├─────────────────────────────────────────────────────────────────┤
│  2. 路径注册                                                      │
│     任何新增子路径必须:                                            │
│     a. 在 bin/ikaros-env.sh + bin/ikaros-env.bat 中注册           │
│        set "IKAROS_XXX=%IKAROS_ROOT%\path\to\dir"                 │
│     b. 同步 core/env/ikaros-paths.json 条目（Python 侧查询用）     │
│        "xxx": "%IKAROS_ROOT%/path/to/dir"                         │
│     c. 不允许硬编码路径出现在业务代码中                              │
├─────────────────────────────────────────────────────────────────┤
│  3. 路径查询                                                      │
│     Python: os.environ["IKAROS_XXX"] 或读 ikaros-paths.json      │
│     Bat:    %IKAROS_XXX%                                          │
│     Bash:   $IKAROS_XXX (经 bin/ikaros-env.sh)                    │
└─────────────────────────────────────────────────────────────────┘
```

### 3.4 当前已注册路径清单

| 注册位置 | 环境变量 | 目标路径 |
|---------|---------|---------|
| `bin/ikaros-env.sh/.bat` | `IKAROS_PYTHON` | `runtime/portable-python/python.exe` |
| `bin/ikaros-env.sh/.bat` | `IKAROS_NODE` | `runtime/node/node.exe` |
| `bin/ikaros-env.sh/.bat` | `IKAROS_BIN` | `bin/` |
| `bin/ikaros-env.sh/.bat` | `IKAROS_DATA` | `data/` |
| `bin/ikaros-env.sh/.bat` | `IKAROS_CONFIG` | `config/` |
| `bin/ikaros-env.sh/.bat` | `IKAROS_LLAMA_DIR` | `runtime/llama/<按 CUDA 自动选择>` |
| `bin/ikaros-env.sh/.bat` | `IKAROS_MEMORY` | `core/memory_v5/` |
| `bin/ikaros-env.sh/.bat` | `IKAROS_HERMES_AGENT` | `runtime/hermes-agent/` |
| `bin/ikaros-env.sh/.bat` | `IKAROS_HERMES_HOME` | `data/hermes-agent/` |
| `data/hermes-agent/.env` | 全部 IKAROS_*（同源镜像） | Hermes 进程上游（手工同步） |
| `core/env/ikaros-paths.json` | 全部 IKAROS_* 变量 | 同上 + 子路径（Python 查询用） |
| `__file__` 推导 | `V5_ROOT` | `core/memory_v5/` (仅 V5 内部) |

### 3.5 目录映射（旧 → 新）

| 旧路径 | 新路径 | 迁移日期 |
|--------|--------|---------|
| `Ikaros-memory/` | `core/memory_v5/` | 2026-07-24 |
| `Ikaros-environment/` | `core/env/` | 2026-07-24 |
| `ikaros-dashboard/` | `core/dashboard/` | 2026-07-24 |
| `ikaros-monitor/` | _(已移除)_ | 2026-07-24 |
| `identity/` | `config/identity/` | 2026-07-24 |
| `N.E.K.O-main/` | `apps/neko/` | 2026-07-24 |
| `Ikaros-Control-Panel/` | `core/control-panel/` | 2026-07-24 |

### 3.6 `.bat` 文件铁律

```batch
setlocal 不可用（被 call 的子批中会丢失）
环境变量: if not defined VAR call init.bat
禁 timeout → 用 ping -n N 127.0.0.1 >nul
纯 ASCII 编码（UTF-8 BOM 会导致 cmd 解析失败）
禁 && / ||（在 cmd 某些版本行为不一致）
隐藏窗口: 控制面板用 pythonw.exe，legacy 用 launch-hidden.vbs
```

---

## 第四章：模块说明

### 4.1 core/memory_v5/ — V5 灵魂核心

> **命名与保留契约**：目录 `core/memory_v5/` 内的 Python 包已重命名为 **`memory_v5`**（`import memory_v5`），`sys.path` 须包含 `E:/Ikaros/core`。但以下属于**对外契约、保持不变**：数据库文件仍叫 **`v5.db`**，48 个 MCP 工具仍以前缀 **`v5_*`** 暴露（2026-08-10 实测，含 V5.3 activity/compression、V5.4 project、V5.5 skill 各系列）。**请勿重命名 `v5.db` 或 `v5_*` 工具前缀。**
>
> **依赖环警示（gitnexus `check --cycles` 2026-08-12 发现）**：`core/memory_v5/extensions/temporal_graph.py ↔ core/memory_v5/memory_retrieval.py` 存在循环依赖（temporal_graph 调 `unified_retrieve`/`retrieve_temporal` 挂接，memory_retrieval 引 temporal_graph 的 supersede 闭环）。当前靠 Python 模块级延迟引用未炸，但重构时优先解开（把 supersede 挂接改由 `store.py`/`dissonance.py` 侧回调注入即可）。

| 模块 | 职责 | 入口 |
|------|------|------|
| ~~`orchestrator.py` / `bin/cloud_chat.py`~~ | ⚠️ 2026-08-14：已随重构删除（对话树双模式 + bridge :8650 → gateway :8642 接替 agent 能力；memory_api / v5_* MCP 工具接替记忆读写） | — |
| `self_model.py` | 持久自我模型 + schema 版本 + revision 追踪 + json_lock | `SelfModel.load() / .save()` |
| `affect.py` | 6D PAD+TLS 情感状态 | `AffectState.load() / .save()` |
| `metacog.py` | 真 LLM 内省 + 哲思循环 | `metacog_cycle()` |
| `relationship.py` | 亲密度追踪 (14 天半衰期) | `track_interaction()` |
| `narrative.py` | 月度叙事生成 + 回写 self_model | `generate_narrative()` |
| `dissonance.py` | 认知失调检测 (store 写入后异步) | `detect_dissonance()` |
| `store.py` | v5.db SQLite 存储 (WAL+busy_timeout+重试) + 异步向量同步 + dissonance 异步检测 + json_lock/revision | `store(content, type, ...)` |
| `search.py` | 向量索引 (ChromaDB 集合 `ikaros_v5`, cosine) + 双路 `fused_search` (FTS5+向量, 权重 0.3/0.7) | `fused_search(query)` / `get_vector_index()` |
| `memory_retrieval.py` | **统一检索入口**（`unified_retrieve`，scope=auto/semantic/lexical/graph/tree/temporal；auto 语义不足自动补图扩散路）。FTS5 关键词 + Chroma 向量 + 时间范围融合，权重/阈值由 `preprocess_config.yaml` 控制 | `unified_retrieve(query, scope="auto")`（`retrieve()` 为内部基础路） |
| `entity_graph.py` | 实体图谱 (6 张 `eg_*` 表同库): 抽取(Stage A/B)+传播激活(spreading_activation_search)+整合 | `run_episodic_consolidation()` |
| `validation.py` | 结构化内容守卫 (`V5-0109`): 拦截 LLM 旁白/裸 JSON/栅栏/超长，防污染 v5.db | `is_clean_structured_content()` |
| `reflect/` | 记忆反射: consolidate/distill/llm_client/registry/scheduler。**全走 DeepSeek 云端 (deepseek-v4-flash)**；本地 :8080 仅 agent 按需懒加载，不参与反思认知 | `registry.run_all()` |
| `extensions/` | **上下文压缩与检索增强层**：`token_compressor.py`（委派 `llmlingua` 现成库 + 离线规则回退，消费闲置的 `token_budget`，**已接入** hermes 插件 `on_pre_compress`）、`gated_retrieval.py`（分层门控，TencentDB 思路借鉴，**骨架阶段**）、`temporal_graph.py`（`eg_*` 加 `valid_from/valid_to` + `dissonance` supersede，Graphiti 思路借鉴但 SQLite 原生、不换图库，**已接入 2026-08-01**）。详见 §5.2.5 与 `docs/v5-context-compression.md` | `compress_text()` / `gated_retrieve()` / `apply_migration()` |
| `models/model_config.py` | 本地 LLM 单一配置源: 模型/别名(`local-llm`)/端口(8080)/ctx/gpu_layers，落盘 `model_config.json` | `default_config()` |
| `cogno_5d.py` | 5D 认知增强 (时间/设备/地理/情绪/上下文) | `enrich_reply()` |
| `__init__.py` | V5 版本 5.1.0 + CONTROLLED_KINDS 注册表 (12 kinds) | `validate_state_key()` |

### 4.2 core/ikaros-dsh/ — 工作引擎 overlay

2026-08-18 起为 dsh (DeepSeek Harness) 的 Ikaros 集成层:

- `cordis.patch.yml` — 规范源 overlay: memory_v5 MCP (48 个 v5_* 工具, stdio 挂 `runtime/portable-python/python.exe`)、terminal、typescript LSP、persona。
- 路径全部经 `!!js process.env.IKAROS_ROOT + ...` 推导, 0 盘符硬编码, 可整体移动。
- 用户层 `~/.dsh/profiles/web/cordis.patch.yml` 为自动加载副本, 与规范源保持同步 (启动脚本不传 `--patch`, 避免 duplicate loader)。
- 启动: `bin/start-dsh-ikaros.bat web` (web :3080) / `headless <task>`; 面板 dsh 组件可启停 (kill 精确匹配 dsh CLI 进程, 不误伤 DSH Desktop)。

> 历史: N.E.K.O 前端 (`apps/neko/`) 已于 2026-08-18 随底座退役移除。
### 4.3 bin/ — 启动器和桥接

| 文件 | 职责 |
|------|------|
| `ikaros-control-panel.bat` | 双击启动控制面板 :9100（只起面板后端+开浏览器，不拉全栈） |
| `ikaros-env.sh` / `ikaros-env.bat` | **环境权威源**（自锚定，设置全部 IKAROS_*；Hermes 上游镜像 `data/hermes-agent/.env`） |
| ~~`cloud_chat.py`~~ | ⚠️ 2026-08-14 已删除（companion 主链由对话树双模式 + bridge :8650 → gateway :8642 接替） |
| `llama-help.py` | llama-server 辅助（`--hotload` 手动热载本地 LLM） |
| `ikaros-soul-sync.py` | V5 → SOUL.md 同步 |
| `hermes_paw_bridge.py` | Hermes Agent 驱动的猫爪桥 (:8088) |
| `import-hermes-to-convtree.py` | Hermes 单会话 → 对话树 (:48920) 导入器 |
| `conversation-tree/server.py` | 对话树面板后端 (REST, :48920) |
| `hermes-bridge.py` | Hermes Bridge 启动器（studio 包装层 :8650，便携 Python 跑 `core/hermes-bridge/server.py`） |
| `hermes-update-and-patch.py` | Hermes 克隆更新 + overlay 补丁重放（`--apply`；9100 面板「更新 Hermes」权威入口） |

---

## 第五章：数据流

### 5.1 对话流

```
用户输入 (对话树 :48920)
  → ikaros 单模式 (build_system_prompt 组装 Ikaros 伴侣人格: 公理 + SOUL + 心绪 + 树域记忆)
    → DeepSeek 直连 (CT_DEEPSEEK_MODEL, 首选; 预检索记忆 → 只读工具回路)
      → 失败回退 → 本地三层 chat 补全链路
        → 本地 :8080 (Phi-4-mini, model_config.json 决定, 懒加载)
  → reply 流式推送 (SSE: content/reasoning/tool 生命周期/usage)
```

### 5.2 记忆流

```
对话/思考产生新信息
  → store.store() [v5.db]
    → 异步 _sync_vector_best_effort() [Chroma]
    → 异步 _run_dissonance_detection() [认知失调]
  → SelfModel.save() [json_lock + revision 检查]
  → affect.json / relationship.json / 其他 state

结构化管线 (consolidate/distill/reflect) 落库前
  → is_clean_structured_content() 守卫 [V5-0109] 拦截旁白/裸JSON/栅栏/超长
```

> 本地 LLM (`:8080`) 采用**懒加载**：看门狗只监测端口，不主动加载模型；agent 首次 `call_llm(provider="local")` 时由 `ensure_local_llm()` 热载入（见 §5.2.3）。

### 5.2.1 记忆库数据布局与现状（2026-07-24 清理后）

记忆库全部落在 `core/memory_v5/data/v5/`。**存储真相源层级（2026-08-14 P5 理顺）**：

1. **`v5.db`（SQLite + FTS5）= 唯一真相源**。`memory` 表存长期记忆条目（含 PAD 情感指纹 `pad_p/a/d`、`character` 角色隔离、`reinforcement/disputation` 证据评分）；另有 `reflections` / `events` / `user_directives` / `anti_repeat` / `project_edges`（类型化项目边）表；实体图谱 6 张 `eg_*` 表（**已启用**：抽取 + 传播激活 + 整合；**时效已接入 2026-08-01**：`valid_from`/`valid_to` 列由 `temporal_graph.apply_migration()` 幂等 ALTER，`dissonance.py` 检测矛盾时 `supersede` 旧事实，检索侧 `unified_retrieve(scope="temporal")` 过滤过期值，见 §5.2.5，零图库依赖）。
2. **`chroma/` = 派生向量索引**（ChromaDB 持久化，**1024 维 `bge-m3` 向量**，由 :8587 嵌入；集合名 `ikaros_v5`，cosine）：**纯派生，可由 v5.db 重建**（维护脚本 `tmp/rebuild_chroma.py`；运行时有 `vector_sync` op 做增量补录）。重建后必须校验非零率（曾因 :8587 全零向量导致 1220 条全零的静默故障，见 AGENTS.md）。
3. **JSON 状态文件（`self_model.json` / `affect.json` / `relationship.json` / `vitality.json` 等）= 灵魂状态，非记忆**：人格/情感/关系/活力的瞬时状态，带 `schema_version` 守卫；与记忆库分离，不做检索融合。

> ⚠️ `v5.db` 无数字化的 schema-version 守卫，版本演进靠 `store.py` 的**幂等 DDL**（`conn()` 中先 `executescript(SCHEMA)` 再按需 `ALTER TABLE ADD COLUMN`）。schema 版本守卫仅存在于 JSON 状态层。
- **三路融合阈值**：`memory_retrieval.retrieve` 的 `min_fused_score` 线上生效值 = **0.3**（在 `core/memory_v5/preprocess_config.yaml` 标定，原 0.6 会把有效召回全过滤掉）；权重 `vector 0.7 / fts 0.3`，时间命中给强初始分 1.0。⚠️ `search.fused_search`（旧双路）已删除（2026-08-14 P1 检索收敛，唯一入口 = `unified_retrieve`）。

> ⚠️ 历史残留：仓库根部曾有一个 0 字节的孤立文件 `core/memory_v5/v5.db`（无任何代码引用，真实库在 `data/v5/v5.db`），已于 2026-07-24 清理删除。

**记忆库清理记录（2026-07-24）**

- 清理前 chroma 共 4057 条，成分：测试/垃圾字符串 154 条（含空内容向量、`test`/`hello`/`over` 等）、V4 旧系统遗留 1465 条（`tags` 以 `v4,` 开头）、精确重复 418 条。
- 清理后 chroma 剩余 **2020 条**真实记忆；`v5.db` 的 `memory` 表测试行（`id=1` "test memory"）已移除，仅余 1 条合法 `activity_reflection`。
- 清理前已整库备份至 `tmp/mem_backup_20260724/`（chroma 全目录 + `v5.db.bak`），误删可恢复。

### 5.2.2 三路融合检索 + 统一路由层（`unified_retrieve` 主入口 / `retrieve` 基础路）

主入口 `unified_retrieve(query, scope="auto", ...)`，`scope` 路由见下文「统一路由层」；基础路 `retrieve(query, time_range?, character?, top_k?, ...)` 三路按 `memory_id` 去重合并、累加分量：

1. **FTS5 关键词** — `store.search(query)`（`:memory_fts` 虚拟表 + 触发器同步，`_sanitize_fts5_query` 防语法错误）。
2. **Chroma 向量** — `get_vector_index().search(query)`（`:8587` 的 `nomic-embed-text-v2-moe` 嵌入，`search_query:` / `search_document:` 任务前缀）。
3. **时间范围** — 仅当传入 `time_range` 时 `store.search_by_time_range`，命中给强初始分 `1.0`（确保过阈值）。

融合计分：`fused = fts*w_fts + vec*w_vec + time*1.0` → 乘时间衰减（下限 0.2）→ 乘类型 boost → 乘频率/反馈 boost（阶段 4：`frequency_weight` log2 加权 / `reinforcement_weight` / `freshness_weight` 7 天新鲜度 / `long_term_boost` 永久记忆，config 可关）→ `exclude` 已知信息置 `-1` → **过滤 `score >= min_fused_score`**（线上 = `0.3`，yaml 标定）。
附加：20s TTL 短缓存（高频短句跳过 embedding）；Vault 兜底（结果 <3 条时回退搜 ThirdSpace `03-知识/`、`02-日记/`）。

> 注：`search.fused_search` 是**另一套**硬编码双路（fts 0.3 / vec 0.7），与 `retrieve` 不是同一份代码；旧调用方 `provider_bridge` 已于 2026-08-14 删除。

**统一路由层（2026-08-01 新增，借鉴 cognee recall auto-scope）**：`unified_retrieve(query, scope=auto|semantic|lexical|graph|tree|temporal)` 是统一检索入口，`scope="auto"` 语义不足时自动补图扩散路（`entity_graph_search`，`graph_min_score` 过滤）；`scope="tree"` 走树域加权（`tree_scoped_retrieve`，需注入 tree+node_id，缺失降级 auto）；`scope="temporal"` 走 `retrieve_temporal` 过滤已失效事实。调用方（`memory_api` fuse 路径、conversation-tree 的 `memory_search` 工具、tree_adapter）已全部切到统一入口；⚠️ `rules_retriever` 已于 2026-08-14 删除（孤儿, 无代码调用; `docs/agent-rules.yaml` 暂未消费）。

### 5.2.3 LLM 后端与懒加载

| 后端 | 配置 | 路由 |
|------|------|------|
| 云端 DeepSeek | `DEEPSEEK_BASE_URL=https://api.deepseek.com`、`DEEPSEEK_MODEL=deepseek-v4-flash`、`DEEPSEEK_API_KEY` (env) | `call_llm()` / `call_llm_auto()` —— **反思/摘要/对话默认走云端** |
| 本地 :8080 | `LOCAL_LLM_URL` (默认 `http://127.0.0.1:8080`)、别名 `local-llm`、模型由 `models/model_config.json` 决定（当前 `Qwen3-1.7B Q4_K_M`） | 仅 `call_llm(provider="local")` 触发，**懒加载** |

- **懒加载链路（已改）**：本地 LLM (:8080) 已退役，无自动热载入 / 看门狗。`llm_client.call_llm(provider="local")` → `_call_local` 直接打 :8080（调用前须确保服务已自行启动），不再经 `bin/wd_import.py` / `ensure_local_llm` 热载入。
- **本地小模型已从 V5 认知管线移除（2026-07-26）**：`call_llm_auto` 纯云端；`consolidate/distill/reflect` 全部 `provider="deepseek"`，`:8080` 不参与反思。
- ⚠️ 内部不一致待修：`summary.py` 配置仍写 `model: local-llm`（与 `preprocess_config.yaml` 默认同），实际却调 `provider="deepseek"` —— 以代码为准。

### 5.2.4 结构化内容守卫（`validation.py`）

`StructuredContentGuard` 拦截**不应落库**的结构化管线输出，防 LLM 旁白/垃圾污染 `v5.db`：

- 旁白前缀（`NARRATION_PREFIXES`："好的，"/"当然，"/"以下是"…、`okay`/`sure` 等英文）；
- 原始 JSON / Markdown 栅栏（content 以 `{`/`[` 开头或含 ` ``` `）；
- 超长（`MAX_STRUCTURED_LEN = 800`）。

API：`guard_structured_content(content) → list[ValidationError]`、`is_clean_structured_content(content) → bool`，错误码 `ErrorCode.IN_STRUCTURED_MALFORMED = "V5-0109"`。
消费方：**`consolidate` / `distill` / `reflect` 三条结构化管线**在落库前各调用一次（Task #15）。`store()` 自身另走通用输入校验 `validate_memory`/`check_and_log`，非此守卫。

### 5.2.5 上下文压缩与检索增强层（extensions/，2026-08-14 融汇裁决后：token_compressor + temporal_graph + ontology_align + tree_adapter 已接入；gated_retrieval 已删）

V5 现有（5.1.0）的上下文缩减手段只有三类：LLM 摘要旧轮（`summary.py`，20 轮才触发）、委托 Hermes `ContextCompressor` 压 transcript 中段、以及 `token_budget` 的**硬截断**（该配置此前在 `preprocess_config.yaml` 定义却**未被主检索代码消费**）。为补齐相对 TencentDB / LLMLingua / Graphiti 的差距，`core/memory_v5/extensions/` 下规划了三层增强骨架。**2026-08-14 P4 裁决后的接入状态**：

1. **`token_compressor.py` — token 级压缩（相对 LLMLingua 的硬缺口）**
   - 核心入口 `compress_text(text, quality="auto")`：装了 `llmlingua` 则委派其 `PromptCompressor.compress_prompt()`（README 实测 11x+ 压缩，对抗 lost-in-the-middle）；未装/离线则回退 `rule_compress`（折叠空白/重复行、删语气 filler、超目标保头尾截中段）。
   - **导入守护 + 离线回退**：U 盘离线便携，不硬依赖 HF 模型权重；`pip install llmlingua` 到 Ikaros venv 联网一次下模型后离线可用缓存。
   - 配套 `compress_old_rounds(tail_keep=6)`（保最近 N 轮原样、压旧轮）、`compress_retrieval_block`（高相关原样、低相关先压再裁，避免「要么全要要么全弃」）、`enforce_budget`（按 score/顺序截到预算内）。
   - **消费此前闲置的 `token_budget` 配置**（min 800 / max 1200 / `char_x` 估算）。
   - **已接入主链路（2026-07-30 验证）**：`data/hermes-agent/plugins/ikaros_v5/memory_provider.py` 的 `on_pre_compress` 用 `compress_retrieval_block(max_chars_per_item=150)` 替换原有 `text[:150]` 硬截断，整段用 `try/except` 包裹，压缩器异常时自动回退原始硬截断。验证见 `tests/test_token_compressor_module.py`（10 项，覆盖离线规则回退）+ `tests/test_token_compressor_integration.py`（2 项，增强/回退双路径）。

2. ~~**`gated_retrieval.py` — 分层检索门控**~~ **已删除（2026-08-14 P4 裁决）**：无调用方的骨架；其「高层优先、按需下钻」思想已被 `context_anchor.should_recall`（调用层门控）+ `type_decay`/`base_weight_factor`（排序层分层）覆盖，无需单独模块。

3. **`temporal_graph.py` — 时效图谱（相对 Graphiti 的缺口，SQLite 原生）**
   - `apply_migration()`：在现有 `eg_*` 表上幂等 `ALTER` 加 `valid_from`/`valid_to`（**不换图库后端**）。
   - `supersede_memory()` / `resolve_dissonance_supersede()`：接在 `dissonance.py` 检测矛盾之后，把冲突旧事实 `valid_to=now` 失效（Graphiti 的「矛盾即更替」）。
   - `retrieve_temporal()`：检索优先有效事实、降权/排除已过期。
   - **已接入主链路（2026-08-01）**：`dissonance._record_dissonance` 末尾接 `resolve_dissonance_supersede`（supersede 闭环生效）+ 对冲突旧记忆 `reinforcement -= 0.5` 降权；`unified_retrieve(scope="temporal")` 路由到 `retrieve_temporal`；新增 `reflect/registry.py` 的 `temporal_extract` op（24h，LLM 抽时间戳写 `valid_from`，fail-open）与 `memory_promote` op（6h，两档记忆桥接）。
4. **`ontology_align.py`**（difflib 轻量本体对齐，`cache.ontology_align_enabled` 控制）→ 已接入 `search.entity_graph_search` 实体候选。
5. **`tree_adapter.py`**（树域加权检索）→ 已接入 `unified_retrieve(scope="tree")`。

> ⚠️ **架构决策（2026-07-30 固化）**：V5 **永久留在 SQLite（`v5.db`），不迁移任何图数据库后端**（Neo4j/FalkorDB/Kuzu/Neptune）。`temporal_graph` 仅借鉴 Graphiti 模式（时效窗口 + 自动失效）在 SQLite 上复刻，目标拿下 80%+ 功能、0 架构迁移；精确 relation_type 级 supersede、双时间追踪历史视图可放弃。`token_compressor` 继续使用 `llmlingua` 现成库（导入守护 + 离线回退），与此决策不冲突。详细接入点 / 风险见 `docs/v5-context-compression.md` 与 `core/memory_v5/extensions/EXTENSIONS.md`。

---

## 第六章：核心规则摘要

### 6.1 路径规则

```
1. 不得硬编码 E:\Ikaros 或任何绝对路径
2. 所有根路径通过 IKAROS_ROOT 环境变量或 __file__ 推导
3. 新增子路径必须在 bin/ikaros-env.sh + bin/ikaros-env.bat 注册，并同步 core/env/ikaros-paths.json + data/hermes-agent/.env（Hermes 上游）
4. Python 代码优先用 Path(__file__).resolve() 相对推导
5. bin/ 的路径用 parent.parent + "core/memory_v5" 模式
6. Neko 集成代码用 os.environ.get("IKAROS_ROOT")
```

### 6.2 导入规则

```
1. V5 模块顶部自举: sys.path.insert(0, str(V5_ROOT))
2. V5_ROOT = Path(__file__).resolve().parent.parent
3. 测试用: python -m pytest core/memory_v5/tests/...
4. Git Bash 路径用 E:/... 而非 /e/... (Windows Git Bash 行为)
```

### 6.3 Python 运行环境规则

```
1. portable-python 用于所有 Ikaros 原生组件
   → E:\Ikaros\runtime\portable-python\python.exe
2. Hermes Agent venv 用于 Hermes Agent 相关操作（2026-08-05 已迁至 `runtime/hermes-agent/`）
   → E:\Ikaros\runtime\hermes-agent\venv\Scripts\python.exe
3. Neko venv 用于 Neko 前端相关操作
   → E:\Ikaros\apps\neko\.venv\Scripts\python.exe
4. 不得在三个 venv 间混合使用
5. PYTHONHOME 必须为空 (set "PYTHONHOME=")
```

### 6.4 V5 数据层规则

```
1. schema_version: 5.1.0 (定义在 core/memory_v5/__init__.py)
2. 受控 kinds: 12 个 (CONTROLLED_KINDS 注册表)
3. 写 self_model.json 必须过 json_lock + revision 检查
4. 写 v5.db 必须用 store() (含 WAL + busy_timeout + 重试)
5. 所有 *.json 写操作: 先写 tmp 文件 + os.replace 原子替换
```

### 6.5 Windows 特有规则

```
1. 停进程: SIGTERM 不可靠 → 用 taskkill /F /T 或按端口强杀
2. 隐藏窗口: 控制面板用 pythonw.exe，legacy 用 launch-hidden.vbs
3. MCP 原生 stdout 提取: 套 Python subprocess 包装 (mcp_wrapper.py)
4. GitHub release 下载: 走 gopeed (bin/ikaros-fastdl.py)，不走 urllib
```

### 6.6 协作约定

```
1. 临时文件一律放 E:\Ikaros\tmp\
2. 不自动 push。等"等哥哥一句 commit"指令
3. 下载用 bin/ikaros-fastdl.py + bin/fastdl.json (gopeed :9999 → aria2c → urllib)
   --mirror hf → hf-mirror.com
```
