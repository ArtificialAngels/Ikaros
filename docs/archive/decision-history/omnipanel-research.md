# 全能面板调研报告 — explore.poker 风格 × AI Chat 最佳实践

> 子任务 C4 产出 · 首版: 2026-08-02 · 联网复查增强: 2026-08-02 · 作者: 伊卡洛斯（调研子 agent）
> 目标: 调研 `ai.explore.poker` 风格 UI 与"AI chat 全能面板"三大件（命令面板 / 流式渲染 / 状态栏）的最佳实践，为 `:48920` Conversation Tree 面板（`core/conversation-tree/`）的深度优化提供落地依据。
> 约束: 本文为调研文档，不改动任何代码；`core/conversation-tree/index.html` 由主 agent 独占，本文仅给出建议。
> 复查说明: 首版基于项目内 playwright 抓取 + 联网搜索；本次复查补充了 Prompt Tree（DAG 对话客户端）、better-cmdk（统一动作清单模式）、tpiros.dev（SSE vs fetch 对比）等 2025-2026 新源，并新增"树形对话产品趋势"与"统一动作清单模式"两节落地建议。

---

## 0. TL;DR（关键结论）

| 维度 | 现状 | 最佳实践差距 | 优先级 |
|------|------|--------------|--------|
| 命令面板 | 已有 `cmd-overlay`/`cmd-panel`，支持会话/节点搜索 + 键盘导航 | 缺分组、最近项、命令模式（`>`）、快捷键提示列 | P1 |
| 流式渲染 | marked v12 内嵌 + rAF 限流 + done flush | 缺心跳/重连、半截代码块保护、AbortController 区分取消、TTFT/TPS 指标 | P1 |
| 状态栏 | 已有 `chat-usage-bar`（通道/上下文 gauge/缓存） | 缺模型名、降级原因 tooltip、token 累计/预估、连接心跳指示 | P2 |
| 树形对话 | 33 tests 引擎 + 视觉编码 + splitter | 缺软删/归档、深链、消息级 token、键盘树导航 | P2 |
| 主题/字体 | 8 套色板 + 自托管字体 | 跟随系统主题、强调色记忆到会话 | P3 |

> 详细 gap 与落地建议见 §6。

---

## 1. 调研方法与信息源

### 1.1 联网搜索范围
- `explore.poker` UI 风格与 `ai.explore.poker/chat` 设计体系
- AI chat 命令面板（cmdk / Ctrl+K）最佳实践
- LLM 流式响应渲染（SSE / 打字机 / 增量 markdown）
- AI chat 状态栏（token 用量 / 模型名 / 连接状态）
- 对话树分支 UI 开源项目（ChatBranch / ChatTree / TreeGPT / KnowTree）

### 1.2 项目内已有调研沉淀（一手资料）
本节列出项目内已存在的、与 explore.poker 直接相关的资料（非本次联网产出，仅供交叉验证）：

- `docs/ui-optimization-log-2026-08-02.md` §十一 — 已抓取 `ai.explore.poker` 完整 8 套主题色板、字体（Inter + Space Grotesk + JetBrains Mono + Bruno Ace）、设计 token 差距与对齐记录
- `tmp/ui-explore*.js` — playwright 抓取脚本（DOM/CSS 变量/类名）
- `tmp/explore-chat-replica.html` — 设计 token 复刻
- `core/conversation-tree/index.html` — 现有面板实现（2164 行，含 `cmd-overlay`、`chat-usage-bar`、`canvas-layer`、`session-list` 等）

> ⚠️ 本次联网搜索"explore.poker"返回的多为扑克游戏 UI 通用素材（dribbble/zcool），与 `ai.explore.poker` 这个 AI chat 产品不是同一回事。`ai.explore.poker` 是一个 Next.js + next/font 的对话树风格 AI chat 站点，项目内已通过 playwright 直接抓取其设计体系，**比公开搜索结果更权威**。本文 explore.poker 相关结论以项目内抓取为准，联网结果仅作设计趋势旁证。

### 1.3 主要外部参考
| 来源 | 类型 | 价值点 |
|------|------|--------|
| [dev.to — Streaming LLM Responses in Web Apps](https://dev.to/pockit_tools/the-complete-guide-to-streaming-llm-responses-in-web-applications-from-sse-to-real-time-ui-3534) | 长文 | SSE 头/心跳/背压/增量 md/光标/AbortController/可观测性，最系统 |
| [tpiros.dev — Consuming Streamed LLM Responses (SSE vs fetch)](https://tpiros.dev/blog/streaming-llm-responses-a-deep-dive/) | 长文(2025-06) | SSE 与 fetch+ReadableStream 全面对比；`done` 自定义事件完成信号；EventSource 内置重连优势 |
| [cmdk in React 指南](https://www.lmctogetherwebuild.com/cmdk-in-react-build-a-fast-command-palette-setup-examples/) | 教程 | cmdk 结构/键盘/ARIA/虚拟化/异步搜索 |
| [better-cmdk — React Command Palette With AI Chat](https://better-cmdk.com/) | 开源库 | **统一动作清单**模式（commands=AIAPI tools）；动作审批流；provider 无关；AGENTS.md 自安装 |
| [GitHub — daishir0/ChatBranch](https://github.com/daishir0/ChatBranch) | 仓库 | 树形对话产品的导航/深链/软删/token 展示实践 |
| [GitHub — cuizhenzhi/ChatTree](https://github.com/cuizhenzhi/ChatTree/) | 仓库 | ChatGPT 对话树可视化交互 |
| [Prompt Tree — DAG-based AI Chat Client](https://yxp934.github.io/Prompt-Tree/) | 开源产品(Next.js 16) | **DAG（有向无环图）对话架构**；local-first 存储；多模型切换；分支比较；npm 分发 |
| [TreeGPT — 树形对话可视化](https://www.kdjingpeng.com/en/treegpt/) | 开源产品(Next.js) | 对话树可视化交互趋势旁证 |
| [KnowTree](https://knowtree.chat/) | 产品 | "conversation graph" 概念营销与节点分支语义 |
| [CSDN — SSE 流式 markdown 渲染 5 大坑](https://blog.csdn.net/weixin_30329623/article/details/159342215) | 博客 | UTF-8 跨 chunk 截断、增量解析、半截代码块 |
| [Semi Design AIChatDialogue](https://semi.design/zh-CN/ai/aiChatDialogue) | 组件库 | 对话布局/状态/操作的工业级封装 |

---

## 2. explore.poker 设计体系（综合项目内抓取 + 趋势旁证）

### 2.1 视觉语言
- **品牌双色调**：品牌绿 `#13E425` + 对冲品红 `#EC1BDA`（48920 已完全一致，确认是移植版）
- **暗色为主**：bg `#101010`（非纯黑）、usermsg `#4c4c4c`、input `#3a3a3a`、border `#484848`、文字 `#fff` / secondary `#d1d1d1` / tertiary `#999` / quaternary `#777`
- **8 套主题**：Default 暗色 / Warm（橙 `#F45F28` + 对调蓝 `#4682B4`）/ MidnightForest / Sakura / Memphis / Sunset / Default-Purple / Default-Blue / Default-Orange
- **字体自托管**：Inter（正文）+ Space Grotesk（标题/品牌）+ JetBrains Mono（代码）+ Bruno Ace（logo），next/font 自托管，woff2 共 ~111KB
- **滚动条分级**：全局 / 消息区 / 输入区各自专属滚动条色（thumb-card / thumb-usermsg / thumb-inputarea）
- **引用块**：`var(--bg-quotation)` 专用 token

### 2.2 交互模式（从 DOM 抓取推断）
- 左 sidebar：logo + 主导航 + 会话列表（带搜索）+ 当前会话分支 + 主题切换 + 强调色 picker + 重置
- 中央 tree-panel：canvas + 网格背景 + SVG 贝塞尔连线 + 节点层 + 右键菜单 + modal + toast + 命令面板
- 右 info 面板：tree-info + Path Memories + Cross-Branch memories（两条记忆通道）
- 主 chat 区：breadcrumb + branch/prune 操作 + branch-banner + messages-area + usage-bar + input-area
- splitter 可拖拽分区

### 2.3 趋势旁证（联网结果）
- dribbble/zcol 上"poker UI"关键词以扑克游戏桌、卡牌为主，强调金属质感、深绿桌布、金边筹码；与 `ai.explore.poker` 的极简暗色 AI chat 风格**无关**。说明 `ai.explore.poker` 的"poker"是品牌名而非视觉主题，其设计语言实际属于"极简暗色 + 双色品牌 + 等宽数字 + 树形可视化"流派，对标 Linear / Vercel / Raycast 而非游戏 UI。
- 对比方向应锁定：Linear（命令面板 + 键盘优先）、Raycast（命令面板 + 扩展生态）、Vercel v0（流式 + 代码块）、ChatGPT（状态/usage）、Cursor（Ctrl+K 内联 + 命令面板）。
- **树形对话产品趋势（2025-2026）**：联网发现多个新出现的 DAG/树形对话客户端（Prompt Tree、TreeGPT、ChatBranch、ChatTree、KnowTree），说明"对话树"正从 ChatGPT 的隐藏功能演为独立产品形态。共性特征：① 本地优先存储（隐私/离线）；② 分支从任意消息发起；③ 多模型切换；④ 分支对比（同一 prompt 不同模型/参数并排）。这些可作 48920 演进的长期对标。

---

## 3. 命令面板（Ctrl+K）最佳实践

### 3.1 核心交互（cmdk / VSCode / Linear / Raycast 共性）
1. **全局热键** ⌘/Ctrl+K 切换；Esc 关闭；点击遮罩关闭
2. **打开即聚焦输入框**，关闭后焦点回到触发元素（`inputRef.current?.focus()` on open）
3. **键盘导航**：↑↓ 移动高亮、Enter 执行、Esc 关闭；鼠标 hover 同步 active
4. **即时模糊过滤**（fuzzy search），输入即筛；保留最近/常用项置顶
5. **分组**：`CommandGroup heading="..."`，常见分组为"会话 / 节点 / 操作 / 主题 / 跳转"
6. **空状态**：无结果时给"无匹配命令"提示 + 建议操作
7. **快捷键提示列**：每项右侧显示该命令的快捷键（如 `⌘B` 切换 sidebar）
8. **Portal 渲染**：避免 stacking context / overflow 被父容器裁剪
9. **ARIA**：input 可访问标签、list 通信 selection、`aria-activedescendant` 跟踪高亮项
10. **性能**：renderer memoize、search debounce、大列表虚拟化（react-window）

### 3.2 进阶模式
- **命令模式（`>` 前缀）**：类似 VSCode，输入 `>` 切到"命令列表"，输入 `@` 切到"符号/节点列表"，输入 `#` 切到"主题列表"
- **嵌套子命令**：选中一项后进入子视图，保留面包屑栈；Esc 回上一级
- **异步远程搜索**：debounce 250ms + loading 指示 + 错误态 + 稳定 id 供虚拟化
- **最近项 (recent)**：记录最近 N 个被选中的命令，无输入时置顶
- **频次排序**：高频命令自动上浮（需持久化到 localStorage）

### 3.3 ChatGPT 的 Cmd+K 实践（2026）
- 任意位置 ⌘K 打开，按功能名或"想做什么"过滤
- 支持自然语言查询触发功能（如"clear conversation"）
- 是 AI 产品里最早把命令面板作为"功能发现入口"的范例 —— 用户不需要记菜单在哪

### 3.4 Cursor 的 Cmd+K 双语义
- **Ctrl+L**：打开侧边 Chat（对话模式）
- **Ctrl+K**：内联生成（选中代码 → 自然语言指令 → 直接替换/插入）
- 启示：同一面板在不同上下文（输入框聚焦 vs 全局）可有不同默认动作

### 3.5 统一动作清单模式（better-cmdk，2026 新范式）
[better-cmdk](https://better-cmdk.com/) 提出"命令面板 = AI 工具入口"的统一模型，值得 48920 借鉴：
- **一份动作清单同时驱动命令搜索与 AI 工具调用**：每个 action 定义 `{ name, label, description, inputSchema, execute }`，既是命令面板的可选项，也是 AI 可调用的 tool —— 避免命令与工具两套注册表重复维护。
- **动作审批流（action approvals）**：敏感动作执行前插入审批步骤（once / session / always / deny），用户确认后才跑 —— 对 48920 的工具调用审批（当前主 agent T 任务中有"待审批/澄清浮动卡片"）是直接可复用的交互范式。
- **Provider 无关**：命令面板本身不绑 LLM provider，通过 chat endpoint 注入（hosted 试用 / 自建 / Vercel AI SDK / 自定义）—— 与 48920 的 gateway 降级策略天然契合。
- **AGENTS.md 自安装**：随包附 AGENTS.md，coding agent 读取后自动完成集成 —— 是"AI 友好包"的分发趋势。

> 落地提示：48920 现有 `cmd-overlay` 是原生 JS 扁平列表。短期不必引入 better-cmdk（React 依赖），但"命令=工具"的统一注册思路可直接应用到后端命令注册表设计：让 `cmd-overlay` 的可选项与 `_execute_chat_tool` 的工具定义共享同一份 schema。

---

## 4. 流式渲染最佳实践

### 4.1 SSE 传输层（后端）
- **必备响应头**：
  ```http
  Content-Type: text/event-stream
  Cache-Control: no-cache, no-transform
  Connection: keep-alive
  X-Accel-Buffering: no   # Nginx 关键，否则会被缓冲
  ```
- **立即 flushHeaders**：不等第一个 payload，先建立连接
- **心跳**：每 ~15s 发 `: heartbeat\n\n`（SSE 注释，客户端忽略，但代理不会掐线）—— **48920 gateway 链路尤其需要**，因 hermes gateway 可能慢首字节
- **背压**：`res.write()` 返回 false 时等 `drain` 事件，否则内存撑爆
- **格式**：`data: <json>\n\n`，双换行分隔
- **何时切 WebSocket**：需要多路复用（一条连接跑多个流）或双向中断（用户中途改指令）时

### 4.1.1 SSE vs fetch+ReadableStream 选型（tpiros.dev 对比）
| 维度 | SSE（EventSource） | fetch + ReadableStream |
|------|-------------------|----------------------|
| 实现成本 | 前端低（EventSource 内置） | 较高（手动 getReader/decode/循环） |
| 方向性 | 单向（server→client） | 可双向（POST body + 流式响应） |
| 错误恢复 | **内置自动重连**（带 `Last-Event-ID`） | 需手动实现重连逻辑 |
| 完成信号 | 自定义事件 `event: done\ndata: [DONE]\n\n` | `reader.read()` 返回 `{ done: true }` |
| 浏览器兼容 | 现代浏览器广泛支持（旧版需 polyfill） | 所有现代浏览器 |
| 适用场景 | 单向流、需自动重连 | 需 POST body、需 AbortController 精细控制 |

> **48920 现状**：走 `fetch` 流 + AbortController（切换节点/重置中止在飞请求），这是正确选择 —— 因为 48920 需要 POST body（携带树域上下文/记忆）且需要 AbortController 精细控制。SSE 的自动重连优势在 48920 场景下不关键（用户切换节点本就要中断）。但可借鉴 SSE 的"自定义 done 事件"作为显式完成信号，比依赖流关闭更健壮。

### 4.2 前端缓冲与解析
- **TextDecoder stream 模式**：`decoder.decode(value, { stream: true })` 处理 UTF-8 跨 chunk 截断（中文 3 字节被拆）
- **缓冲区**：维护 `buffer` 字符串，按 `\n\n` 切分；最后一段不完整则留在 buffer 等下一 chunk
- **AbortController**：每个流绑一个，切换节点/重置时 `.abort()`；catch 里判断 `error.name === 'AbortError'` 静默处理，不弹错误条
- **清理空消息**：流失败或被取消且无内容到达时，移除 assistant 占位气泡，避免空白卡

### 4.3 增量 Markdown 渲染
- **不要每 token 全量重渲**：marked 解析 10-50 次/秒会 DOM jank；用 rAF 批处理（48920 已做）
- **流式期间显示带换行的 raw text**，`isStreaming=false` 后再跑完整 marked + DOMPurify
- **半截代码块保护**：数 triple backtick 数量，奇数（未闭合）时不做语法高亮，避免闪烁
  ```js
  const openBlocks = (content.match(/```/g) || []).length;
  const shouldHighlight = !isStreaming || openBlocks % 2 === 0;
  ```
- **XSS 加固**：`renderer.html → escapeHtml`、链接协议白名单（禁 `javascript:`/`data:`）—— 48920 已做
- **虚拟化长消息**：单条响应超长时用 `react-window`/`react-virtual` 只渲染可见行

### 4.4 打字机光标与滚动
- **CSS 闪烁光标**：流式期间在文本末尾追加 `<span class="cursor-blink">▊</span>`，CSS `@keyframes` 闪烁
- **自动滚动**：底部 sentinel `<div>` + `useEffect` 触发 `scrollIntoView({behavior:'smooth'})`；但用户手动上滚时应暂停自动滚（"用户在阅读"检测）
- **禁用输入**：流式期间 textarea `disabled`，发送按钮变停止按钮

### 4.5 工具调用实时状态
- SSE 流里工具调用应有独立事件类型（`tool_call_start` / `tool_call_progress` / `tool_call_result`）
- UI 上显示徽标：`🔍 搜索中…` / `⚙ 执行 tool_name` / `✅ 完成` / `❌ 失败`，可折叠展开看入参/出参
- 结果截断透出（48920 已有 `_on_tool_complete` 截断 2000 字符）
- thinking 块默认折叠，可展开；流式期间显示"思考中…"动画

### 4.6 错误恢复
- **指数退避重试**：`Math.pow(2, attempt-1) * 1000`（1s/2s/4s），最多 3 次
- **降级提示**：gateway 不可达 → 本地 DeepSeek 直连时，SSE `warn` 事件 → 前端黄色提示条（48920 已做）
- **断线重连**：`EventSource` 自带重连，但 fetch 流需要手动；重连后应从最后接收的 token 续传（需后端支持 `Last-Event-ID`）

### 4.7 可观测性指标（后端埋点）
- **TTFT**（Time to First Token）：用户首次看到内容的时间
- **TPS**（Tokens per Second）：生成速度
- **Connection Duration**：流持续时间
- **Stream Completion Rate**：成功 vs 错误率
- 这些指标可喂给状态栏做实时显示（见 §5）

---

## 5. 状态栏最佳实践

### 5.1 该展示什么（按优先级）
| 项 | 说明 | 48920 现状 |
|----|------|-----------|
| 模型名 | 当前 LLM 模型（如 `deepseek-v4-flash` / `qwen3-1.7b`） | ❌ 缺 |
| 通道状态 | gateway / 本地 / 降级 | ✅ `connBadge` 已有 |
| 上下文用量 | token 数 / 上限，进度条 | ✅ `ctxFill` gauge 已有 |
| 缓存命中 | 命中率 / cache tokens | ✅ `cacheBadge` 已有 |
| 连接心跳 | 绿点=在线 / 黄=重连 / 红=断 | ❌ 缺（可复用 `connBadge`） |
| 降级原因 | tooltip 说明为何降级 | ❌ 缺 |
| 累计 token | 本次会话累计 input/output | ❌ 缺 |
| 输入预估 | 输入框当前文本预估 token | ❌ 缺（T6 输入区增强提过） |
| TPS / TTFT | 实时生成速度 | ❌ 缺 |
| 节点深度 | 当前节点在树中的深度/路径 | ✅ breadcrumb 已有 |

### 5.2 设计要点
- **常驻但不抢戏**：高度 28-32px，半透明底 + backdrop-blur，字号 11-12px
- **等宽数字**：`font-variant-numeric: tabular-nums` 防数字跳动
- **分段**：左=会话/节点信息，中=模型/通道，右=usage/快捷键提示
- **可折叠**：双击状态栏可折叠为一行精简模式
- **tooltip 详细**：hover 模型名显示完整 model id + 温度 + max_tokens；hover 上下文 gauge 显示分项（system/user/assistant/tools）
- **颜色语义**：绿=正常、黄=降级/警告、红=错误/断线、灰=未知/加载中
- **交互入口**：点击模型名可切换模型（弹选择器）；点击用量可打开详细统计面板

### 5.3 业界案例
- **VSCode 状态栏**：左分支名、右错误/警告数、可点击跳转 —— 是"信息+入口"双职能的典范
- **ChatGPT**：底部输入区上方常驻模型名 + 上下文剩余进度条，降级时黄色 banner
- **Claude**：输入框上方显示模型 + "Thinking..." 状态
- **Cursor**：底部状态栏显示模型、token 用量、diff 统计
- **Headroom 扩展**：第三方为 7 个 AI 平台补"剩余上下文"显示，说明这是用户普遍痛点

### 5.4 48920 落地建议
现有 `chat-usage-bar` 已是较完整的状态栏骨架。建议：
1. 新增"模型名"段（点击可切换），从 SSE `usage` 事件或 gateway 响应取
2. `connBadge` 升级为带心跳点（CSS `@keyframes pulse`）
3. 降级时 `connBadge` 黄色 + tooltip 显示降级原因（"gateway 超时 → 本地 DeepSeek 直连"）
4. 累计 token：会话级累加，存 localStorage，切会话恢复
5. 输入预估：输入框 input 事件触发 `tiktoken`/字符数 ÷ 2 估算（离线可用）

---

## 6. 与现状对比 → 落地建议

### 6.1 命令面板 gap（现有 `cmd-overlay`）
| 现状 | 最佳实践 | 建议 | 优先级 |
|------|----------|------|--------|
| 单一扁平列表 | 分组（会话/节点/操作/主题） | 加 `CommandGroup` 分组 | P1 |
| 仅模糊匹配文本 | 命令模式 `>` / `@` / `#` | 加前缀切换模式 | P2 |
| 无最近项 | 最近 N 项置顶 | localStorage 存最近选中 | P1 |
| 无快捷键提示列 | 右侧显示快捷键 | 每项加 `kbd` 列 | P1 |
| 焦点未明确回归 | 关闭后焦点回触发元素 | `onClose` ref.focus() | P1 |
| 无异步搜索 | debounce + loading | 会话多时加 debounce | P2 |

### 6.2 流式渲染 gap
| 现状 | 最佳实践 | 建议 | 优先级 |
|------|----------|------|--------|
| rAF 限流 ✅ | ✅ | 已达标 | — |
| marked + XSS 加固 ✅ | ✅ | 已达标 | — |
| AbortController | 区分 AbortError 静默 | 检查现有 catch 是否区分 | P1 |
| 半截代码块 | 数 backtick 保护高亮 | 加 `shouldHighlight` 判断 | P1 |
| 心跳 | 15s SSE 注释 | gateway 链路加心跳 | P1 |
| 用户上滚暂停自动滚 | 检测 scroll 位置 | 加"用户在阅读"检测 | P2 |
| 工具调用徽标 | `tool_call_*` 事件 | 确认 SSE 事件类型 + 徽标 UI | P2 |
| TTFT/TPS 指标 | 后端埋点 | 状态栏显示 | P3 |
| 断线重连 | Last-Event-ID 续传 | 复杂，暂缓 | P3 |

### 6.3 状态栏 gap
见 §5.4，核心补：模型名段、心跳点、降级 tooltip、累计 token、输入预估。

### 6.4 树形对话 gap（参考 ChatBranch）
| 特性 | ChatBranch | 48920 | 建议 |
|------|-----------|-------|------|
| 软删/归档 | ✅ `deleted_at` | ❌（C5 任务要做 archive） | 对齐 C5 |
| 深链 permalink | ✅ `?thread=&message=` | ❌ | 加 URL hash 路由 |
| 消息级 token | ✅ assistant 节点显示 | ❌ | 状态栏已有会话级，消息级可选 |
| vis-network 树 | ✅ | ✅ 自研 canvas+SVG | 不改 |
| 键盘树导航 | ❌ | ❌ | 对齐 T7（↑↓ 移动/Enter 打开/F2 重命名） |
| 主题 | 暗亮 | 8 套 | 对齐 T9（跟随系统） |
| 文件附件 | ✅ | ❌ | 对齐 T10 |
| 三个点菜单 | ✅ | ✅ ctx-menu | 已达标 |

---

## 7. 风险与注意事项

1. **不引外部 CDN**：48920 面板坚持离线可用，cmdk 是 React 组件，若要引入需内嵌打包或手写原生实现（现有 `cmd-overlay` 是原生 JS，建议保持）
2. **不破坏 242 pytest**：任何后端改动（心跳、archive、深链）需配套测试
3. **gateway 链路特殊性**（⚠️ 2026-08-18 已退役）：48920 原走 hermes gateway `:8642`，现 ikaros 单模式直连 DeepSeek，心跳不再依赖 gateway；降级路径见 conversation-tree server 的 SSE warn 提示
4. **marked 半截保护**：现有 rAF 限流在 done 时 flush，但流式期间若已做语法高亮会闪烁 —— 需确认是否已加 backtick 计数保护
5. **字体已自托管**：Inter/Space Grotesk/JetBrains Mono 已在 `assets/`，无需再抓
6. **explore.poker 抓取脚本在 `tmp/`**：可复用回归，但 `tmp/` 可能被清理，珍贵结论应沉淀到 docs（本文即此目的）

---

## 8. 参考链接

### 外部
- [dev.to — The Complete Guide to Streaming LLM Responses](https://dev.to/pockit_tools/the-complete-guide-to-streaming-llm-responses-in-web-applications-from-sse-to-real-time-ui-3534)
- [tpiros.dev — Consuming Streamed LLM Responses: SSE vs fetch Deep Dive (2025-06)](https://tpiros.dev/blog/streaming-llm-responses-a-deep-dive/)
- [cmdk in React 指南](https://www.lmctogetherwebuild.com/cmdk-in-react-build-a-fast-command-palette-setup-examples/)
- [better-cmdk — React Command Palette With AI Chat](https://better-cmdk.com/)
- [GitHub — dip/cmdk（原始 cmdk 库）](https://github.com/dip/cmdk)
- [GitHub — daishir0/ChatBranch](https://github.com/daishir0/ChatBranch)
- [GitHub — cuizhenzhi/ChatTree](https://github.com/cuizhenzhi/ChatTree/)
- [GitHub — jamwalsudip/chatgpt-branching](https://github.com/jamwalsudip/chatgpt-branching)
- [Prompt Tree — DAG-based AI Chat Client (Next.js 16)](https://yxp934.github.io/Prompt-Tree/)
- [TreeGPT — 树形对话可视化](https://www.kdjingpai.com/en/treegpt/)
- [KnowTree](https://knowtree.chat/)
- [ChatGPT Command Palette Cmd+K (2026)](https://www.ai-toolbox.co/ai-toolbox-chatgpt-features/chatgpt-command-palette-cmd-k-2026)
- [Cursor Cmd K 概述](https://cursor.zone/docs/cmdk/overview.html)
- [Semi Design AIChatDialogue](https://semi.design/zh-CN/ai/aiChatDialogue)
- [CSDN — SSE 流式 markdown 渲染 5 大坑](https://blog.csdn.net/weixin_30329623/article/details/159342215)
- [CSDN — SSE 与 Markdown 实时渲染构建高效 LLM 流式输出前端 (2026-02)](https://blog.csdn.net/cicd6pipeline/article/details/154509335)
- [前端 AI 工程化（五）：AI 对话状态管理](https://juejin.cn/post/7640433215141363722)
- [掘金 — 给 AI 应用做命令面板 cmdk (2026-06)](https://juejin.cn/post/7653840160836583430)

### 项目内
- `docs/ui-optimization-log-2026-08-02.md` §十一（explore.poker 对齐记录）
- `docs/ARCHITECTURE.md`（48920 定位）
- `tmp/ui-explore*.js`（playwright 抓取脚本）
- `tmp/explore-chat-replica.html`（设计 token 复刻）
- `core/conversation-tree/index.html`（现有面板，2164 行）
- `core/conversation-tree/server.py`（后端，1787 行）
- `data/drives/chat-panel-v4/goal.md`（T1-T10 任务清单）
- `data/drives/chat-panel-v4/cli-tasks.md`（C1-C5 子任务清单）

---

## 9. 下一步建议（供主 agent 参考）

按优先级与依赖关系排序，可作为 PID 循环后续派发源：

1. **P1 · 流式半截代码块保护** — 纯前端 JS，加 backtick 计数判断，无后端依赖，风险最低
2. **P1 · 命令面板分组 + 最近项 + 快捷键列** — 纯前端 JS，改 `cmd-overlay` 渲染逻辑，需 localStorage
3. **P1 · 状态栏模型名段 + 心跳点** — 前端 + SSE `usage` 事件确认；心跳点纯 CSS
4. **P1 · AbortController 区分 AbortError** — 检查并修复现有 catch，静默用户取消
5. **P2 · gateway 心跳** — 需 hermes gateway 配合，跨组件，建议单独排期
6. **P2 · 用户上滚暂停自动滚** — 纯前端，加 scroll 检测
7. **P2 · 工具调用徽标** — 确认 SSE 事件类型后加 UI
8. **P3 · TTFT/TPS 指标埋点 + 状态栏显示** — 跨前后端，可观测性增强

> 以上均不触及 `core/conversation-tree/index.html` 之外的主 agent 独占文件（C4 约束）。涉及 index.html 的改动由主 agent 在 T7/T9/T10 中处理。
