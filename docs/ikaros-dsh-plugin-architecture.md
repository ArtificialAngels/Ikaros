# Ikaros → dsh 插件化架构方案

> 状态：评估草案（未动代码）。目标是把 Ikaros 整体做成一个可分发的 DeepSeek Harness 插件，
> 含 **memory_v5（核心记忆引擎）+ 接入层 + UI（一体分发）**，便于其他 dsh 用户 `--patch` 即用。
> UI 的**实现**由另一个窗口推进；本文档只定义目标架构、接口边界与迁移 Phase，不替 UI 窗口做技术决策。

## 1. 目标与范围

**目标**：Ikaros 从一个「整合包」（runtime + core + data + apps 混在一起）演进为
「**一个 npm bundle + 一个 Python 包 + 一套 Client Slot UI**」的组合，可随 `dsh` 安装。

**进插件（一体分发）**：
- `memory_v5` 记忆引擎 —— Ikaros 核心组件，→ 独立 Python 包
- dsh 接入层（召回/写回 + MCP 挂载 + persona）→ npm 包 + bundle
- UI（dashboard / conversation-tree）—— 一体，→ dsh Client Slot 插件

**不进插件（用户自装 / 运行时生成）**：
- `runtime/`（portable-python、node、bun、omp、hermes-agent）—— 环境，非能力
- 模型文件（`memory_v5/models/*.gguf` 共 4.2GB）—— 改为可选下载 / 指向外部推理服务
- `data/`（v5.db、配置、记忆）—— 每用户运行时生成
- hermes 遗留（`core/hermes-bridge`、`patches/hermes`）—— 转 dsh 后废弃

## 2. dsh 分发单位选型

dsh 的「插件」是分层概念，Ikaros 用组合而非单层：

| 层 | 载体 | Ikaros 用途 |
|---|---|---|
| Plugin | `apply(ctx)` Cordis 插件 | 召回/写回（Host）、Slot UI（Client） |
| Package | npm 包 `@ikaros/dsh-ikaros` | 插件的 npm 载体 |
| **Bundle** | `cordis.patch.yml` + `dsh.bundle` 字段 | **分发正解**：挂 MCP/terminal/lsp + 声明 UI 插件 |
| Python 包 | `ikaros-memory`（pip） | memory_v5 本体，经 MCP stdio 被 bundle 拉起 |
| Client Slot | React 组件注册到 `ui-*` Slot | UI 一体集成 |

## 3. 目标架构（目录树）

```
@ikaros/dsh-ikaros                       # npm 包（dsh 插件，Host+Client 双面）
├── package.json                          #   dsh.bundle → cordis.patch.yml；peerDep @deepseek-ai/cordis
├── cordis.patch.yml                      #   bundle：mcp-memory + terminal/lsp + persona + UI 插件行
├── src/host/index.ts                     #   ikaros-memory Host 插件（召回/写回，agent/pre-step + turn-stopping）
├── src/client/index.tsx                  #   Ikaros UI Client 插件（注册 Slot：sidebar/conversation/面板）
└── README.md

ikaros-memory                            # Python 包（pip install ikaros-memory，独立于 dsh）
├── pyproject.toml                        #   chromadb/httpx/mcp/numpy/psutil/pyyaml（6 个）
├── ikaros_memory/                        #   从 core/memory_v5 迁出（store/retrieval/self_model/affect/…35 模块）
├── mcp_server.py                         #   MCP stdio 入口（48 个 v5_* 工具，已验证）
└── models/  data/                        #   默认空；模型可选下载，数据运行时生成
```

## 4. memory_v5 → Python 包（Phase 1 细节）

### 4.1 依赖（已实测，仅 6 个外部包）

```
chromadb  httpx  mcp  numpy  psutil  pyyaml
```
（`fcntl`/`msvcrt` 是平台 stdlib；`services` 是 memory_v5 内部子目录。无 sentence-transformers —— embedding 走外部服务。）

### 4.2 三处解耦

1. **路径硬编码**：`mcp_server.py` 的 `sys.path.insert(0, _V5_ROOT)`、`ikaros_v5` 插件的
   `_resolve_root()`（`__file__.parents[4]` 猜 Ikaros 根）→ 改成标准 `import ikaros_memory`；
   数据目录用 `IKAROS_MEMORY_HOME`（默认 `~/.ikaros-memory`）。
2. **模型耦合**：`memory_v5/models/*.gguf`（4.2GB）不进包 → embedding/LLM 改为**可配置外部服务**
   （OpenAI 兼容端点；Ikaros 指向 :8080/:8587，其他用户指向自己的）。
3. **服务耦合**：`services/` 子目录与 `httpx` 调用（本地 LLM/embedding）→ 走配置注入，
   保持「模型是部署选择，不是插件自带」的 dsh seam 哲学。

### 4.3 包结构（迁移映射）

| 现状 | 目标 |
|---|---|
| `core/memory_v5/*.py`（35 模块） | `ikaros_memory/*.py` |
| `core/memory_v5/tools/`（49 v5_* 函数） | `ikaros_memory/tools/` |
| `core/memory_v5/mcp_server.py` | 包根 `mcp_server.py`（保持 stdio 入口） |
| `core/memory_v5/data/v5/` | `$IKAROS_MEMORY_HOME/v5/`（运行时生成） |
| `core/memory_v5/models/` | 移出包，可选下载脚本单独提供 |

## 5. dsh 接入层（Host 插件）

已在 `core/ikaros-dsh/` 落骨架，事件签名已对照 `packages/core/agent/src/runtime-types.ts`：

- **召回**：`agent/pre-step`（waterfall，须 `next()`）→ `should_recall` 门控 → `agent.inject()`
- **写回**：`agent/turn-stopping`（serial）→ 异步写 `v5_memory_store`
- **MCP**：`dsh-mcp-client` 挂 `ikaros-memory/mcp_server.py`，48 工具 → `mcp__ikaros-v5__v5_*`（已验证可启动可执行）

## 6. UI 一体集成方案（本文档重点，定义边界给 UI 窗口）

### 6.1 dsh 的 UI 机制

dsh UI 是 **Client 插件 + Slot**（React，`React.createElement`，无 JSX 转换）：
`ui-slots`/`ui-layout`/`ui-conversation`/`ui-sidebar`/`ui-settings`/`ui-theme` 等现成 Slot，
Client 插件把 React 组件注册到这些 Slot；Host↔Client 走包内私有 JSON 方法（`harness.handle`/`host.call`）。

### 6.2 两条集成路径（并存，不是二选一）

| 路径 | 做法 | 适用 |
|---|---|---|
| **A 原生 Slot 化** | dashboard/conversation-tree 重写为 React 组件，注册到 dsh 对应 Slot | 长期目标，真正「一体」 |
| **B 服务编排** | 保留 FastAPI 后端，bundle 里 Host 插件 spawn/管理 Python 服务，Client 用 iframe/路由嵌入 | 过渡，快速可用 |

**推荐**：dashboard 走 A（注册到 `ui-sidebar`/`ui-settings` 的 Slot）；conversation-tree 因其
后端（`conversation_tree.py` 树引擎）是 memory_v5 的衍生，走 A 注册到 conversation 面板 Slot，
后端能力经 Host 插件暴露 JSON 方法给 Client 调用（不必是独立 :48920 服务）。

### 6.3 接口边界（本方案承诺给 UI 窗口的契约）

- **数据来源**：UI 不直接碰 `v5.db`，统一经 Host 插件暴露的 JSON 方法（检索/存储/树操作），
  复用 dsh 的 `Model-visible ⟺ logged` 与 `ctx.fs` 沙箱约束。
- **面板身份**：每个 UI 面板 = 一个 Slot 注册 + 一个 `ConversationNodeDefinition`（若进对话流）。
- **生命周期**：UI 副作用（订阅、定时器、样式、主题）全部 `ctx.effect()` 挂当前 Fiber，
  stop/update 可逆 —— 这是 dsh Client 插件的硬要求。

## 7. 迁移 Phase

| Phase | 内容 | 产出 | 依赖 | 由谁 |
|---|---|---|---|---|
| 1 | memory_v5 → `ikaros-memory` Python 包（4.2/4.3） | 可 `pip install` 的包 | 无 | 本侧 |
| 2 | 实现 Host 插件 `recallMemory`/`writeMemory` | 召回/写回跑通 | P1 | 本侧 |
| 3 | `@ikaros/dsh-ikaros` npm 包 + bundle，验证 `--patch` 一键装 | 可分发 | P1+P2 | 本侧 |
| 4 | UI 原生 Slot 化（A） | 面板进 dsh Web | P3 | UI 窗口 |

## 8. 风险与决策点

1. **UI 重写成本**：FastAPI+HTML/Electron → React Slot 是重写，不是搬运；P4 前用路径 B 过渡。
2. **dsh 是 developer preview**（0.1.0-rc.5，`SESSION_FORMAT_VERSION=0` 无兼容承诺）——
   插件要跟上游破坏性变更；策略照搬 hermes 的「纯净 + patch 幂等重打」。
3. **模型/embedding 服务解耦**是 P1 最大行为变更点：Ikaros 现状依赖本地 :8080/:8587，
   包化后必须配置化，否则「给别人用」只是空话。
4. **v5.db 兼容**：迁移只动包结构，DB schema 与 48 个 `v5_*` 工具前缀**不动**（外部契约）。
