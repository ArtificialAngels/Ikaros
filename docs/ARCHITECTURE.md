# Ikaros 架构文档

> **目标读者**: 所有接入本项目的 AI Agent
> **核心原则**: 便携性（零系统依赖）+ 路径统一管理（一处注册，全局可查）
> **最后更新**: 2026-07-30

---

## 第一章：项目概览

Ikaros 是一个**完全自包含的 AI 桌宠系统**，核心引擎为 V5 灵魂核心，前端为 N.E.K.O (Neko) Electron 壳，运行时环境全部打包在项目目录内，零系统依赖。

### 1.1 组件化微服务 + 逻辑职责分层

> **重要**：下面的 L0–L3 **不是结构边界，而是逻辑职责分组**。Ikaros 是一组**相互独立的进程（组件化微服务）**，组件间仅通过 **HTTP 接口 + 环境变量** 耦合，**不存在被强制执行的跨层接口**。每个组件可单独启动/停止，层与层之间不共享内存或调用栈。下列分层只是为了阅读与职责划分方便。

```
┌─────────────────────────────────────────────────────────────┐
│      L3: 表现层 (Presentation) — 逻辑分组                       │
│  core/control-panel/ — Electron 桌面壳 (Desktop Shell)        │
│  core/neko/ — 前端服务 (Frontend Service): FastAPI+React      │
│  :48911 main_server  :48912 memory_server  :48915 agent      │
├─────────────────────────────────────────────────────────────┤
│      L2: 智能体层 (Soul) — 逻辑分组                             │
│  core/memory_v5/ — V5 自我认知引擎 (包名 memory_v5)            │
│  orchestrator (双模) → cloud_chat → cogno_5d → metacog      │
│  self_model / affect / relationship / narrative / dissonance │
├─────────────────────────────────────────────────────────────┤
│      L1: 基础设施 (Hermes Infrastructure) — 逻辑分组           │
│  core/hermes/ — Agent 框架 (Skills / MCP)                    │
│  hermes Dashboard :9119 — 云端 LLM 网关                     │
│  bin/ikaros-memory-watchdog — 本地 LLM :8080 + Embed :8587   │
├─────────────────────────────────────────────────────────────┤
│      L0: 运行时层 (Portable Runtime) — 逻辑分组                │
│  runtime/portable-python/ — Python 3.12.10                  │
│  runtime/llama/ — llama.cpp (b10000-cuda)                   │
│  runtime/node/ — Node.js                                    │
│  core/env/ — 环境引导 (路径发现 + 变量注入)                   │
└─────────────────────────────────────────────────────────────┘
```

> **桌面壳 vs 前端服务**：`core/control-panel/` 是 Electron **桌面壳**（拉起面板 `:9100` 与各组件）；`core/neko/` 是 **前端服务**（FastAPI + React，其 `N.E.K.O.exe` 即 neko 壳）。二者职责不同，勿混为一谈。

### 1.2 核心端口一览

| 端口 | 服务 | 路径 | 启动方式 |
|------|------|------|---------|
| :9100 | 控制面板 Web UI | `core/dashboard/server.py` | `bin/ikaros-control.bat` |
| :8587 | Embedding (本地) | `bin/ikaros-memory-watchdog.py` | 面板 memory 组件 |
| :8080 | 本地 LLM (Qwen3-1.7B, **懒加载**) | `bin/ikaros-memory-watchdog.py` | 看门狗**仅被动监测端口**，不主动拉起模型；模型由 agent 首次调用本地 LLM 时经 `ensure_local_llm()` 热载入（`bin/llama-help.py --hotload` 可手动触发） |
| :48911 | Neko 主前端 | `core/neko/app/main_server/` (包, `python -m app.main_server`) | 面板 neko 组件 |
| :48912 | Neko 记忆服务器 | `core/neko/app/memory_server/` (包, `python -m app.memory_server`) | 面板 neko_memory |
| :48915 | Neko Agent 服务器 | `core/neko/app/agent_server/` (包, `python -m app.agent_server`) | 面板 neko_agent |
| :9119 | Hermes Dashboard | `core/hermes/.../web_server.py` | 面板 hermes_dashboard |
| :8088 | Hermes-Paw (猫爪) | `bin/hermes_paw_bridge.py` | 面板 qwenpaw |
| :48920 | 对话树面板 (Conversation Tree) | `core/conversation-tree/server.py`（后端引擎 `core/memory_v5/conversation_tree.py`） | 面板 conversation_tree 组件 |
| 命名管道 | Herdr 终端编排 (coding-agent 多路复用器) | `runtime/herdr/herdr.exe`（headless server，命名管道 `\\.\pipe\...`，无 TCP 端口） | 面板 herdr 组件（按需，不随全栈自启） |

> **端口状态**：上述 9 个 TCP 端口为当前生效服务。已移除：语音桥（原端口 7870 / 7871）—— 于 2026-07-24 删除。**`:8642` Hermes API 网关已重新启用**（`bin/hermes-api-server.py`，dashboard 与 chat-tree 复用），请勿删除。`:8080` 为**懒加载**：看门狗仅监测端口，模型在 agent 首次 `call_llm(local)` 时热载入。另：`herdr`（coding-agent 终端多路复用器，2026-07-29 经 Path B 接入）使用**命名管道**而非 TCP 端口，作为 `herdr` 组件在 9100 面板独立控制（默认不随全栈自启）；其 supervisor 编排端点与对话树事件流同址于 `:48920`。

### 1.3 控制面板 9100 重构 (2026-07-26)

控制面板（`:9100`）在此次重构后调整如下：

- **local_model / memory 拆分**：原看门狗统一持有的 `:8080`（本地 LLM）与 `:8587`（Embedding）拆分为两个独立卡片 `local_model` 与 `memory`，**两者均可在面板内切换模型**。
- **neko_group 合并**：Neko 原来的 3 个服务（`:48911` / `:48912` / `:48915`）合并为单一 `neko_group`，支持**一键启动**或分别控制。
- **Hermes API 网关（:8642）已重新启用**：由 `bin/hermes-api-server.py` 提供，dashboard 与 chat-tree 复用；Person Sync（人设同步脚本）已删除。
- **hermes → dashboard 别名**：`cloud_chat` 的 `hermes` 云端 provider 现已**别名指向 `dashboard`**（即 Hermes Dashboard `:9119`）。

### 1.4 对话树面板 (Conversation Tree, :48920)

新增于 2026-07-28，由控制面板 `conversation_tree` 组件管理（启动 `core/conversation-tree/server.py --port 48920`）：

- **定位**：Explore.poker 风格的树形对话面板，把多轮 / 分支对话以可折叠树呈现（卡片 + 贝塞尔连线 + 拖拽 + 缩放 + 右键菜单 + 双主题 + splitter + localStorage）。
- **后端引擎**：`core/memory_v5/conversation_tree.py`（`ConversationTree`，33 tests）；REST 接口 `fork` / `conclude` / `merge` / `unmerge` / `abandon` / `full_context`；`build_context_v2`（L0 祖先 + L1 兄弟 + L2 合并，MAX 50）。
- **数据布局**：对话内容存 V5，`v5_memory_id` + `summary` + 拓扑落 `core/memory_v5/data/v5/ui_conversation_tree.json`（`super-conv-2.0` schema）；树 JSON 只存指针，真实记忆在 `v5.db`。
- **与 V5 集成**：`hermes_provider.push_to_conversation_tree()` 在记忆写入后静默推送节点（`core/memory_v5/hermes_provider.py:343`）；`bin/import-hermes-to-convtree.py` 可将 Hermes 单会话（`.hermes_history`）批量导入对话树（需重启服务重载内存树）。
- **LLM 路由**：`/api/chat` 直连 DeepSeek（`DEEPSEEK_KEY` 已配），不经 Hermes 三层路由（与 V5 companion 的 DeepSeek → Hermes → 本地 `:8080` 不同）。
- **已知限制**：前端 `/api/chat` 的 system prompt 写死为通用「Explore」助手，**未接入 Ikaros 人格**（SOUL.md / axiom.md / V5 self_model）；`/api/chat` 不记录 `skills_used` / `tool_calls`；`MemoryRetriever._node_memories` 不持久化。Ikaros 人格目前仅由 `cloud_chat.build_system_prompt`（桌宠）与 Hermes（SOUL.md）使用。

---

## 第二章：便携性（Portability）

### 2.1 核心设计：零系统依赖

Ikaros 运行所需的所有运行时环境**全部打包在项目目录内**，不依赖系统安装的 Python/Node/Rust。

```
E:\Ikaros\runtime\              ← 便携运行时根目录
├── portable-python\            ← Python 3.12.10 (自带 pip / site-packages)
│   ├── python.exe              ← 主解释器
│   └── Scripts\                ← pip 安装的可执行脚本
├── node\                       ← Node.js
│   └── node.exe
├── llama\b10000-cuda\          ← llama.cpp
│   └── llama-server.exe
├── rust\bin\                   ← 便携 Rust 工具
│   └── cargo.exe
├── herdr\                      ← Herdr coding-agent 终端多路复用器 (headless, 命名管道)
└── MCPServe\                   ← MCP 服务套件
    ├── playwright\
    └── codebase-memory\
```

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

所有 Agent 可依赖的 `IKAROS_*` 环境变量由 `core/env/ikaros-env.bat` / `.ps1` 统一设置：

| 变量名 | 值示例 | 说明 |
|--------|--------|------|
| `IKAROS_ROOT` | `E:\Ikaros` | 项目根（所有路径的锚点） |
| `IKAROS_PYTHON` | `%IKAROS_ROOT%\runtime\portable-python\python.exe` | 便携 Python |
| `IKAROS_NODE` | `%IKAROS_ROOT%\runtime\node\node.exe` | 便携 Node |
| `IKAROS_RUNTIME` | `%IKAROS_ROOT%\runtime` | 运行时根 |
| `IKAROS_BIN` | `%IKAROS_ROOT%\bin` | 启动脚本 |
| `IKAROS_CONFIG` | `%IKAROS_ROOT%\config` | 配置 |
| `IKAROS_DATA` | `%IKAROS_ROOT%\data` | 数据 |
| `IKAROS_LLAMA_DIR` | `%IKAROS_ROOT%\runtime\llama\b10000-cuda` | llama.cpp |
| `IKAROS_MODEL_EMBEDDING` | `%IKAROS_ROOT%\core\memory_v5\models\...` | Embedding 模型 |
| `IKAROS_MEMORY` | `%IKAROS_ROOT%\core\memory_v5` | V5 代码 + 数据根 |
| `IKAROS_LABEL_EMOTION_PROVIDER` | `local` / `deepseek` | 情感标注 LLM |
| `IKAROS_MODULES` | `%IKAROS_ROOT%\modules` | 模块目录（扩展挂载点） |
| `IKAROS_LOGS` | `%IKAROS_ROOT%\data\logs` | 统一日志目录 |
| `IKAROS_HERMES_AGENT` | `%IKAROS_ROOT%\core\hermes` | Hermes Agent 代码根（relocated from `hermes-agent`） |
| `IKAROS_HERMES_HOME` | `%IKAROS_ROOT%\data\hermes-agent` | Hermes 用户态数据 / 会话目录 |

### 2.4 PYTHONHOME 安全门

```bat
set "PYTHONHOME="           ← 关键！防止系统 Python 干扰
set "PATH=%IKAROS_RUST%\bin;%IKAROS_LLAMA_DIR%;...;%IKAROS_ROOT%\runtime\portable-python\Scripts;%IKAROS_ROOT%\runtime\portable-python;%PATH%"
```

便携 Python 始终在 PATH 首位，且 `PYTHONHOME` 被显式清空。

### 2.5 Neko 的独立 Venv

`core/neko/` 使用**独立的 venv**（非 portable-python）：

```
core/neko/.venv/Scripts/python.exe
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

#### 规则 C：Neko 集成代码路径

使用 `IKAROS_ROOT` 环境变量：

```python
# core/neko/main_logic/ikaros_integration.py
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
│     core/env/ikaros-env.bat / .ps1                                │
│     → 启动时自动推导 IKAROS_ROOT                                  │
│     → 设置所有 IKAROS_* 环境变量                                   │
│     → 写入 ikaros-paths.json (供 Python 直接读取)                  │
├─────────────────────────────────────────────────────────────────┤
│  2. 路径注册                                                      │
│     任何新增子路径必须:                                            │
│     a. 在 ikaros-env.bat + ikaros-env.ps1 中注册                  │
│        set "IKAROS_XXX=%IKAROS_ROOT%\path\to\dir"                 │
│     b. 在 ikaros-paths.json 中添加条目                             │
│        "xxx": "%IKAROS_ROOT%/path/to/dir"                         │
│     c. 不允许硬编码路径出现在业务代码中                              │
├─────────────────────────────────────────────────────────────────┤
│  3. 路径查询                                                      │
│     Python: os.environ["IKAROS_XXX"] 或读 ikaros-paths.json      │
│     Bat:    %IKAROS_XXX%                                          │
│     PS1:    $env:IKAROS_XXX                                       │
└─────────────────────────────────────────────────────────────────┘
```

### 3.4 当前已注册路径清单

| 注册位置 | 环境变量 | 目标路径 |
|---------|---------|---------|
| `ikaros-env.bat` | `IKAROS_PYTHON` | `runtime/portable-python/python.exe` |
| `ikaros-env.bat` | `IKAROS_NODE` | `runtime/node/node.exe` |
| `ikaros-env.bat` | `IKAROS_BIN` | `bin/` |
| `ikaros-env.bat` | `IKAROS_DATA` | `data/` |
| `ikaros-env.bat` | `IKAROS_CONFIG` | `config/` |
| `ikaros-env.bat` | `IKAROS_LLAMA_DIR` | `runtime/llama/b10000-cuda` |
| `ikaros-env.bat` | `IKAROS_MEMORY` | `core/memory_v5/` |
| `ikaros-paths.json` | 全部 IKAROS_* 变量 | 同上 + 子路径 |
| `__file__` 推导 | `V5_ROOT` | `core/memory_v5/` (仅 V5 内部) |

### 3.5 目录映射（旧 → 新）

| 旧路径 | 新路径 | 迁移日期 |
|--------|--------|---------|
| `Ikaros-memory/` | `core/memory_v5/` | 2026-07-24 |
| `Ikaros-environment/` | `core/env/` | 2026-07-24 |
| `ikaros-dashboard/` | `core/dashboard/` | 2026-07-24 |
| `ikaros-monitor/` | _(已移除)_ | 2026-07-24 |
| `identity/` | `config/identity/` | 2026-07-24 |
| `N.E.K.O-main/` | `core/neko/` | 2026-07-24 |
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

> **命名与保留契约**：目录 `core/memory_v5/` 内的 Python 包已重命名为 **`memory_v5`**（`import memory_v5`），`sys.path` 须包含 `E:/Ikaros/core`。但以下属于**对外契约、保持不变**：数据库文件仍叫 **`v5.db`**，40 个 MCP 工具仍以前缀 **`v5_*`** 暴露。**请勿重命名 `v5.db` 或 `v5_*` 工具前缀。**

| 模块 | 职责 | 入口 |
|------|------|------|
| `orchestrator.py` | agent/companion 双模路由 | `agent_loop(history=[])` |
| `bin/cloud_chat.py` | companion 主链 + system prompt 组装 + LLM 路由 | `cloud_chat_sync(text, history)` |
| `self_model.py` | 持久自我模型 + schema 版本 + revision 追踪 + json_lock | `SelfModel.load() / .save()` |
| `affect.py` | 6D PAD+TLS 情感状态 | `AffectState.load() / .save()` |
| `metacog.py` | 真 LLM 内省 + 哲思循环 | `metacog_cycle()` |
| `relationship.py` | 亲密度追踪 (14 天半衰期) | `track_interaction()` |
| `narrative.py` | 月度叙事生成 + 回写 self_model | `generate_narrative()` |
| `dissonance.py` | 认知失调检测 (store 写入后异步) | `detect_dissonance()` |
| `store.py` | v5.db SQLite 存储 (WAL+busy_timeout+重试) + 异步向量同步 + dissonance 异步检测 + json_lock/revision | `store(content, type, ...)` |
| `search.py` | 向量索引 (ChromaDB 集合 `ikaros_v5`, cosine) + 双路 `fused_search` (FTS5+向量, 权重 0.3/0.7) | `fused_search(query)` / `get_vector_index()` |
| `memory_retrieval.py` | **三路融合检索** 入口 (FTS5 关键词 + Chroma 向量 + 时间范围)，权重/阈值由 `preprocess_config.yaml` 控制 | `retrieve(query, ...)` |
| `entity_graph.py` | 实体图谱 (6 张 `eg_*` 表同库): 抽取(Stage A/B)+传播激活(spreading_activation_search)+整合 | `run_episodic_consolidation()` |
| `validation.py` | 结构化内容守卫 (`V5-0109`): 拦截 LLM 旁白/裸 JSON/栅栏/超长，防污染 v5.db | `is_clean_structured_content()` |
| `reflect/` | 记忆反射: consolidate/distill/llm_client/registry/scheduler。**全走 DeepSeek 云端 (deepseek-v4-flash)**；本地 :8080 仅 agent 按需懒加载，不参与反思认知 | `registry.run_all()` |
| `extensions/` | **上下文压缩与检索增强层（骨架，尚未接入主链路）**：`token_compressor.py`（委派 `llmlingua` 现成库 + 离线规则回退，消费闲置的 `token_budget`）、`gated_retrieval.py`（分层门控，TencentDB 思路借鉴）、`temporal_graph.py`（`eg_*` 加 `valid_from/valid_to` + `dissonance` supersede，Graphiti 思路借鉴但 SQLite 原生、不换图库）。详见 §5.2.5 与 `docs/v5-context-compression.md` | `compress_text()` / `gated_retrieve()` / `apply_migration()` |
| `models/model_config.py` | 本地 LLM 单一配置源: 模型/别名(`local-llm`)/端口(8080)/ctx/gpu_layers，落盘 `model_config.json` | `default_config()` |
| `cogno_5d.py` | 5D 认知增强 (时间/设备/地理/情绪/上下文) | `enrich_reply()` |
| `__init__.py` | V5 版本 5.1.0 + CONTROLLED_KINDS 注册表 (12 kinds) | `validate_state_key()` |

### 4.2 core/neko/ — 前端表现层

| 服务器 | 端口 | 职责 |
|--------|------|------|
| `app/main_server/` (`python -m app.main_server`) | :48911 | 主 HTTP + WebSocket + 静态文件 |
| `app/memory_server/` (`python -m app.memory_server`) | :48912 | 持久记忆服务器 |
| `app/agent_server/` (`python -m app.agent_server`) | :48915 | Agent/Tool 执行服务器 |

集成点: `main_logic/ikaros_integration.py` → 对接 Ikaros V5 orchestrator。

### 4.3 bin/ — 启动器和桥接

| 文件 | 职责 |
|------|------|
| `ikaros-control.bat` | 双击启动控制面板 :9100 |
| `ikaros-control-panel.bat` | 启动 Electron 桌面壳 + 面板后端 |
| `cloud_chat.py` | V5 companion 主链 (LLM 路由 + 高信号检测) |
| `ikaros-memory-watchdog.py` | 管理 :8587 embed + :8080 LLM (CFG 退避) |
| `ikaros-soul-sync.py` | V5 → SOUL.md 同步 |
| `hermes_paw_bridge.py` | Hermes Agent 驱动的猫爪桥 (:8088) |
| `import-hermes-to-convtree.py` | Hermes 单会话 → 对话树 (:48920) 导入器 |
| `conversation-tree/server.py` | 对话树面板后端 (REST, :48920) |

---

## 第五章：数据流

### 5.1 对话流

```
用户输入 (Neko 前端 :48911)
  → main_logic/core.py (ikaros_integration 嫁接点)
    → orchestrator.run(history=...) [companion 模式]
      → cloud_chat.py (system prompt 组装 + LLM 路由)
        → Hermes Dashboard :9119 (DeepSeek 云端, 首选)
          → 失败回退 → 本地 :8080 (Qwen3-1.7B)
            → 失败回退 → cloud_chat 返回"走神"文案
      → cogno_5d.enrich_reply() (5D 增强)
    → orchestrator 返回 reply
  → reply 流式推送到 Neko 前端
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

记忆库全部落在 `core/memory_v5/data/v5/`：

- `v5.db`（SQLite + FTS5）：结构化记忆主存储（**唯一真相源**）。`memory` 表存长期记忆条目（含 PAD 情感指纹 `pad_p/a/d`、`character` 角色隔离、`reinforcement/disputation` 证据评分）；另有 `reflections` / `events` / `user_directives` / `anti_repeat` 表；实体图谱 6 张 `eg_*` 表（**已启用**：抽取 + 传播激活 + 整合；**规划中（未迁移）**：拟加 `valid_from`/`valid_to` 时效列，`dissonance.py` 检测矛盾时 `supersede` 旧事实，检索侧 `retrieve_temporal` 过滤过期值，见 §5.2.5，零图库依赖）。
  - ⚠️ `v5.db` 无数字化的 schema-version 守卫，版本演进靠 `store.py` 的**幂等 DDL**（`conn()` 中先 `executescript(SCHEMA)` 再按需 `ALTER TABLE ADD COLUMN`）。schema 版本守卫仅存在于 JSON 状态层（`self_model.json` 等带 `schema_version: 5.1.0`，由 `SelfModel.load` 校验）。
- `chroma/`（ChromaDB 持久化，768 维 `nomic-embed-text-v2-moe` 向量，由 :8587 嵌入）：记忆向量索引，集合名 `ikaros_v5`（cosine），**纯派生，可由 v5.db 重建**（维护脚本 `tmp/rebuild_chroma.py`；运行时有 `vector_sync` op 做幂等全量 upsert 作为崩溃恢复安全网）。运行时代码无自动 HNSW drop-rebuild。
- **三路融合阈值**：`memory_retrieval.retrieve` 的 `min_fused_score` 线上生效值 = **0.3**（在 `core/memory_v5/preprocess_config.yaml` 标定，原 0.6 会把有效召回全过滤掉）；权重 `vector 0.7 / fts 0.3`，时间命中给强初始分 1.0。`search.fused_search` 是另一套硬编码双路（fta 0.3/vec 0.7），由 `provider_bridge` 的 Hermes `v5search` 桥调用。

> ⚠️ 历史残留：仓库根部曾有一个 0 字节的孤立文件 `core/memory_v5/v5.db`（无任何代码引用，真实库在 `data/v5/v5.db`），已于 2026-07-24 清理删除。

**记忆库清理记录（2026-07-24）**

- 清理前 chroma 共 4057 条，成分：测试/垃圾字符串 154 条（含空内容向量、`test`/`hello`/`over` 等）、V4 旧系统遗留 1465 条（`tags` 以 `v4,` 开头）、精确重复 418 条。
- 清理后 chroma 剩余 **2020 条**真实记忆；`v5.db` 的 `memory` 表测试行（`id=1` "test memory"）已移除，仅余 1 条合法 `activity_reflection`。
- 清理前已整库备份至 `tmp/mem_backup_20260724/`（chroma 全目录 + `v5.db.bak`），误删可恢复。

### 5.2.2 三路融合检索（`memory_retrieval.retrieve`）

入口 `retrieve(query, time_range?, character?, top_k?, ...)`，三路按 `memory_id` 去重合并、累加分量：

1. **FTS5 关键词** — `store.search(query)`（`:memory_fts` 虚拟表 + 触发器同步，`_sanitize_fts5_query` 防语法错误）。
2. **Chroma 向量** — `get_vector_index().search(query)`（`:8587` 的 `nomic-embed-text-v2-moe` 嵌入，`search_query:` / `search_document:` 任务前缀）。
3. **时间范围** — 仅当传入 `time_range` 时 `store.search_by_time_range`，命中给强初始分 `1.0`（确保过阈值）。

融合计分：`fused = fts*w_fts + vec*w_vec + time*1.0` → 乘时间衰减（下限 0.2）→ 乘类型 boost → `exclude` 已知信息置 `-1` → **过滤 `score >= min_fused_score`**（线上 = `0.3`，yaml 标定）。
附加：20s TTL 短缓存（高频短句跳过 embedding）；Vault 兜底（结果 <3 条时回退搜 ThirdSpace `03-知识/`、`02-日记/`）。

> 注：`search.fused_search` 是**另一套**硬编码双路（fts 0.3 / vec 0.7），由 `provider_bridge` 的 Hermes `v5search` 桥调用，与 `retrieve` 不是同一份代码。

### 5.2.3 LLM 后端与懒加载

| 后端 | 配置 | 路由 |
|------|------|------|
| 云端 DeepSeek | `DEEPSEEK_BASE_URL=https://api.deepseek.com`、`DEEPSEEK_MODEL=deepseek-v4-flash`、`DEEPSEEK_API_KEY` (env) | `call_llm()` / `call_llm_auto()` —— **反思/摘要/对话默认走云端** |
| 本地 :8080 | `LOCAL_LLM_URL` (默认 `http://127.0.0.1:8080`)、别名 `local-llm`、模型由 `models/model_config.json` 决定（当前 `Qwen3-1.7B Q4_K_M`） | 仅 `call_llm(provider="local")` 触发，**懒加载** |

- **懒加载链路**：看门狗 `bin/ikaros-memory-watchdog.py` 设 `LLM_LAZY=True`，`start_all` 只 `_port_alive(8080)` 监测，不 spawn 模型；agent 调 `llm_client.call_llm(provider="local")` → `_call_local` → `_ensure_local_llm_loaded` → 经 `bin/wd_import.py`（因看门狗文件名含连字符，用 `importlib` 按路径加载）调 `ensure_local_llm(timeout=180)`，detached 进程 + `data/logs/.llama-hotload.lock` 防并发。
- **本地小模型已从 V5 认知管线移除（2026-07-26）**：`call_llm_auto` 纯云端；`consolidate/distill/reflect` 全部 `provider="deepseek"`，`:8080` 不参与反思。
- ⚠️ 内部不一致待修：`summary.py` 配置仍写 `model: local-llm`（与 `preprocess_config.yaml` 默认同），实际却调 `provider="deepseek"` —— 以代码为准。

### 5.2.4 结构化内容守卫（`validation.py`）

`StructuredContentGuard` 拦截**不应落库**的结构化管线输出，防 LLM 旁白/垃圾污染 `v5.db`：

- 旁白前缀（`NARRATION_PREFIXES`："好的，"/"当然，"/"以下是"…、`okay`/`sure` 等英文）；
- 原始 JSON / Markdown 栅栏（content 以 `{`/`[` 开头或含 ` ``` `）；
- 超长（`MAX_STRUCTURED_LEN = 800`）。

API：`guard_structured_content(content) → list[ValidationError]`、`is_clean_structured_content(content) → bool`，错误码 `ErrorCode.IN_STRUCTURED_MALFORMED = "V5-0109"`。
消费方：**`consolidate` / `distill` / `reflect` 三条结构化管线**在落库前各调用一次（Task #15）。`store()` 自身另走通用输入校验 `validate_memory`/`check_and_log`，非此守卫。

### 5.2.5 上下文压缩与检索增强层（extensions/，token_compressor 已接入、另两项骨架阶段）

V5 现有（5.1.0）的上下文缩减手段只有三类：LLM 摘要旧轮（`summary.py`，20 轮才触发）、委托 Hermes `ContextCompressor` 压 transcript 中段、以及 `token_budget` 的**硬截断**（该配置此前在 `preprocess_config.yaml` 定义却**未被主检索代码消费**）。为补齐相对 TencentDB / LLMLingua / Graphiti 的差距，`core/memory_v5/extensions/` 下规划了三层增强骨架：**`token_compressor` 已接入 hermes 插件 `on_pre_compress`（guard + 异常回退，替换原 `text[:150]` 硬截断，已通过集成沙箱测试）；`gated_retrieval` / `temporal_graph` 仍为骨架，未并入主链路**：

1. **`token_compressor.py` — token 级压缩（相对 LLMLingua 的硬缺口）**
   - 核心入口 `compress_text(text, quality="auto")`：装了 `llmlingua` 则委派其 `PromptCompressor.compress_prompt()`（README 实测 11x+ 压缩，对抗 lost-in-the-middle）；未装/离线则回退 `rule_compress`（折叠空白/重复行、删语气 filler、超目标保头尾截中段）。
   - **导入守护 + 离线回退**：U 盘离线便携，不硬依赖 HF 模型权重；`pip install llmlingua` 到 Ikaros venv 联网一次下模型后离线可用缓存。
   - 配套 `compress_old_rounds(tail_keep=6)`（保最近 N 轮原样、压旧轮）、`compress_retrieval_block`（高相关原样、低相关先压再裁，避免「要么全要要么全弃」）、`enforce_budget`（按 score/顺序截到预算内）。
   - **消费此前闲置的 `token_budget` 配置**（min 800 / max 1200 / `char_x` 估算）。
   - **已接入主链路（2026-07-30 验证）**：`core/hermes/plugins/memory/ikaros_v5/__init__.py` 的 `on_pre_compress` 用 `compress_retrieval_block(max_chars_per_item=150)` 替换原有 `text[:150]` 硬截断，整段用 `try/except` 包裹，压缩器异常时自动回退原始硬截断。验证见 `tests/test_token_compressor_module.py`（10 项，覆盖离线规则回退）+ `tests/test_token_compressor_integration.py`（2 项，增强/回退双路径）。

2. **`gated_retrieval.py` — 分层检索门控（相对 TencentDB 的缺口）**
   - `gated_retrieve()`：默认**只注入高层**（`self_model.get_self_prompt()` + distill/reflect 层记忆），仅在查询实质化且预算剩余时才**下钻** `retrieve()` 拉低层细节。
   - 抓 TencentDB L0-L3「默认注高层、按需下钻」模式的精髓，不引入其调度基础设施。

3. **`temporal_graph.py` — 时效图谱（相对 Graphiti 的缺口，SQLite 原生）**
   - `apply_migration()`：在现有 `eg_*` 表上幂等 `ALTER` 加 `valid_from`/`valid_to`（**不换图库后端**）。
   - `supersede_memory()` / `resolve_dissonance_supersede()`：接在 `dissonance.py` 检测矛盾之后，把冲突旧事实 `valid_to=now` 失效（Graphiti 的「矛盾即更替」）。
   - `retrieve_temporal()`：检索优先有效事实、降权/排除已过期。

> ⚠️ **架构决策（2026-07-30 固化）**：V5 **永久留在 SQLite（`v5.db`），不迁移任何图数据库后端**（Neo4j/FalkorDB/Kuzu/Neptune）。`temporal_graph` 仅借鉴 Graphiti 模式（时效窗口 + 自动失效）在 SQLite 上复刻，目标拿下 80%+ 功能、0 架构迁移；精确 relation_type 级 supersede、双时间追踪历史视图可放弃。`token_compressor` 继续使用 `llmlingua` 现成库（导入守护 + 离线回退），与此决策不冲突。详细接入点 / 风险见 `docs/v5-context-compression.md` 与 `core/memory_v5/extensions/EXTENSIONS.md`。

---

## 第六章：核心规则摘要

### 6.1 路径规则

```
1. 不得硬编码 E:\Ikaros 或任何绝对路径
2. 所有根路径通过 IKAROS_ROOT 环境变量或 __file__ 推导
3. 新增子路径必须在 ikaros-env.bat + .ps1 + .json 三处注册
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
2. Hermes Agent venv 用于 Hermes Agent 相关操作（正在迁至 `core/hermes/`，路径以实际为准）
   → E:\Ikaros\core\hermes\venv\Scripts\python.exe
3. Neko venv 用于 Neko 前端相关操作
   → E:\Ikaros\core\neko\.venv\Scripts\python.exe
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
