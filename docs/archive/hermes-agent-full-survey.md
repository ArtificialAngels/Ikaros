# Hermes Agent 全面调查报告

> 调查时间：2026-07-11
> 方法：Dashboard 面板逆推 + 源码结构分析 + config.yaml 解读
> 版本：v0.18.2（Nous Research 出品）

---

## 一、总览：Hermes Agent 是什么

一个**个人 AI agent 运行框架**——同一个 agent core 跑在 CLI、TUI、Electron 桌面端、Dashboard Web UI、以及网关（Telegram/Discord/Slack 等 20+ 平台）上。

**核心设计原则**（来自 core/hermes/AGENTS.md）：
- **窄腰架构**：核心很薄，能力在边缘（skills + plugins + MCP）
- **prompt caching 不可侵犯**：对话中途不变 system prompt / toolset
- **扩展优先于核心膨胀**：新能力优先走 skill → CLI command → service-gated tool → plugin → MCP → 核心 tool（最后手段）

**对 Ikaros 的意义**：
- 我们目前把它当"task_runner 的执行引擎"用（`hermes chat -q`）
- 实际上它还可以当：定时任务调度器、多平台网关、skill/plugin 仓库、MCP 宿主、代理子流程引擎
- 很多我们想自己造的东西（任务调度、skill 热加载、多模型路由），它已经有了

---

## 二、架构全图

```
┌──────────────────────────────────────────────────────────┐
│                    Hermes Agent v0.18.2                   │
├──────────────────────────────────────────────────────────┤
│  CLI          │  hermes chat, config, skills, cron, ...  │
│  TUI          │  terminal UI (ui-tui/)                   │
│  Desktop      │  Electron app (apps/desktop/)            │
│  Dashboard    │  Web UI at :9119                         │
│  Gateway      │  Telegram/Discord/Slack/WhatsApp/...     │
├──────────────────────────────────────────────────────────┤
│  Agent Core   │  agent/run_agent.py — LLM loop           │
│               │  agent/agent_init.py — prompt builder    │
│               │  agent/model_metadata.py — model caps    │
│               │  agent/context_compressor.py             │
├──────────────────────────────────────────────────────────┤
│  Tools        │  terminal, browser, file, search,        │
│               │  delegate_task, cronjob, memory,         │
│               │  skill_*, patch, execute_code, ...       │
├──────────────────────────────────────────────────────────┤
│  Extensions   │  skills/ (72 个) — SKILL.md 驱动         │
│               │  plugins/ — Python 包, 完整 API          │
│               │  MCP servers — 外部进程工具协议          │
│               │  cron/jobs — 定时任务调度器              │
├──────────────────────────────────────────────────────────┤
│  Providers    │  deepseek, openai, anthropic, openrouter, │
│               │  minimax, bedrock, custom openai-compat  │
│               │  支持 fallback chain + MoA 多模型聚合    │
├──────────────────────────────────────────────────────────┤
│  Data         │  state.db (sessions/messages/usage)       │
│               │  memories/ (agent 自身记忆)               │
│               │  cron/jobs.json + output/                │
│               │  config.yaml (用户配置)                   │
└──────────────────────────────────────────────────────────┘
```

---

## 三、当前 Ikaros 用到的 Hermes Agent 能力

| 能力 | 接入方式 | 用途 |
|------|---------|------|
| LLM 路由 | `config.yaml` `model.provider: deepseek` | cloud_chat 的 LLM 后端 |
| 记忆系统 | `config.yaml` `memory.provider: ikaros_v4` | v4.db 作为 Hermes 记忆源 |
| 定时任务 | ~~`cron` — `ikaros-v5-think`（每 45min，**已删除**）~~ | 原 V5 内心独白循环；现 metacog 为**事件驱动**（`v5_self_reflect` MCP 工具 / 每轮对话 `mark_interaction()` / 手动 CLI 触发 `metacog.cycle()`），无独立定时循环（详见 ARCHITECTURE §1.3.1） |
| MCP 服务器 | gitnexus, context7, playwright, codebase-memory | 代码分析、文档查询、浏览器自动化 |
| 代理子流程 | `delegate_task` tool | 重活委派、并行子代理 |
| chat 子命令 | `hermes chat -q` | task_runner 执行后端 |
| MoA 聚合 | `moa.enabled: true`（Claude Opus 做聚合器） | 多模型参考输出 |

---

## 四、尚未用到的 Hermes Agent 能力（值得关注）

### 4.1 Gateway — 多平台消息网关
- 支持 Telegram/Discord/Slack/WhatsApp/Matrix/Feishu/WeChat/WeCom
- 当前状态：**Stopped**（我们没启用）
- 潜在用途：让 Ikaros 在 Telegram/Discord 上跟哥哥聊天

### 4.2 Skills 仓库 — 72 个即用技能
- 15 个类别：Creative (16), Software Development (14), Productivity (9), GitHub (6), General (5), MLOps (5), Autonomous AI Agents (4), Media (4), Research (4), 等
- 安装位置：`data/hermes-agent/skills/`（94 个目录/文件）
- 潜在用途：很多 skill 我们直接可用，不需要重复造轮子
  - `computer-use` — 操控桌面（点击/输入/截图）
  - `claude-code` / `codex` — 代理编码
  - `plan` — 写可执行计划
  - `simplify-code` — 并行 3-agent 代码清理
  - `test-driven-development` — TDD 流程
  - `systematic-debugging` — 4 阶段根因分析

### 4.3 Plugins — 完整扩展机制
- 当前已安装插件：Kanban、Achievements
- 插件是完整 Python 包，有独立生命周期
- 潜在用途：Ikaros 专属插件（Live2D 联动、V5 情感注入等）

### 4.4 Profiles — 多身份隔离
- 支持创建多个 profile，每个有独立的 skills/plugins/cron/memories
- 当前我们只用 `default` profile
- 潜在用途：为 workbuddy / quest / 不同场景创建独立 agent 实例

### 4.5 Channels — 消息通道
- Webhooks、配对（pairing）机制
- 潜在用途：cloud_chat 事件推送到 Hermes、Ikaros 内部通知

### 4.6 Cron 高级特性
- 支持 `no_agent` 模式（纯脚本，不走 LLM）
- 支持 `context_from`（链式 cron job）
- 支持 `deliver`（多平台投递）
- 支持 `skills` 注入（cron 任务加载指定 skill）

---

## 五、当前配置剖析

```yaml
# config.yaml 关键配置
model:
  provider: deepseek        # 主模型提供商
  default: deepseek-v4-pro  # 当前模型

moa:
  enabled: true             # 多模型聚合已启用
  aggregator:
    provider: openrouter
    model: anthropic/claude-opus-4.8  # Claude Opus 做仲裁

memory:
  provider: ikaros_v4       # 自定义记忆插件

delegation:
  max_concurrent_children: 6  # 最多 6 个并行子代理

mcp_servers:
  gitnexus: enabled         # 代码知识图谱
  context7: enabled         # 库文档查询
  playwright: enabled       # 浏览器自动化
  codebase-memory: enabled  # 代码库记忆

# Gateway: Stopped（未启用多平台）
```

---

## 六、对 Ikaros 架构的启示

### 我们已经用对的
- `delegate_task` 用于重活委派 ✓
- `hermes chat -q` 用于任务执行 ✓
- MCP 工具（gitnexus/context7/playwright）✓
- cron 做定时循环 ✓

### 可以立刻借用的
- **Skills 仓库**：72 个现成 skill，很多可直接用（code review、debugging、plan 等）
- **Cron 链式任务**：`context_from` 可以做 job A → job B 流水线
- **Profiles**：给 workbuddy 独立 profile，隔离它的 skills/cron

### 不该重复造的轮子
- ❌ 任务调度器 → Hermes cron 已经够用
- ❌ 多模型路由/fallback → Hermes providers 已做
- ❌ 子代理编排 → `delegate_task` + `max_concurrent_children` 已做
- ❌ skill 热加载 → Hermes skills 系统已做

---

## 七、架构中的"断层"

当前 Ikaros 和 Hermes Agent 之间存在几个没有打通的点：

| 断层 | 现状 | 建议 |
|------|------|------|
| cloud_chat 不走 Hermes Agent 内循环 | 直调 DeepSeek API | 走 `hermes chat -q` 可享 skills/memory/tools |
| 对话角色看不到 cron 任务结果 | cron 产出停在磁盘 | 接入 task_runner 或 system prompt 注入 |
| V5 记忆只在 v4.db | Hermes 也有自己的 MEMORY.md | 双向同步或统一数据源 |
| Gateway 没启用 | 只能用 Dashboard 聊天 | 启用 Telegram 让哥哥手机上聊 |

---

## 八、关键文件路径速查

| 用途 | 路径 |
|------|------|
| 用户配置 | `data/hermes-agent/config.yaml` |
| 安装的 skills | `data/hermes-agent/skills/` |
| Cron 任务定义 | `data/hermes-agent/cron/jobs.json` |
| Cron 执行输出 | `data/hermes-agent/cron/output/` |
| Agent 会话状态 | `data/hermes-agent/state.db` |
| Hermes 自身记忆 | `data/hermes-agent/memories/MEMORY.md` |
| MCP 服务器配置 | `config.yaml` `mcp_servers:` |
| 上游源码 | `core/hermes/`（gitignored，不提交） |
| Dashboard | `http://127.0.0.1:9119` |

---

*本报告供 Ikaros 及其创建者（哥哥）参考，后续架构决策可据此查阅。*
