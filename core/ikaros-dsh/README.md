# ikaros-dsh —— Ikaros 工作引擎（deepseek-harness 底座）

把 Ikaros 的 agent 底座从 hermes-agent 迁到 DeepSeek Harness 的组合层。不动 `runtime/deepseek-harness-master` 源码树，全部定制集中在本目录。

## 文件结构

```
core/ikaros-dsh/
├── cordis.patch.yml                      # 完整组合 overlay（terminal/lsp/mcp-memory/persona）
└── plugins/ikaros-memory/src/index.ts    # memory_v5 召回/写回插件骨架
```

## 已验证 / 待实现

| 项 | 状态 |
|---|---|
| memory_v5 MCP 49 工具可启动、可执行（stats/search 实测通过） | ✅ 已验证 |
| `cordis.patch.yml` 里所有包名 / config 字段 | ✅ 已对照 dsh 源码确认 |
| `dsh-mcp-client` 挂载 memory_v5（工具 → `mcp__ikaros-v5__v5_*`） | ✅ 已验证（2026-08-18） |
| terminal / lsp 挂载 | ✅ 已验证（2026-08-18） |
| ikaros-memory 插件（召回/写回/压缩沉淀） | ✅ 已实现（2026-08-18 初始；2026-08-24 新增 compaction 捕获 + pre-step 幂等性修复） |

## ikaros-memory 插件（2026-08-18 已实现）

**设计**：memory_v5 (Python) 保持 MCP server 形态（48 个 `v5_*` 工具给模型主动调用、
被 dsh + pi 复用）；本插件 = harness 进程内「工程层」，补齐 MCP 做不了的主动能力。

| 机制 | 实现 |
|---|---|
| 自动沉淀（写回） | `agent/turn-stopping` → `agent.session.deriveMessages()` 取本轮**真实** user/assistant（`source.kind==='user'` 过滤系统注入）→ 规则蒸馏 `Q:/A:` → `bin/v5_call.py store` 落盘（type=conversation, tags source:dsh, 走 upsert 防雷同） |
| 召回注入 | `agent/pre-step` → `should_recall` 门控（线索词/寒暄/实质三级）→ `v5_call.py search` → `systemPrompt.context()` user-role 快照注入 |
| 前缀缓存友好 | 静态纪律 `systemPrompt.section()`（字节稳定锚）；动态快照 `systemPrompt.context()`（内容变化只击穿快照本身，不用 agent.inject 破坏 KV 复用） |
| 冷却/防抖 | 写回 5min 冷却 + 最短轮长 60 字（寒暄/琐碎跳过） |
| 压缩沉淀（2026-08-24 新增） | `session/event` → `compaction/summary` 事件捕获（dsh 压缩已花 API 生成 checkpoint，零额外成本）→ `v5_call.py store` 落盘（tags source:dsh, v5_kind:dsh-compaction） — 「压缩即沉淀」 |
| pre-step 幂等性（2026-08-24 修复） | 每 turn 按 `turn + query` 指纹只注入一次（同名 `systemPrompt.context` 重复注册会 throw）；后续 turn 先 dispose 旧 context 再注册新快照，解决「第二轮用户消息后记忆注入永远停在第一次旧快照」bug |

**文件**：
- `src/index.ts` — 插件规范源（TypeScript，静态装配时构建）
- `bin/v5_call.py` — Node→Python 桥接（直接 import memory_v5.memory_api，不经过 MCP 协议；search/store 双 op，stdout 输出 JSON，失败静默降级）
- 动态验证版：cordis_define 定义 `ikaros-auto-memory`（plain JS 转译），实测：
  - turn-stopping 写回 v5.db 成功（id=101908 等，已删污染记录）
  - pre-step 注入 `[Ikaros 相关记忆]` 快照出现在模型上下文 ✓
  - source.kind 过滤修复（v3 误用 String(source)，v6 改用 `source.kind==='user'`）

**dsh API 要点**（实测确认，写插件时对照）：
- `Message.source` 是**对象** `{kind:'user'|'plugin'|'model'|'tool'}`（dsh-llm message.d.ts），不是字符串
- subprocess Service：`spawn({argv,cwd,stdio:{stdout:{collect:true}},graceMs})` → `handle.done: Promise` + `handle.collected.stdout.readFrom(0)`（**不是** EventEmitter 的 .on('close')）
- 事件 agent-scoped：subagent 的 turn-stopping 不触发父 ctx 监听器；主会话触发

## 用法

### 启动器（推荐，2026-09-05 起统一入口）

```bat
:: 交互式 Web GUI（双击 ikaros.bat 选 1=web；CLI 透传）
ikaros web
bin\ikaros.bat                :: 双击出 13 项菜单

:: one-shot 工作引擎（跑一个 task 退出）
ikaros dsh headless "<task>"
```

`bin/ikaros.bat` 自锚定 IKAROS_ROOT（`%~dp0`）、`call bin/ikaros-env.bat` 注入便携环境，再透传给 `ikaros.ps1` -> `core/ikarosctl.py`。`ikaros dsh headless` 是 dsh 组件的 `web|headless` 分支，非一级子命令。

> 历史入口（2026-09-05 删除）：`bin/start-dsh-ikaros.bat` 薄壳已并入 `ikaros.bat`；`bin/restart-dsh-ikaros.ps1` 已并入 `ikaros dsh restart`。`Win+R` 快捷入口 `dsh-open/status/sync` 三个 .bat 已并入 `ikaros.bat` 菜单（选项 5/6/7）。

### 重启（让已运行会话加载 overlay）

```bat
:: 同步 patch + 停 dsh + 启 dsh（日志 ~/.dsh/ikaros-dsh-restart.log）
ikaros dsh restart

:: 2026-09-05 修复: 不再传 --patch（web 模式从 profile 加载），IKAROS_ROOT 已由 ikaros.bat 注入。
```

> ⚠️ 重启会中断当前 Web 会话；刷新 http://127.0.0.1:3080 后从持久化会话恢复。

### 手动等价命令

```sh
dsh --profile headless --patch "core/ikaros-dsh/cordis.patch.yml" "<task>"   # one-shot
dsh web --patch "core/ikaros-dsh/cordis.patch.yml"                            # Web GUI
```

### profile 级自动加载（裸 `dsh web` 也生效）

`~/.dsh/profiles/web/cordis.patch.yml` 已同步本 overlay（见文件头注释）：裸 `dsh web` 会作为「用户 patch 层」自动加载，无需 `--patch`。注意 dsh 的 HMR 热重载对该文件在部分运行态不可靠（实测未触发 MCP spawn），**推荐重启而非依赖 HMR**。改动请改本规范源，再重新同步 profile patch。

`cordis.patch.yml` 是 **patch overlay**：叠加在 dsh-base + 模式 bundle 之上，只补 dsh-base 默认不挂的 terminal/lsp/mcp-memory，并覆盖 `system-prompt` 的 persona。

## 插件（ikaros-memory）的两种挂载方式

1. **动态插件（临时跑）**：用 dsh 的 `cordis_define` 把 `src/index.ts` 的 `apply` 逻辑定义成动态 Plugin 后 `cordis_run`。
2. **静态 package（长期）**：把 `plugins/ikaros-memory/` 补成完整 npm 包（`package.json` + `tsconfig.json` + 声明到 Ikaros 的 workspace），在 `cordis.patch.yml` 里 `name: '@ikaros/dsh-ikaros-memory'` 引用。

## 插件 TODO 的实现路径

`recallMemory` / `writeMemory` 二选一：

- **方案 A（推荐）**：复用 `dsh-mcp-client` 已建立的 stdio 连接做 programmatic 调用——零额外进程。前提是 mcp-client 暴露可程序化调用的服务（需查 `ctx` 上是否有 mcp 服务可注入）。
- **方案 B（兜底）**：写一个 `bin/v5_call.py`（用 FastMCP 的 `call_tool` 单次调用一个工具，stdout 输出 JSON），插件经 `child_process` spawn portable-python 调它。独立于 mcp-client，但每次 spawn 有 Python 冷启动开销（常驻连接可消除）。

## 与 hermes 底座的对照

| hermes（旧） | dsh（新） |
|---|---|
| `ikaros_v5` 插件继承 `MemoryProvider`/`ContextEngine` ABC | `ikaros-memory` 插件监听 `agent/pre-step`/`agent/turn-stopping` |
| `prefetch`（每轮检索注入） | `agent/pre-step` + `agent.inject()` |
| `sync_turn`（每轮写回） | `agent/turn-stopping` |
| `on_pre_compress`（压缩注入） | `compaction` seam（CompactionEngine） |
| 48 工具经 hermes `get_tool_schemas` | 48 工具经 `dsh-mcp-client` → `mcp__ikaros-v5__v5_*` |
