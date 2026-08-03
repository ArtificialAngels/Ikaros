# Hermes Studio AI Chat 功能对比报告

> 任务 C1：参考项目分析  
> 来源：`E:\Ikaros-something\reference project\hermes-studio-main`  
> 目标：对比 Ikaros 现有 chat 实现，提取可借鉴点与落地建议  
> 日期：2026-08-02

---

## 1. Hermes Studio 项目概览

Hermes Studio 是一个完整的 AI 编码助手桌面应用，采用 **monorepo** 架构：

```
packages/
  client/      — Vue 3 + Pinia + TypeScript 前端
  server/      — Koa HTTP + Socket.IO 后端
  desktop/     — Electron 壳
  skills/      — Agent 技能定义
  ekko-agent/  — Ekko 编码 agent
```

其 AI Chat 功能是桌面应用的核心交互界面，功能成熟度对标 ChatGPT/Claude Desktop。

---

## 2. 核心架构对比

| 维度 | Hermes Studio | Ikaros（现状） |
|------|--------------|---------------|
| **前端框架** | Vue 3 + Pinia（有状态管理） | 3 个独立前端：conversation-tree（原生 HTML/JS）、Hermes Dashboard（React/@assistant-ui）、N.E.K.O（React） |
| **传输层** | Socket.IO（双向全双工） | SSE（conversation-tree）+ WebSocket（N.E.K.O）+ PTY/EventBus（Hermes Dashboard） |
| **LLM 网关** | AgentBridge（WebSocket 长连接） | Hermes Gateway `:8642`（HTTP `/v1/chat/completions`） |
| **消息存储** | SQLite（`node:sqlite` + FTS5 BM25）+ KeyValue DB | V5 SQLite（`v5.db`）+ JSON 拓扑（`ui_conversation_tree.json`） |
| **会话模型** | 线性会话列表（分组：今天/昨天/本周/更早） | 树形分支会话（ConversationTree） |
| **工具调用** | Socket.IO 事件流（`tool.started`/`tool.completed`/`tool.failed`） | SSE 事件（`tool_call`/`tool_result`），**工具结果被网关截断到 2000 字符** |

---

## 3. 功能维度逐项对比

### 3.1 多会话管理

<table>
<tr><th>Hermes Studio</th><th>Ikaros</th><th>借鉴度</th></tr>
<tr>
<td>

- 会话列表侧边栏（可折叠）
- 时间分组（今天/昨天/本周/更早）
- 新建按钮 → 抽屉式配置（模型/Provider/工作区/编码Agent）
- 右键菜单：重命名/归档/删除/设置工作区/设置模型
- 批量删除
- 侧边栏底部：Profile 切换器 + 菜单
- **Cmd/Ctrl+K 全局会话搜索**（FTS5 + 模糊匹配）

</td>
<td>

- 会话列表侧边栏（conversation-tree）
- 活跃/归档分组
- 搜索过滤（仅按会话名）
- 右键菜单：重命名/归档/删除
- **树形分支**（fork/merge/conclude/abandon）—— Hermes Studio 没有
- **Ctrl+K 命令面板**（操作+会话+节点混合）

</td>
<td>

⭐⭐⭐⭐ 高

</td>
</tr>
</table>

**可借鉴点：**

1. **会话分组**（按时间维度）：Ikaros 的活跃/归档二分法太粗，可增加"今天/昨天/本周/更早"分组，参考 `SessionSearchModal.vue` 的分组逻辑
2. **会话搜索增强**：当前仅按会话名过滤，可接入 V5 已有 FTS5 实现全文消息搜索（hermes-studio 的 `sessions-db.ts` 有完整 CJK 兼容搜索参考实现）
3. **批量操作**：批量删除/归档会话

### 3.2 工具展开（Tool Display）

<table>
<tr><th>Hermes Studio</th><th>Ikaros</th><th>借鉴度</th></tr>
<tr>
<td>

- 工具消息作为独立消息体（`role: 'tool'`）
- 折叠/展开：折叠显示 `toolPreview`（简短描述），展开显示完整 `toolArgs` + `toolResult`
- 执行中动画（spinner）+ 完成后图标（✓/✗）
- **JSON 截断保护**：200 字符 max、深度 6、1000 节点
- `` ` ``代码块 `` ` `` 结果内语法高亮
- ToolChange 文件变更记录
- **工具审批流**（`approval.requested`/`resolved`）

</td>
<td>

- `toolCardHtml` 渲染工具卡片
- `<details>` 展开/折叠（name + args + result）
- 成功/失败状态着色（ok/fail）
- **无工具审批**（hermes 模式由 gateway 管理）
- **工具结果被网关截断到 2000 字符**（`_on_tool_complete`）

</td>
<td>

⭐⭐⭐ 中

</td>
</tr>
</table>

**可借鉴点：**

1. **工具结果截断/保护**：hermes-studio 的 JSON 截断策略（200 字符、深度限制）可移植到 Ikaros 的 `toolCardHtml`，防止大结果撑爆 DOM
2. **工具执行时间显示**：`toolDuration` 字段让用户感知性能
3. **子 agent 生命周期可视化**：`subagent.start/progress/complete` 事件流可在 conversation-tree 中独立展示
4. **ToolChange 文件变更预览**：在工具结果中展示文件 diff 摘要

### 3.3 会话内搜索

<table>
<tr><th>Hermes Studio</th><th>Ikaros</th><th>借鉴度</th></tr>
<tr>
<td>

- SQLite FTS5 + BM25 排名
- 标题 + 内容联合搜索
- **CJK 检测**：检测到 CJK 字符自动回退到 LIKE 查询（FTS5 不擅长 CJK）
- 搜索结果：`matched_message_id` + 高亮片段（`>>>...<<<` 标记）+ rank
- 去重 + 按 rank/活跃时间排序

</td>
<td>

- V5 已有 FTS5（`memory_v5/store.py`）
- conversation-tree **无会话内消息搜索**
- Ctrl+K 是命令/会话/节点跳转，不是内容搜索

</td>
<td>

⭐⭐⭐⭐⭐ 极高

</td>
</tr>
</table>

**可借鉴点：**

1. **直接可落地**：V5 已具备 FTS5 基础设施，只需在 `conversation-tree/server.py` 增加 `/api/sessions/:id/search?q=` 端点
2. CJK 搜索回退策略：hermes-studio 的 `sessions-db.ts` 中 CJK 字符检测 + LIKE 回退逻辑直接可移植
3. 搜索结果展示：高亮片段 + 点击跳转到消息位置

### 3.4 文件上传 / 附件

<table>
<tr><th>Hermes Studio</th><th>Ikaros</th><th>借鉴度</th></tr>
<tr>
<td>

- 拖拽/粘贴/点击上传
- `ContentBlock[]` 协议：文本 + 图片（base64）+ 文件（服务器本地路径）
- 图片支持 `media_type` + base64 data
- 50MB 上传限制
- 8 字节随机 hex 文件名（防冲突）
- `FileProvider` 抽象层（Local/Docker/SSH/Singularity）

</td>
<td>

- conversation-tree：**无文件上传**（纯文本 textarea）
- Hermes Dashboard：有 `UploadFile`/`attachmentRefs`（`@file:`/`@image:`）
- N.E.K.O：无文件上传

</td>
<td>

⭐⭐⭐⭐ 高

</td>
</tr>
</table>

**可借鉴点：**

1. **conversation-tree 前端**：增加 `<input type="file">` + paste-handler + drag-drop，构建 `ContentBlock[]` 并在 SSE `chat/stream` 请求中透传
2. **后端上传路由**：`conversation-tree/server.py` 增加 `/api/upload` 端点（multipart，限制 50MB）
3. **ContentBlock 协议对齐**：Hermes gateway 的 `/v1/chat/completions` 可接收 `ContentBlock[]`，三端统一协议
4. 图片支持：base64 编码后作为 `image_url` 类型传入 gateway

### 3.5 流式渲染与 SSE 事件系统

<table>
<tr><th>Hermes Studio</th><th>Ikaros</th><th>借鉴度</th></tr>
<tr>
<td>

- **Socket.IO 事件总线**：30+ 事件类型
- 事件按 `session_id` 路由（`sessionEventHandlers` Map）
- 断开重连 + 状态恢复（`resumeSession`）
- 队列系统（`run.queued` + `dequeueNextQueuedRun`）
- 运行中状态保护（`isWorking` 标志）

</td>
<td>

- **SSE 命名事件**：`thinking`/`content`/`tool_call`/`tool_result`/`warn`/`usage`/`done`/`error`
- 单飞模式（AbortController）
- 降级提示（`warn` 事件 → 黄色提示条）
- **无重连/状态恢复**
- **无队列系统**

</td>
<td>

⭐⭐⭐ 中

</td>
</tr>
</table>

**可借鉴点：**

1. **消息队列**：当会话正在处理时，后续消息应入队而非丢弃（hermes-studio 的 `RunQueue` 模式）
2. **状态恢复**：SSE 断开后重新连接时应恢复上次状态（当前 SSE 断连后无恢复机制）
3. **事件丰富度**：可扩展 SSE 事件类型（`approval.requested`、`clarify.requested` 等）

### 3.6 推理/思考（Reasoning/Thinking）展示

<table>
<tr><th>Hermes Studio</th><th>Ikaros</th><th>借鉴度</th></tr>
<tr>
<td>

- `reasoning.delta` / `thinking.delta` 事件
- 推理内容单独存储（`message.reasoning` 字段）
- UI 折叠展示（默认折叠，可展开查看）

</td>
<td>

- SSE `thinking` 事件（非标准）
- 前端"思考中"动画
- 部分透出（gateway 模式下 reasoning 在 gateway 内部消费）

</td>
<td>

⭐⭐ 低

</td>
</tr>
</table>

**可借鉴点：**

1. 折叠式推理展示（当前 conversation-tree 将 thinking 作为动画而非可读内容）—— 低优先级
2. reasoning 内容持久化到 V5（当前仅 content + summary 落库）

### 3.7 会话导出

<table>
<tr><th>Hermes Studio</th><th>Ikaros</th><th>借鉴度</th></tr>
<tr>
<td>

- JSON 导出（完整消息历史）
- 压缩 TXT 导出（`ExportCompressor`）
- 分页消息导出

</td>
<td>

- **无会话导出功能**

</td>
<td>

⭐⭐⭐ 中

</td>
</tr>
</table>

**可借鉴点：**

1. 增加 `/api/sessions/:id/export` 端点（支持 JSON/TXT 格式）
2. 前端增加导出按钮

---

## 4. Ikaros 独有优势（Hermes Studio 没有的）

| 特性 | 说明 |
|------|------|
| **树形分支对话** | conversation-tree 的 fork/merge/conclude/abandon 是 Hermes Studio 完全没有的能力，更适合探索性对话 |
| **Persona 注入** | ikaros 模式的完整 SOUL 人格注入 + 树域上下文，Hermes Studio 只有简单的 system instruction |
| **统一 LLM 网关** | Hermes Gateway `:8642` 作为单一内核，三端共享。Hermes Studio 的 bridge 是紧耦合的 WebSocket |
| **V5 记忆系统** | 时间图谱 + 语义搜索 + 本体对齐 + 认知 5D，Hermes Studio 没有持久化记忆系统 |
| **多服务架构** | 9 个服务独立运行，可组合可独立调试。Hermes Studio 是单体桌面应用 |

---

## 5. 可借鉴点优先级排序

### P0 — 高影响、低阻力（建议立即落地）

| 功能 | 借鉴来源 | 落地路径 | 预估工作量 |
|------|---------|----------|-----------|
| **会话内全文搜索** | `sessions-db.ts`（FTS5 + CJK fallback） | `conversation-tree/server.py` 新增 `/api/sessions/:id/search?q=` 端点；V5 已有 FTS5 | 小（1-2 天） |
| **文件上传/附件** | `upload.ts` + `ContentBlock[]` 协议 | `server.py` 新增 `/api/upload` + `index.html` 增加附件 UI | 中（2-3 天） |
| **会话导出** | `ExportCompressor` | `server.py` 新增 `/api/sessions/:id/export` + 前端按钮 | 小（半天） |

### P1 — 中影响、中阻力（建议近期规划）

| 功能 | 借鉴来源 | 落地路径 | 预估工作量 |
|------|---------|----------|-----------|
| **消息队列** | `RunQueue` + `run.queued` 事件 | `server.py` 增加队列管理 + 前端排队 UI | 中（2-3 天） |
| **工具结果截断保护** | JSON truncation（200 字符/深度 6） | 改造 `toolCardHtml` 渲染逻辑 | 小（半天） |
| **工具执行时间** | `toolDuration` | SSE `tool_result` 事件增加 `duration` 字段 | 小（1 小时） |
| **会话分组优化** | 时间分组（今天/昨天/本周） | 前端会话列表分组逻辑 | 小（半天） |

### P2 — 低影响或高阻力（建议视需求决定）

| 功能 | 借鉴来源 | 落地路径 | 预估工作量 |
|------|---------|----------|-----------|
| **SSE 重连/状态恢复** | `resumeSession` | 需要会话状态持久化 + 前端重连逻辑 | 大（3-5 天） |
| **工具审批流** | `approval.requested/resolved` | 需要 gateway 配合 + 双向通信 | 大（3-5 天） |
| **消息编辑/重发** | 内联编辑 | 需要配合 fork 语义 | 中（1-2 天） |
| **子 agent 可视化** | `subagent.*` 事件 | 扩展 SSE 事件类型 | 中（1-2 天） |

---

## 6. 架构层面对比

### 6.1 状态管理

Hermes Studio 使用 Pinia store（`chatStore`），有完整的响应式状态树：

```typescript
// Hermes Studio: 集中式状态
chatStore = {
  sessions: Map<sid, SessionState>
  activeSessionId: string
  // SessionState 包含: messages[], isWorking, queue, tokens, events[]
}
```

Ikaros 的 conversation-tree 是单文件原生 JS，状态分散在全局变量中。**不建议**为此引入框架，但可参考 Pinia 的 session state 结构来优化 Ikaros 的内存状态管理。

### 6.2 事件协议

Hermes Studio 的 Socket.IO 事件系统（30+ 事件类型）比 Ikaros 的 8 个 SSE 事件丰富得多。建议逐步扩展 SSE 事件类型，但**不切换传输层**（SSE 对当前架构足够）。

### 6.3 服务端会话管理

Hermes Studio 的 `sessionMap`（内存 Map）+ `loadSessionStateFromDb()`（持久化回退）模式值得借鉴。Ikaros 的 conversation-tree 目前主要依赖 JSON 文件和 V5 DB，可增加内存缓存层。

---

## 7. 不宜借鉴的点

| Hermes Studio 做法 | 原因 |
|-------------------|------|
| Socket.IO 替代 SSE | Ikaros 的 SSE 设计简洁够用，Socket.IO 增加复杂度且与现有 gateway 架构不兼容 |
| Vue 3 + Pinia 替代原生 HTML | conversation-tree 的单文件 HTML 是其轻量优势，引入框架会破坏部署简易性 |
| `node:sqlite` 替代 Python sqlite3 | Ikaros 后端是 Python，不适用 Node.js 工具链 |
| Electron 桌面壳 | Ikaros 已有自己的 Electron 壳（control-panel），不重复造轮子 |

---

## 8. 总结

Hermes Studio 的 AI Chat 是一个**功能完备的线性会话桌面应用**，在多会话管理、工具展开、文件上传、会话搜索方面成熟度高。Ikaros 的 conversation-tree 在**树形分支对话**和**人格系统**上有独特优势，但在基础交互层面（搜索、上传、导出）缺失明显。

**建议路线：**

1. **第一优先级**：补齐搜索 + 上传 + 导出 三个基础功能（对标 Hermes Studio 的成熟度，但不破坏树形架构）
2. **第二优先级**：增加消息队列和工具结果显示优化（提升可靠性 + 用户体验）
3. **第三优先级**：SSE 重连恢复和工具审批流（视实际需求决定）

核心原则：**保持 conversation-tree 的树形架构和轻量部署特性，只吸收 hermes-studio 的基础交互经验，不引入其技术栈依赖。**
