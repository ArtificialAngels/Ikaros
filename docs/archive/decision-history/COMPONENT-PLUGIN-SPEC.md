# Ikaros 组件插件接口规范（Component Plugin Spec）

> 状态：规范文档（line3，2026-08-20）。与 `config/components.yaml`（事实源）+ `core/components/registry.py::ComponentSpec`（类型校验）配套。
> 适用范围：line3 工作树的 3 个活跃组件（`dsh` / `conversation-tree` / `embedding`）；herdr (pi/omp) 已于 2026-08-23 退役。
> 阅读对象：注册新组件 / 修改组件元数据 / 编写启动器 / 写 dsh overlay 的开发者与 AI agent。

---

## 0. 一句话目标

**把每个 Ikaros 组件抽象成一份"插件接口契约"，让启动器（`ikarosctl`）、dsh overlay、监控、CI 都能从同一份 YAML 元数据自动派生行为，无需硬编码组件名。**

---

## 1. 组件接口字段定义（YAML Schema）

### 1.1 顶层结构

```yaml
# config/components.yaml
components:
  - id: <string>            # 必需 — 稳定 kebab-case id
    name: <string>          # 必需 — 人类可读显示名
    category: <enum>        # 必需 — memory | ui | runtime | tool | embedding
    port: <int|null>        # 必需 — TCP 端口；非 TCP 传输置 null
    process_marker: <string> # 必需 — OS 进程表匹配子串
    dependencies: [<id>...]  # 可选 — 必须先于本组件启动的 id 列表
    config_schema: {...}    # 可选 — 预留 JSON-Schema 片段（line3 当前全空）
    healthcheck:            # 可选 — 健康检查描述
      type: <port|pipe|http>
      endpoint: <string|int>
      [extra: <任意>]
    lifecycle:              # 可选 — 生命周期命令
      start_script: <string|null>
      stop_script: <string|null>
      restart_script: <string|null>
      watchdog: <self|none|central>
    dsh_integration:        # 可选 — dsh overlay 注入描述
      overlay: <path|null>
      mcp_servers: [<id>...]
```

### 1.2 字段详细语义

| 字段 | 类型 | 必需 | 含义 |
|------|------|------|------|
| `id` | string | ✅ | 稳定 kebab-case 标识（如 `dsh`、`conversation-tree`、`memory_v5`）。**唯一键**——`get_component(id)` 用它查找。**永不重用**（一个 id 退役后不要复用，避免监控/日志错位）。 |
| `name` | string | ✅ | 人类可读显示名。自由文本，用于 UI / 文档 / 日志标签。 |
| `category` | enum | ✅ | 粗粒度角色。当前枚举：`memory` / `ui` / `runtime` / `tool` / `embedding`。`VALID_CATEGORIES` 在 `core/components/registry.py` 强制（loader 会 raise ValueError）。 |
| `port` | int\|null | ✅ | TCP 端口；非 TCP 传输（如命名管道承载的服务）置 `null`。**不要发明默认值**——`null` 是合法值。 |
| `process_marker` | string | ✅ | OS 进程表匹配子串（Windows `tasklist` / POSIX `pgrep`）。例：`dsh`、`embed`、`conversation_tree`。**注意**：`conversation-tree` id 对应的 process_marker 是 `conversation_tree`（Python 模块名 / 文件名约定）。 |
| `dependencies` | list[string] | optional | 必须**先于本组件启动**的 id 列表。顺序信息性——拓扑排序由 caller 负责（Kahn 算法）。**不**自递归依赖。 |
| `config_schema` | dict | optional | 预留 JSON-Schema 片段。line3 当前所有组件都是 `{}`——未启用校验。 |
| `healthcheck.type` | enum | optional | 健康检查类型：`port`（TCP 端口监听）/ `pipe`（命名管道）/ `http`（HTTP endpoint）。 |
| `healthcheck.endpoint` | string\|int | optional | 与 type 配合：`port` → 端口号；`pipe` → 管道路径；`http` → URL path（如 `/healthz`）。 |
| `healthcheck.extra` | any | optional | 额外校验（如 embedding 必填的 `{"probe": "verify-non-zero-vector"}`——line3 当前未实现，仅作示例）。 |
| `lifecycle.start_script` | string\|null | optional | 启动命令（相对 `IKAROS_ROOT`，或绝对路径）。**null** 表示此组件不由启动器直接拉起（依赖其他组件脚本顺带启动，如 embedding）。 |
| `lifecycle.stop_script` | string\|null | optional | 优雅停止命令。line3 当前**全 null**（Ctrl-C / taskkill / `bin/proc.py kill <port>` 兜底）。 |
| `lifecycle.restart_script` | string\|null | optional | 重启命令。dsh 当前用 `bin/restart-dsh-ikaros.ps1`。 |
| `lifecycle.watchdog` | enum | optional | watchdog 策略：`self`=脚本自带 / `none`=无 / `central`=集中（**已退役 2026-08-XX，新组件禁止用**）。详见 `docs/ikaros-launcher-design.md` §5。 |
| `dsh_integration.overlay` | path\|null | optional | 该组件向 dsh 注入的 cordis overlay 路径（相对 IKAROS_ROOT）。仅 dsh 组件有值（如 `core/ikaros-dsh/cordis.patch.yml`），其他组件 `null`。 |
| `dsh_integration.mcp_servers` | list[string] | optional | 该组件经 dsh 注册的 MCP server 名（出现在 cordis patch 的 `id:` 字段）。例如 `dsh` 组件挂 `["ikaros-v5-memory"]`。 |

### 1.3 必需字段最小集（loader 校验）

`registry.py::REQUIRED_FIELDS = ("id", "name", "category", "port", "process_marker")`

缺任一字段 → `ValueError(f"ComponentSpec missing required field(s) {missing!r}")`，**启动器拒绝启动**。

### 1.4 路径约束

- **所有 `lifecycle.*_script` / `dsh_integration.overlay` 都是相对 IKAROS_ROOT**——绝对禁止硬编码 `E:\` / `/home/x/`（教训：`core/env/ikaros-paths.json` 头注释明示）。
- **不存在的脚本路径** → loader 不预检（避免启动器启动慢），但**启动时** subprocess 失败 → 启动器打印 F4 错误（见 launcher doc §7.1）。

---

## 2. 实际组件条目（line3 当前 4 个组件）

> 事实源：`config/components.yaml`（2026-08-19 line3-dsh-base 已就绪）。本节是该文件的语义化对照说明。

### 2.1 `dsh` —— DeepSeek Harness 工作引擎

```yaml
- id: dsh
  name: DeepSeek Harness
  category: tool            # 工具/工作引擎（vs memory/embedding 是底层能力）
  port: 3080                # dsh web 默认端口
  process_marker: dsh       # node bin.js 命令行含 "dsh"
  dependencies: [embedding] # MCP memory-v5 需要 bge-m3 嵌入服务
  config_schema: {}         # 当前无 schema 校验
  healthcheck:
    type: port
    endpoint: 3080          # TCP 端口监听 = 健康（待 dsh 提供 /healthz 后升级为 http）
  lifecycle:
    start_script: bin/start-dsh-ikaros.bat          # Windows bat worker
    stop_script: null                               # 暂无优雅 stop（Ctrl-C / kill 兜底）
    restart_script: bin/restart-dsh-ikaros.ps1      # 杀旧 dsh + --patch 重启（独立进程）
    watchdog: self                                 # bat 内嵌 self-respawn（待实测）
  dsh_integration:
    overlay: core/ikaros-dsh/cordis.patch.yml       # dsh 加载此 overlay 注入 Ikaros 定制
    mcp_servers: [ikaros-v5-memory]                 # MCP stdio → core/memory_v5/mcp_server.py
```

**语义解读**：

- `category: tool`——dsh 是"用户主要交互的工件"，区别于 `embedding`（底层能力）和 `memory`（数据）。这个 category 标签驱动未来 UI 把 dsh 放在主入口。
- `dependencies: [embedding]`——dsh 在启动时挂 MCP memory-v5，MCP server 启动会探活嵌入端口。这意味着 dsh 强依赖 embedding **启动**，但不要求 embedding **健康**（dsh 的 MCP 配置 `failOnStartupError: false`，端口起不来时工具列表为空但不报错）。
- `watchdog: self` + `start_script: bin/start-dsh-ikaros.bat`——bat 内嵌 self-respawn 循环（计划中，待实测），启动器不重复拉起。
- `restart_script` 用 ps1 而非 bat——杀 node.exe 进程 + 重启需要 PowerShell `Get-CimInstance` 跨进程命令行匹配（cmd 的 `wmic` 已废弃）。
- `dsh_integration.overlay: core/ikaros-dsh/cordis.patch.yml`——这是 dsh 的"插件入口"。其他组件（tree/embed）此字段为 `null`。
- `mcp_servers: [ikaros-v5-memory]`——`ikaros-v5-memory` 对应 `cordis.patch.yml` 第 23-35 行的 `id: memory-ikaros-v5` 段（注意 MCP server id 是 `memory-ikaros-v5`，但服务名 `serverName: ikaros-v5`；registry 字段记 `ikaros-v5-memory` 是为了与 v5 工具前缀 `v5_*` 区分的命名约定）。

### 2.2 `conversation-tree` —— 树形对话面板

```yaml
- id: conversation-tree
  name: Conversation Tree Panel
  category: ui              # 用户面板（vs tool 是工作引擎本体）
  port: 48920
  process_marker: conversation_tree   # Python 模块名
  dependencies: [memory_v5]          # 隐式 = 嵌入 embedding（注意：未直接列 embedding）
  config_schema: {}
  healthcheck:
    type: port
    endpoint: 48920
  lifecycle:
    start_script: python core/conversation-tree/server.py --port 48920
    stop_script: null
    restart_script: null             # 重启 = Ctrl-C + 重跑 start_script
    watchdog: self                   # server.py 内嵌端口拉起（Phase 2 计划）
  dsh_integration:
    overlay: null                    # 不向 dsh 注入 overlay
    mcp_servers: []                  # 不挂 MCP（面板内嵌 V5 检索路由）
```

**语义解读**：

- `category: ui`——用户面板，独立于 dsh 工作引擎。dsh 用户也可以开 conversation-tree 做"对话历史回看 + V5 检索"。
- `dependencies: [memory_v5]`——逻辑上是依赖 memory_v5 持久层（SQLite v5.db）。**但当前 YAML 写的是 `memory_v5` 而不是 `embedding`**——因为 `memory_v5` 在 `core/components/` 注册表里**尚未单独条目化**（line3 把 memory_v5 当成 dsh 的子模块而非独立组件）。**这是已知不严格性**：启动器对 `dependencies` 的拓扑排序只检查 `id in components`——如果 `memory_v5` 不在 components 列表，会被**静默忽略**。**修正建议**（line3 实施时）：要么把 memory_v5 升级为独立条目，要么把 conversation-tree 的 dependencies 改为 `[embedding]`。
- `start_script` 用 Python 直接调（不依赖 bat wrapper）——Python 标准库 `ThreadingHTTPServer` 零依赖，跨 shell 一致。
- `process_marker: conversation_tree`（下划线）——Python 模块名约定（`core/conversation-tree/` 是目录，`import conversation_tree` 实际可能不行因为 `core/conversation-tree/` 不在 sys.path；但 server.py 内部 `from conversation_tree import ...` 用的是 `core` 加入 sys.path 后的相对引用）。**注意**：tasklist 里看到的命令行是 `python core/conversation-tree/server.py`，匹配子串用 `conversation_tree` 还是 `conversation-tree`？实测 Windows tasklist 命令行保留原始 `-`（dash），所以 `process_marker: conversation_tree` 可能**匹配不到**。**修正建议**：`process_marker: conversation-tree`（与 id 同形）。
- `watchdog: self` 但 server.py 当前**不**带 watchdog（registry 标了计划项）。**已知 gap**：启动器若按此 schema 相信 `self`，会跳过外部巡检，但 server.py 实际死了也没人拉起。**缓解**：启动器 §5 混合 watchdog 在 Phase 2 实装后，端口巡检会兜底。

### 2.3 `embedding` —— bge-m3 本地嵌入服务

```yaml
- id: embedding
  name: Embedding (bge-m3, local)
  category: embedding       # 底层能力
  port: 8587
  process_marker: embed     # 启动命令行 "llama-server -m ... --embedding"
  dependencies: []          # 无依赖（最底层）
  config_schema: {}
  healthcheck:
    type: port
    endpoint: 8587
    lifecycle:
      start_script: null    # ⚠️ 见下
      stop_script: null
      restart_script: null
      watchdog: self        # 由各上层组件启动脚本顺带拉起（AGENTS.md 2026-08-19）
  dsh_integration:
    overlay: null
    mcp_servers: []
```

**语义解读**：

- `category: embedding`——line3 唯一 category 为 embedding 的组件。`VALID_CATEGORIES` 已枚举此值。
- `dependencies: []`——最底层，无任何依赖；启动拓扑的根。
- `lifecycle.start_script: null`——**关键设计**。embedding 不由启动器直接拉起，而是由 `core/memory_v5/services/start-embedding.bat` 单独存在，由 dsh / conversation-tree 等上层组件脚本"顺带"调用。理由：embedding 是 LLM 共享资源（多个组件可能都用），独立拉起避免重复进程。
- `lifecycle.watchdog: self`——`core/memory_v5/services/start-embedding.bat` 当前是**前台 bat**（无 self-respawn）。**实际语义缺口**：`watchdog: self` 描述的是**未来的设计意图**，不是当前现实。**修正建议**：要么补一个 watchdog 包装脚本（`start-embedding-watched.bat`），要么把 watchdog 字段改为 `none`。
- `process_marker: embed`——llama-server 启动命令行是 `llama-server -m ... --embedding ...`，含 `--embedding` 标志，tasklist 匹配子串 `embed` 命中。注意：这也匹配 `core/memory_v5/mcp_server.py` 的启动命令行（不含 `--embedding` 但文件名不含 embed），所以匹配相对干净。
- **健康校验缺语义**：port + health 不够——必须加 probe 向量非零校验（AGENTS.md 2026-08-14 教训）。当前 schema 没体现；**修正建议**：`healthcheck.extra: {probe: "verify-non-zero-vector"}`，loader 不强制，由启动器自行读取。
- `dsh_integration.mcp_servers: []`——embedding 本身不挂 MCP（dsh 通过 MCP memory-v5 间接触达 embedding）。

### 2.4 ~~`herdr`~~ —— ⚠️ 已退役（2026-08-23）

herdr（Coding-Agent 多路复用器，含 `core/herdr/` 包、`bin/start-omp.bat`、命名管道健康检查）已随 **pi/omp 底座整体退役**：
- `config/components.yaml` 不再登记该组件（当前 3 组件：dsh / conversation-tree / embedding）；
- 底座统一为 **deepseek-harness (dsh) 工作引擎**，外部编码 agent 执行由 dsh overlay（terminal / LSP / MCP）承载；
- `core/herdr/`、`runtime/herdr/herdr.exe`、`data/omp/`、`runtime/bun/omp.exe` 均已移除。

> 若未来需要"外部 agent 复用器"，按本规范新注册组件即可——`process_marker` 与 `healthcheck.type: pipe` 的通用能力仍在 registry 保留（`port: null` 表示无 TCP 端口）。

---

## 3. 跨组件拓扑图

```
                ┌─────────────────┐
                │   embedding     │  ← 最底层 (:8587, llama-server bge-m3)
                │  (bge-m3 q8_0)  │
                └────────┬────────┘
                         │  (MCP server 探活)
                         ▼
                ┌─────────────────┐         ┌──────────────────┐
                │   memory_v5     │ ←────── │  dsh 工作引擎    │
                │  (core module,  │  MCP    │   (:3080)        │
                │  未独立组件化)  │ stdio   │  cordis overlay  │
                └────────┬────────┘         │  + MCP memory    │
                         │                  └──────────────────┘
                         │  (Python import)
                         ▼
                ┌─────────────────┐
                │ conversation-   │  ← 用户面板 (:48920)
                │     tree        │     V5 检索路由（unified_retrieve）
│  (ThreadingHTTP)│
                 └─────────────────┘
```

**注意**：
- `memory_v5` 在拓扑上是 `embedding` 的下游 + `dsh` / `conversation-tree` 的上游；但 components.yaml 当前**未注册** `memory_v5` 为独立条目（被当作 core 子模块）。**line3 实施时建议补全**（详见 §2.2 修正建议 + §6 open questions）。

---

## 4. 注册新组件的流程（line3 实施 SOP）

### 4.1 步骤

1. **写条目**：在 `config/components.yaml` 的 `components:` 列表下加新条目，**填齐 5 个必需字段**（`id` / `name` / `category` / `port` / `process_marker`）。
2. **写 worker**：如果组件需要独立启动器（bat/ps1/sh），写到 `bin/<id>.bat` 或 `core/<id>/start.bat`。路径登记在 `lifecycle.start_script`。
3. **写 watchdog**：根据 §5 launcher 决策，在 worker 脚本里**嵌入语义健康校验**（不能只看端口）。
4. **跑校验**：
   ```sh
   python -m core.components.registry --print   # 见 §5
   ikaros doctor                                # 必须 OK
   ```
5. **跑端到端 smoke**：
   ```sh
   ikaros <id>           # 启动
   # 用实际功能测试一次（v5_memory_search / 树面板聊天 / embedding 检索）
   ikaros status          # 必须显示 healthy
   ikaros stop <id>       # 验证 stop 路径
   ```
6. **更新 AGENTS.md 端口表**：如果开了新端口，必须登记到 `## Ports` 段。

### 4.2 反例（line3 已知的不严格条目）

下面三种情况 line3 当前存在，需在 Phase 2 修：

- ❌ `dependencies` 引用未注册的 id（`conversation-tree` 引 `[memory_v5]`，但 `memory_v5` 不在 components 列表）→ 拓扑排序静默忽略，潜在启动竞态。
- ❌ `process_marker` 与 id 命名不一致（`conversation-tree` 的 marker 是 `conversation_tree`，Windows tasklist 实际命令行含 `-`）→ tasklist 匹配失败，进程查找 bug。
- ❌ `watchdog: self` 但 worker 脚本无自检循环（embedding 当前是前台 bat，无 self-respawn）→ 实际死了没人拉起。

---

## 5. 加载与校验工具

### 5.1 Python 加载

```python
from core.components.registry import (
    load_components,
    list_components,
    get_component,
)

specs = list_components()                          # 缓存加载
dsh = get_component("dsh")                        # 单查
assert dsh.lifecycle["start_script"] == "bin/start-dsh-ikaros.bat"
assert dsh.dsh_integration["overlay"].endswith("cordis.patch.yml")
```

### 5.2 命令行校验

`core/components/registry.py` 末尾已带 `__main__`：
```sh
python core/components/registry.py
# {"count": 3, "ids": ["dsh", "conversation-tree", "embedding"]}
```

未来可扩为 `ikaros doctor` 的子检查项（详 §1.1 `ikaros doctor` 检查项清单）。

### 5.3 必填字段校验

loader 在 `ComponentSpec.from_dict` 内强制 5 个必需字段；缺字段抛 `ValueError`：
```python
specs = load_components()  # 缺字段 → ValueError
```

启动器在 `ikaros all` / `ikaros web` 等启动类子命令前**先跑一次** `load_components()`，失败即拒启动。

---

## 6. Open Questions（line3 实施时需 line3 owner 决策）

- [ ] **`memory_v5` 是否升级为独立组件条目？** 当前 dsh 的 `dependencies: [embedding]` 显式列了 embedding，但 conversation-tree 写的 `[memory_v5]` 是个未注册的 id。**建议**：要么把 memory_v5 升级为 `category: memory` 的独立条目（`port: null`，进程嵌入在调用方里），要么把 conversation-tree 的 dependencies 改为 `[embedding]`。前者更准确（memory_v5 是有 MCP server 进程的子服务），后者更简单。
- [ ] **`process_marker` 命名规范是否统一为下划线？** 当前 dsh/embed 都是单词（直接用 id），但 conversation-tree 的 marker 是下划线。**建议**：约定 `process_marker` = `id` 去掉 dash（kebab-case → snake_case），但**实测 Windows tasklist 命令行是否保留 dash**——若保留，则 marker 必须含 dash。
- [ ] **`watchdog: self` 的"自管"语义如何校验？** 当前是声明式（YAML 字段），无强制。**建议**：加 loader 验证——worker 脚本存在且 `grep -E 'while|loop|respawn|restart' worker` 非空（启发式）。
- [ ] **`healthcheck.extra` 字段什么时候启用？** line3 当前空。**建议**：embedding 第一个启用（`{"probe": "verify-non-zero-vector"}`），loader 透传不解析，启动器读 extra 自行决定校验逻辑。
- [ ] **`category` 是否要扩枚举？** 当前 `memory / ui / runtime / tool / embedding` 5 个；将来加 `mcp_server` / `cli` 等类别时要同步 `VALID_CATEGORIES`。
- [ ] **`lifecycle.start_script: null` 的语义是否保留？** 当前 embedding 用 null 表示"不由启动器直接拉起"。**风险**：未来启动器如果想自动化"全部启动"，会跳过 null 条目——可能不符合用户预期（用户可能期望 embedding 也启动）。**建议**：拆 `start_script`（直接启动命令）vs `trigger`（顺带启动路径），或加 `autostart: bool` 字段。

---

## 7. 与 `docs/ikaros-launcher-design.md` 的对应关系

| launcher doc 章节 | 本 spec 章节 |
|------------------|--------------|
| §5 watchdog 设计（C 混合方案） | §1.2 `healthcheck.extra` 字段、§2.3 embedding probe 校验 |
| §7.1 失败分级（F4 路径不存在） | §1.4 路径约束 |
| §2.4 `ikaros all` 拓扑 | §3 跨组件拓扑图 |
| §1.3 启动器 vs worker 共存 | §4 注册新组件流程 |

---

## 附录 A：当前 3 个组件元数据速查表

| id | category | port | process_marker | start_script | watchdog |
|----|----------|------|----------------|--------------|----------|
| `dsh` | tool | 3080 | `dsh` | `bin/start-dsh-ikaros.bat` | self |
| `conversation-tree` | ui | 48920 | `conversation_tree` | `python core/conversation-tree/server.py --port 48920` | self (planned) |
| `embedding` | embedding | 8587 | `embed` | `null` | self (gap: 当前 bat 无 self-respawn) |

> `herdr`（runtime / null / `bin/start-omp.bat`）已于 2026-08-23 随 pi 底座删除。

## 附录 B：必需字段最小集（loader 强校验）

```python
REQUIRED_FIELDS = ("id", "name", "category", "port", "process_marker")
VALID_CATEGORIES = frozenset({"memory", "ui", "runtime", "tool", "embedding"})
```

缺任一必需字段 → `ValueError`，启动器拒启动。