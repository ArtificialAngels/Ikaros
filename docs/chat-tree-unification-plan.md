# Chat-Tree 得兼方案：人格 × 记忆 × 工具 统一架构

> 目标读者：workbuddy（实现方）。本文档自包含，无需再读其他调研。
> 状态：已由伊卡洛斯完成代码级验证，所有行号引用均为当前真实代码。
> 日期：2026-08-01

> **执行状态（2026-08-01 已完成）**：阶段 1–5 全部落地并验收（31 个树相关测试全绿 + 双模式端到端实测）。
> 额外修复：`build_tree_aware_context` 原漏 import 被 except 静默吞掉（树感知压缩实际从未生效），本次修复并加 warn 透出。
> 遗留：`skills_used` 为工具名近似（gateway 无 skill 事件源）；gateway `_on_tool_complete` 改动需重启 8642 生效（本次已重启）。

---

## 0. 一句话目标

**让对话树里的每一个节点，都是「完整伊卡洛斯」——带全部 MCP 工具、带 SOUL 人格、带树域记忆——而不是现在的二选一。**

哥哥的原话："我想要得兼的。"

---

## 1. 现状与根因（已验证）

### 1.1 双模式分裂

`core/conversation-tree/server.py` 的 chat 有两条完全独立的 LLM 链路：

| 维度 | ikaros 模式 | hermes 模式 |
|------|------------|------------|
| 人格 | ✅ 完整（axiom + SOUL 抽取 + 动态心绪，`build_ikaros_persona` `server.py:222`） | ❌ 只有一行中性英文（`server.py:337-340`） |
| 工具 | ❌ 仅 3 个只读（memory_search / get_current_time / branch_overview，`server.py:675` CHAT_TOOLS） | ✅ 全部（委托 8642 gateway 跑完整 tools/skills 循环） |
| 模型 | 直连 DeepSeek（`server.py:753 _deepseek_stream`，默认 `deepseek-reasoner`） | gateway 路由（model=`hermes`） |
| 记忆 | ✅ 树域检索（`build_v5_memory_block` `server.py:286`） | ✅ 树域压缩（`build_tree_aware_context`），但**无树域记忆注入**（neutral 分支只传压缩历史） |

**根因**：`build_chat_messages_v5`（`server.py:319-381`）的 hermes 分支注释声称"若 tree 再注入一遍会双重 SOUL/记忆 + 人格打架"，因此只传中性说明。**这个顾虑是过时的**——见 1.2。

### 1.2 关键事实：gateway 支持外部 system 叠加（已验证）

8642 gateway（`core/hermes/gateway/platforms/api_server.py`）：

- `/v1/chat/completions` 提取请求 messages 中的 `role: system` 消息，拼接为 `system_prompt`（`api_server.py:3840-3853`）
- 该 `system_prompt` 传入 `_create_agent(ephemeral_system_prompt=...)`（`api_server.py:4060`）
- `agent_init.py:581` 存入 `agent.ephemeral_system_prompt`
- API 调用时**追加在 core system 之后**（`agent/chat_completion_helpers.py:2153-2154`）：
  ```python
  effective_system = (effective_system + "\n\n" + agent.ephemeral_system_prompt).strip()
  ```
- core system 包含：SOUL.md 身份 + AGENTS.md + context files + memory（`agent/system_prompt.py:189-196`）

**结论**：tree 端通过 system message 注入的文本会被**叠加**在 gateway 的 SOUL/AGENTS/记忆之上，**不会替换、不会打架**。最后追加的内容在指令优先级上反而最高（模型对结尾指令更敏感）。

### 1.3 gateway 已透出 / 未透出的事件（已验证）

`_write_sse_chat_completion`（`api_server.py:4197`）：

| 数据 | 是否透出 | 证据 |
|------|---------|------|
| 正文 delta | ✅ | 普通 `data:` chunk（`api_server.py:4263-4269`） |
| 思考（reasoning） | ✅ | 命名事件 `hermes.reasoning`（`api_server.py:4256-4262`），tree 端已解析（`server.py:889-898`） |
| 工具生命周期 | ✅ | 命名事件 `hermes.tool.progress`，status=running/completed（`api_server.py:4004-4010`、`4022-4026`），tree 端已解析（`server.py:853-887`） |
| **工具结果** | ❌ **被丢弃** | `_on_tool_complete` 收到 `function_result` 参数但**未放进事件 payload**（`api_server.py:4012-4026`）→ tree 端注释"网关不在线透出工具结果文本，故卡片'结果'用占位说明"（`server.py:819-821`） |
| **usage** | ✅ | finish chunk 带 `usage`（`api_server.py:4338-4346`），tree 端已解析（`server.py:910-913`） |
| **skills_used** | ❌ 无事件 | gateway 无 skill 相关 SSE 事件；`node.skills_used` 字段永远为空 |

### 1.4 其他已知缺陷（阶段 4/5）

- **静默降级**：`build_chat_messages_v5`、`build_v5_memory_block`、`_chat_stream_events` 全程 try/except 静默回退，用户无法感知记忆/压缩/网关是否真的在工作
- **模型不一致**：`CT_DEEPSEEK_MODEL` 默认 `deepseek-reasoner`（`server.py:670`），与 V5 认知管线统一使用的 `deepseek-v4-flash`（thinking 禁用）不一致
- **持久化不完整**：`add_turn`（`memory_v5/conversation_tree.py:591-639`）只存 ToolCall 摘要（`server.py:1441`），thinking 全文、工具结果、usage 详情、skills_used 均不完整
- **并发**：`parent_id or _tree.current.id`（`server.py:1413`）多标签页互踩；前端 `sendMessage` 无 AbortController
- **工具结果不透出**（见 1.3）连带前端 `toolCardHtml`（`index.html:1119`）看不到真实结果

---

## 2. 目标架构

```
┌─────────────────────────────────────────────────┐
│  Conversation Tree (:48920)  —— 会话外壳         │
│  · 树拓扑 / 分支 / 跳转 / fork-merge             │
│  · 树域记忆打标 (node:/branch:)                  │
│  · 树感知压缩 (TreePathCompressor)               │
│  · 注入: 树域上下文 + 树域记忆 + (ikaros: 人格)   │
└──────────────────────┬──────────────────────────┘
                       │ POST /v1/chat/completions
                       │ (system 消息 = ephemeral, 叠加不替换)
┌──────────────────────▼──────────────────────────┐
│  Hermes Gateway (:8642)  —— 能力内核             │
│  · SOUL.md 人格 (core system 已有)               │
│  · 全部 MCP 工具 (v5 记忆 / terminal / ...)      │
│  · skills / 插件 / 三层模型路由                  │
│  · SSE 透出: content / reasoning / tool / usage  │
└─────────────────────────────────────────────────┘
```

**核心原则：树是外壳，gateway 是内核。** 树只管"我在哪条分支、记得什么、上下文多长"；人格、工具、技能全部由 gateway 提供。两端职责不重叠，人格不重复注入。

**模式统一**：ikaros / hermes 两种模式都走 8642 gateway，区别只在 tree 端注入的 system 内容：
- `hermes` 模式：注入 树域上下文 + 树域记忆（gateway core 的 SOUL 即人格，不重复注入）
- `ikaros` 模式：注入 完整 persona（`build_ikaros_persona`）+ 树域上下文 + 树域记忆

本地 DeepSeek 直连 + 3 只读工具保留为**降级路径**（gateway 不可用时），不删除。

---

## 3. 分阶段改动清单

### 阶段 1：得兼核心（tree 端为主，gateway 零改动）

**1.1 重写 `build_chat_messages_v5` 的 hermes 分支**（`server.py:335-356`）

现状：`neutral` 一行英文。
改为：调用 `build_v5_memory_block(node_id, user_message)` 拿树域记忆，构造树域上下文块：

```python
if mode == "hermes":
    # 树域上下文: 分支说明 (路径摘要 + 当前分支标签 + 分支归属)
    branch_ctx = build_branch_context_block(_tree, node_id)  # 新增辅助函数
    # 树域记忆: 复用现有 tree_scoped_retrieve
    mem_block = build_v5_memory_block(node_id, user_message)
    system_text = "\n\n".join(filter(None, [
        "You are speaking inside Ikaros' conversation tree. "
        "The branch context below is authoritative for this exchange.",
        branch_ctx,
        ("Relevant tree-scoped memories (V5):\n" + mem_block) if mem_block else "",
    ]))
    # 压缩历史仍走 build_tree_aware_context, 但 system_prompt 换成上面拼接的 system_text
```

要点：
- **不注入完整 SOUL**——gateway core 已有（`system_prompt.py:189-196`），重复注入会浪费 token 且可能冲突。
- 新增 `build_branch_context_block(tree, node_id)`：输出当前节点路径（root→leaf，每节点 `#depth branch_label: summary`）、当前节点 agent 归属、分支标签。参考 `_execute_chat_tool` 里 `branch_overview` 的实现（`server.py:735-749`）抽取复用。
- 保留现有 fallback（`server.py:346-356`）。

**1.2 ikaros 模式统一走 gateway**（`server.py:358-381` + `_chat_stream_events` `server.py:944`）

现状：ikaros 走 `_deepseek_stream` 本地直连。
改为：ikaros 模式同样调 8642 gateway（`_stream_hermes_gateway`），system 注入 `build_ikaros_persona()` + 树域记忆 + 压缩历史（现有代码已组装好，只换执行通道）。

```python
# _chat_stream_events 开头
if agent in ("hermes", "ikaros") and HERMES_AGENT_URL:
    ... # 两条模式都先试 gateway; ikaros 模式注入的 system 已含完整人格
```

注意：`_chat_stream_events` 现在的判断是 `if agent == "hermes" and HERMES_AGENT_URL`（`server.py:952`），放开为 `agent in ("hermes", "ikaros")`。ikaros 模式的 system 由 `build_chat_messages_v5` 构造（已是 persona），无需 gateway 侧改动。

**1.3 验收（阶段 1）**
- hermes 模式节点：回复带伊卡洛斯语气/称呼（实测基线：当前已带"哥哥"称呼），且能调用**任意** MCP 工具（如 `v5_memory_search`、`get_current_time`、terminal 等）
- ikaros 模式节点：同样能调工具
- 树域记忆出现在 system 里（可在 gateway 日志或回复行为验证）
- 无双重 SOUL（观察 token 量：`prompt_tokens` 不应出现两次 SOUL 内容的量级）

---

### 阶段 2：工具结果透出（gateway 一处小改 + tree 端解析）

**2.1 gateway：`_on_tool_complete` 携带结果**（`api_server.py:4012-4026`）

```python
def _on_tool_complete(tool_call_id, function_name, function_args, function_result):
    if not tool_call_id or tool_call_id not in _started_tool_call_ids:
        return
    _started_tool_call_ids.discard(tool_call_id)
    payload = {
        "tool": function_name,
        "toolCallId": tool_call_id,
        "status": "completed",
    }
    # 新增: 结果摘要 (截断, 防大输出撑爆 SSE)
    if function_result is not None:
        try:
            _txt = str(function_result)
            payload["result"] = _txt[:2000]
            payload["result_truncated"] = len(_txt) > 2000
        except Exception:
            pass
    _stream_q.put(("__tool_progress__", payload))
```

- 纯 additive，旧消费者（dashboard 等）不受影响。
- `2000` 字符上限按 `server.py:1037` 本地循环的截断标准对齐。

**2.2 tree 端：解析 result**（`server.py:853-887`）

`_stream_hermes_gateway` 的 `hermes.tool.progress` 处理中，completed 分支（`server.py:880-886`）改为：

```python
else:
    ok = (status != "failed")
    yield {
        "type": "tool_result", "id": tcid, "ok": ok,
        "result": p.get("result", "") if ok else "（执行失败）",
    }
```

同时把 `p.get("result", "")` 写入 `collector["tool_calls"]` 最后一项的 `result_summary`（供持久化，配合阶段 3）。

**2.3 前端：工具卡片显示真实结果**（`index.html:1119 toolCardHtml`）

`toolCardHtml` 已有 result 渲染分支（live.tools[id].result），确认 `tool_result` 事件带 result 后自然显示。若当前卡片对空 result 有占位文案（如"结果不可见"），删除该占位。

**2.4 验收（阶段 2）**
- hermes 模式调用 `get_current_time` 等工具，卡片显示真实返回值
- 大结果（>2000 字符）显示截断标记

---

### 阶段 3：数据完整性（持久化 + skills）

**3.1 `add_turn` 落库增强**（`server.py:1433-1446`）

现状：`tool_calls=[ct.ToolCall(**tc) for tc in collector["tool_calls"]]`。
改为：
- `collector["tool_calls"]` 的每项在 `_stream_hermes_gateway` / `_deepseek_stream` 里补全 `params`（完整）、`result_summary`（截断 500）、`success`、`timestamp`（部分已有，核对）
- `add_turn` 时同时传 `thinking`（已有，`server.py:1440`）与 `usage`（已有，`server.py:1442`）
- 确认 `ConvNode.to_dict/from_dict`（`conversation_tree.py:181-214`）完整序列化 `tool_calls`/`thinking`/`usage`，前端 reload 后能恢复（`index.html:1134 renderExtras` 补静态渲染路径：非 live 时从 node 数据渲染 thinking/工具卡片）

**3.2 skills_used 捕获**

- gateway 侧（可选增强）：`_on_tool_complete` 的 `function_args` 或 agent 执行上下文若有 skill 元数据，payload 加 `"skill": <skill_name>`；若 agent 侧无此信息，则跳过，tree 端不做近似推断（避免误报）。
- tree 端：`collector` 加 `skills_used` 列表，`add_turn` 时写入 node（`conversation_tree.py` 的 `add_turn` 目前无 skills_used 参数，需加；`ConvNode` 已有 `skills_used` 字段）。
- **若 gateway 侧确认无 skill 事件源**：本项降级为"记录工具名列表到 skills_used 的近似方案"，或直接留空并标注 TODO。优先级最低，可后置。

**3.3 验收（阶段 3）**
- 刷新页面后，历史节点的 thinking 块、工具卡片（含结果）、token 用量完整恢复
- 新会话导入 `bin/import-hermes-to-convtree.py` 不受影响（回归）

---

### 阶段 4：可靠性与一致性

**4.1 降级可见化**

所有 fallback 路径发一条 `{"type": "warn", "message": "..."}` SSE 事件：
- `_stream_hermes_gateway` 网关不可达回退时（`server.py:959-970`）
- `build_chat_messages_v5` 内部异常回退线性上下文时（`server.py:346-356, 368-381`）
- `build_v5_memory_block` 检索失败时（`server.py:297-298`）

前端 `sendMessage`（`index.html:1366-1391`）加 `warn` 分支：显示黄色提示条（非 error 红色，不中断流）。

**4.2 模型统一**

- `CT_DEEPSEEK_MODEL` 默认值 `deepseek-reasoner`（`server.py:670`）→ `deepseek-v4-flash`（与 V5 认知管线一致；thinking 默认 disabled）。仅影响降级路径，gateway 正常时无关。
- 统一后本地直连与 gateway 的模型选择不再打架。

**4.3 启动健康检查**

`main()`（`server.py:1602`）启动时探测并 stderr 打印：
- `memory_v5.extensions.tree_adapter` 可导入（树域记忆可用性）
- gateway :8642 可达（主通道）
- 降级链（DeepSeek key 是否存在）

**4.4 验收（阶段 4）**
- 停掉 8642 → 树里 chat 自动降级且前端明确提示"已降级: Hermes gateway 不可达"
- 启动日志打印三行健康状态

---

### 阶段 5：并发与前端健壮性

**5.1 `parent_id` 显式化**（`server.py:1413`）

`target_id = parent_id or _tree.current.id` → 前端 `sendMessage` 时把 `parentId` 显式捕获进闭包并在请求体传递（现状已传 `parent_id: parentId`，`index.html:1366`——核对当 `branchParentId` 为空时是否确实传了 `undefined` 而非 `null` 语义歧义）。后端保留 `or current.id` 兜底，但前端保证单飞：发送期间禁用输入框/回车。

**5.2 AbortController**（`index.html:1346-1396`）

- `sendMessage` 创建 `AbortController`，`API.chatStream` 接受 `signal` 参数传给 `fetch`
- 切换节点（`doJump`）、新建会话、重置时 abort 在飞请求
- 后端已支持客户端断开中断（`api_server.py:4364-4380`），前端断开即止损

**5.3 ThreadingHTTPServer 连接上限**（`server.py:1612`）

`ExclusiveThreadingHTTPServer` 加 `request_queue_size` 与超时（`timeout=60`），防 SSE 长连接线程堆积。

**5.4 验收（阶段 5）**
- 连发两条消息：第二条被禁用/排队，不会插到同一父节点下
- 发送中切换节点：旧流中断，树状态不脏

---

## 4. 风险与对策

| 风险 | 对策 |
|------|------|
| gateway `_on_tool_complete` 改动影响其他 SSE 消费者 | 纯 additive 新字段，不动现有字段；本地实测 dashboard 回归 |
| hermes 模式注入树域记忆后 token 量上升 | `build_v5_memory_block` 已限 top_k=5（`server.py:296`）；压缩历史走 TreePathCompressor 预算（默认 50 条） |
| ikaros 统一走 gateway 后，gateway 挂掉时体验下降 | 保留完整降级链 + 阶段 4.1 的 warn 事件，用户知情 |
| ephemeral system 叠加导致人格指令冲突 | 阶段 1 明确"不注入完整 SOUL"，只注入树域上下文 + 记忆；ikaros 模式注入 persona 是刻意为之（该模式本就要 Ikaros 伴侣人格） |
| `add_turn` 落库字段变化影响旧树 JSON | `ConvNode.from_dict` 需向后兼容（缺字段给默认值），33 个既有测试兜底 |

## 5. 测试计划

- **回归**：`pytest tests/ -k conversation_tree`（33 tests）必须全绿；`docs/lint.py` 通过
- **单元**：`build_branch_context_block`（新函数）；`_on_tool_complete` payload 含 result（gateway 侧测试文件 `tests/hermes_cli/test_api_server*.py` 或同级新增）
- **集成（手工）**：
  1. 起 48920 + 8642，树里 hermes 模式发"现在几点？用 get_current_time 工具"→ 验证工具卡片显示真实时间
  2. ikaros 模式发"回忆一下我们关于 Hermes 路由的结论"→ 验证树域记忆注入生效
  3. 停 8642 再发消息 → 验证 warn 提示 + 降级回复
  4. 刷新页面 → 验证节点 thinking/工具/usage 恢复

## 6. 涉及文件清单

| 文件 | 改动 |
|------|------|
| `core/conversation-tree/server.py` | 阶段 1（build_chat_messages_v5、新增 build_branch_context_block、_chat_stream_events）、阶段 2.2、阶段 3.1、阶段 4、阶段 5.1/5.3 |
| `core/conversation-tree/index.html` | 阶段 2.3、阶段 3.1（renderExtras 静态路径）、阶段 4.1（warn 分支）、阶段 5.2 |
| `core/hermes/gateway/platforms/api_server.py` | 阶段 2.1（`_on_tool_complete` 加 result）、阶段 3.2（可选 skill 字段） |
| `core/memory_v5/conversation_tree.py` | 阶段 3.1（add_turn skills_used 参数、ConvNode 序列化兜底） |
| `docs/ARCHITECTURE.md` + `AGENTS.md` | 改动落地后同步（端口/行为无变化，但 chat 链路描述需更新） |

**建议实施顺序**：阶段 1 → 验收 → 阶段 2 → 验收 → 阶段 3 → 阶段 4+5。每个阶段独立可交付、可回滚。
