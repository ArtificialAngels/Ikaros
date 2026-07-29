# N.E.K.O 对话框 Session 机制与上下文管理深度分析

> **日期**: 2026-07-24
> **范围**: `E:\Ikaros\core\neko` — LLMSessionManager、OmniOfflineClient、system_router、proactive pipeline
> **分析目标**: 6 个维度的问题诊断与解决方案

---

## 1. Session 建立与生命周期

### 1.1 架构总览

N.E.K.O 的会话架构是一个**纯内存、WS 耦合**的模型：

```
┌─────────────────────────────────────────────────────────────────┐
│                        N.E.K.O 会话架构                            │
│                                                                  │
│  LLMSessionManager (per-character 单例)                           │
│  ├─ self.websocket: WebSocket ──→ 前端 (通信通道)                 │
│  ├─ self.session: OmniOfflineClient | OmniRealtimeClient          │
│  │     └─ _conversation_history: List[BaseMessage] (纯内存)       │
│  ├─ self.is_active: bool                                          │
│  └─ self.state: SessionStateMachine (TurnOwner + ProactivePhase) │
│                                                                  │
│  会话 = WS 连接 + LLM 客户端 + 内存历史列表的三元组                │
│  生命周期: start_session() → ... → end_session()                  │
│  历史不持久化, 进程重启即丢失                                     │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 创建流程

```
start_session(websocket, input_mode)
  │
  ├─ 1. 并发守卫: _starting_session_count 原子递增
  │      同模式去重: 等待 in-flight 落定后补发 ack
  │      跨模式抢占: user_initiated=True 时递归重入
  │
  ├─ 2. 资源清理: _reset_proactive_gate() + idle reset loop
  │
  └─ 3. start_llm_session() [内部闭包, 第 6179 ��]
        ├─ HTTP GET memory_server:48912/new_dialog/{name}
        │     ← 获取记忆上下文 (persona + reflections + recent)
        ├─ _build_initial_prompt() 构建系统提示词
        │     = SESSION_INIT_PROMPT + lanlan_prompt + agent_tasks
        │       + ban_topic + anti_repeat_hint
        └─ 根据 input_mode 创建客户端:
              text  → OmniOfflineClient (OpenAI Chat API)
              audio → OmniRealtimeClient (Realtime WS API)
              connect(initial_prompt) → CAS 赋值 self.session
```

### 1.3 存储模型

| 数据 | 位置 | 持久化 | 生命周期 |
|------|------|--------|---------|
| `self.session` | core.py:865 | ❌ 纯内存 | start_session → end_session |
| `_conversation_history` | omni_offline_client.py:705 | ❌ 纯内存 | OmniOfflineClient 构造 → GC |
| 系统提示词 `_instructions` | omni_offline_client.py:695 | ❌ 纯内存 | 同上 |
| 记忆上下文 | Memory Server `memory/{name}/` | ✅ JSON/SQLite | 独立于 session |

### 1.4 销毁流程

```
end_session(by_server, expected_session)
  │
  ├─ abandon epoch 递增 (仅用户主动)
  ├─ stale-session guard: is_active + expected_session 双重检查
  ├─ is_active = False
  ├─ 清理音频流 / TTS / 回声缓存
  └─ 资源清理:
        ├─ cancel message_handler_task (max 3s)
        ├─ session.close() → LLM 客户端断开
        ├─ self.session = None
        └─ TTS runtime 拆除 + 输入缓存清空
```

**关键缺陷**: `_conversation_history` 随 `OmniOfflineClient` 实例一起被 GC 回收，**没有任何持久化保护**。

---

## 2. 上下文传递与连贯性

### 2.1 上下文传递链

```
一轮对话的完整上下文传递
═══════════════════════════

[Session 启动时]
  _build_initial_prompt()
    = SESSION_INIT_PROMPT (基础系统提示)
    + lanlan_prompt (角色人设)
    + agent task 摘要 (如有)
    + ban_topic 指令
    + anti_repeat 防复读
    → 作为 SystemMessage 固定保存在 _instructions

[用户发送消息时]
  _process_stream_data_internal()
    → session.stream_text(data)
      → 1. 追加 HumanMessage(content=user_text) 到 _conversation_history
      → 2. 构建 messages = [SystemMessage(_instructions), ...全部 _conversation_history...]
      → 3. LLM API 调用 (messages)
      → 4. 收到 AI 回复 → handle_text_data 回调
      → 5. 追加 AIMessage(content=reply) 到 _conversation_history
      → 6. TTS 管线 + WS 推送

[主动搭话时]
  Phase 2 构建:
    messages = [SystemMessage(generate_prompt), HumanMessage(begin_text)]
    → 不使用 _conversation_history! (设计行为, 见 §4)
    → 独立 prompt, 含 memory_context 占位符
    → 生成后通过 finish_proactive_delivery 追加 AIMessage 到 _conversation_history
```

### 2.2 连贯性保障机制

| 机制 | 作用 | 覆盖范围 |
|------|------|---------|
| `_conversation_history` 全量传递 | 所有轮次 Human/AI 消息 | 当前 session 内 |
| `_instructions` (SystemMessage) | 固定人格设定 | 整个 session 生命周期 |
| Memory Server `/new_dialog` | 压缩后的跨 session 记忆 | Session 启动时一次性获取 |
| anti_repeat corpus | 防话题复读 | 当前 session 的 ngram 记录 |
| proactive_chat_history | 过往主动搭话记录缓存 | 仅 proactive 间互通 |

### 2.3 连贯性断裂场景

| 场景 | 原因 | 后果 |
|------|------|------|
| **同 session text 对话** | `_conversation_history` 完整传递 | ✅ 连贯 |
| **新 session 对话** | `OmniOfflineClient.__init__` → `_conversation_history = []` | ❌ 前 session 历史丢失 |
| **text ↔ audio 模式切换** | `end_session` + `start_session(new_mode)` | ❌ 历史丢失 |
| **idle 30min 超时** | `_idle_session_reset_loop` → `end_session` | ❌ 历史丢失 |
| **proactive → user reply** | 可能触发模式切换重建 session | ❌ 见 §4 根因分析 |

---

## 3. 主动搭话触发机制与运行流程

### 3.1 完整链路

```
[前端] scheduleProactiveChat() 递归 setTimeout
  │  Leader 选举 (BroadcastChannel, TTL=15s)
  │  三段式退避: 10s → 60min
  │
  ▼ HTTP POST /api/proactive_chat
[后端] system_router.proactive_chat()
  │
  ├─ Activity snapshot (活动状态快照)
  ├─ Gate guard: try_start_proactive() 原子抢占 SM
  │     409 = busy → 跳过本轮
  │
  ├─ Phase 0: 并行信息源收集 (asyncio.gather)
  │     ├─ 屏幕截图 + 窗口标题 (vision)
  │     ├─ 微博热搜 / 新闻 (news)
  │     ├─ B站推荐 (video)
  │     └─ 用户活动状态 (activity)
  │
  ├─ /new_dialog 获取记忆上下文 (HTTP :48912)
  │
  ├─ Phase 1: LLM 主题筛选 (1 次 LLM 调用)
  │     prompt = 记忆 + 信息源 + 角色 + 指令
  │     输出: [PASS] 跳过 / 选择的话题内容
  │
  ├─ Phase 2: LLM 流式生成搭话文本 (1 次 LLM 调用)
  │     prompt = 角色 + 话题 + 风格指令
  │     astream → 逐 chunk 推送到 TTS/WS
  │
  ├─ finish_proactive_delivery()
  │     ├─ sid guard (防抢占)
  │     ├─ send_lanlan_response() → 前端气泡
  │     ├─ _conversation_history.append(AIMessage) ← 写入用户 session 历史
  │     └─ anti_repeat corpus 记录
  │
  └─ fire(PROACTIVE_DONE) → 状态机复位 IDLE
```

### 3.2 两个独立 Session 通道

```
             ┌──────────────────────────┐
             │  Phase 1/2 LLM 调用路径    │
             │  (独立局部 LLM client)     │
             │                           │
             │  prompt = 独立构建         │
             │  不含 _conversation_history│
             │  含 memory_context 占位符  │
             └──────────┬───────────────┘
                        │ 生成文本
                        ▼
             ┌──────────────────────────┐
             │  finish_proactive_delivery │
             │                           │
             │  1. 推送到前端 (WS)        │
             │  2. 追加到 _conversation   │
             │     _history (供后续使用)   │
             │  3. anti_repeat 记录       │
             └──────────────────────────┘
```

**核心设计**: Phase 1/2 的 LLM 调用和用户消息的 LLM 调用使用**独立的 prompt 构建路径**。proactive 的 prompt 不包含 `_conversation_history`，而是通过 `/new_dialog` 获取的记忆。这是有意为之——proactive 是发起新话题，而非"继续对话"。

---

## 4. Session 隔离问题的根因与修复

### 4.1 根因定位

经过代码追踪，session 隔离的根因有 **3 条路径**：

#### 根因 #1 (最常见)：text → audio 模式切换

```
时间线:
  T1: Proactive 运行 → prepare_proactive_delivery() 发现无 session
        → start_session(websocket, input_mode='text')
        → 创建 OmniOfflineClient (_conversation_history = [])
  
  T2: finish_proactive_delivery()
        → _conversation_history.append(AIMessage("今天的新闻是..."))
        → history = [AIMessage("今天的新闻是...")]
  
  T3: 用户语音回复 (input_type='audio')
        → _process_stream_data_internal()
        → 检查: not isinstance(self.session, OmniRealtimeClient)
        → end_session() ← 销毁 OmniOfflineClient, history 丢失!
        → start_session(input_mode='audio')
        → 创建 OmniRealtimeClient (无 _conversation_history 属性)
        → 用户的语音回复:L 看不到 AI 刚说的"今天的新闻是..."
```

**影响**: 语音模式用户 + 主动搭话 → 上下文断裂。这是最常见的场景。

#### 根因 #2：空闲超时后 session 重建

```
  T1: Proactive 投递 → history = [AIMessage]
  T2: 30 分钟无用户回复
  T3: _idle_session_reset_loop → end_session()
  T4: 用户回复 → start_session() → 空 history
```

**影响**: 长时间无人应答后回复，proactive 内容丢失。

#### 根因 #3：Phase 2 prompt 不含 `_conversation_history`

```
  system_router.py:7170
  messages = [SystemMessage(content=generate_prompt), HumanMessage(content=human_content)]
  
  generate_prompt 不从 _conversation_history 读取,
  仅从 memory_context (Memory Server) 和 _proactive_chat_history (独立缓存) 获取
```

**影响**: 即使在同一 session 内，proactive 也看不到当前 session 中的用户对话历史。

### 4.2 修复方案

#### 修复 A：proactive → user reply 上下文桥接 (P0)

在 `_process_stream_data_internal` 的 session 重建路径中，增加历史抢救：

```python
# core.py:_process_stream_data_internal, 在 end_session 之前
async def _salvage_conversation_history(self) -> list:
    """在 session 重建前抢救历史."""
    if not self.session:
        return []
    if hasattr(self.session, '_conversation_history'):
        return list(self.session._conversation_history)  # 快照
    return []

# 在重建 session 后, 将抢救的历史注入新 session
async def _restore_conversation_history(self, history: list):
    if history and hasattr(self.session, '_conversation_history'):
        self.session._conversation_history = history
```

改动点:
1. `_process_stream_data_internal` 第 9531 行（text 重建）: 在 `end_session` 前调用 `_salvage_history`
2. 第 9788 行（audio 重建）: 同上
3. `_restore_conversation_history` 在 `start_session` 成功后调用

#### 修复 B：OmniRealtimeClient 支持 `_conversation_history` (P0)

```python
# omni_realtime_client.py
class OmniRealtimeClient:
    def __init__(self, ...):
        ...
        self._conversation_history: list = []  # 新增
```

目前 `OmniRealtimeClient` 完全不具备 `_conversation_history` 属性。即使不做全量传递，至少提供空列表让历史抢救写入。

#### 修复 C：Idle reset 前持久化抢救 (P1)

在 `_idle_session_reset_loop` 调用 `end_session` 前，将 `_conversation_history` 写入 V5 store 作为临时记忆：

```python
# core.py:_idle_session_reset_loop
async def _idle_persist_before_reset(self):
    if not self.session or not hasattr(self.session, '_conversation_history'):
        return
    history = self.session._conversation_history
    if len(history) > 2:  # 至少一轮有效对话
        summary = "\n".join([f"{m.type}: {m.content[:200]}" for m in history[-10:]])
        try:
            from v5 import store
            store.store(summary, type="conversation",
                        tags="session_salvage", character=self.lanlan_name)
        except Exception:
            pass  # 抢救失败不阻塞 reset
```

#### 修复 D：Proactive Phase 2 注入 session 历史上下文 (P2)

在 `generate_prompt` 构建时，有条件地注入当前 session 的用户对话：

```python
# system_router.py:Phase 2 prompt 构建
if mgr.session and hasattr(mgr.session, '_conversation_history'):
    recent = mgr.session._conversation_history[-4:]  # 最近 4 轮
    if recent:
        context_block = "\n".join([
            f"{'你' if isinstance(m, HumanMessage) else '我'}: {m.content[:100]}"
            for m in recent
        ])
        generate_prompt = generate_prompt.replace(
            "{recent_context}", context_block
        )
```

---

## 5. 主动搭话的人格设定与记忆系统

### 5.1 人格设定来源

主动搭话的人格来源于 **3 个层次**：

```
层次 1: 角色配置文件 (静态)
  ├─ config/characters/{lang}.json → 角色名/称呼/语气
  ├─ config/prompts/prompts_proactive.py → 主动搭话专用 prompt 模板
  └─ 内嵌角色描述 (lanlan_prompt)

层次 2: Memory Server 运行时 (动态)
  ├─ /new_dialog → persona.json (长期人格画像)
  │     ├─ master: 用户画像 (偏好/习惯/关系)
  │     ├─ neko: AI 自我认知
  │     └─ relationship: 关系动态
  ├─ /new_dialog → reflections.json (反思综合)
  │     ├─ pending → 待确认反思
  │     └─ confirmed → 已确认反思
  └─ /new_dialog → recent.json (压缩对话历史)

层次 3: V5 元认知层 (启用 Ikaros 集成时)
  ├─ affect.json → PAD 情感状态
  ├─ self_model.json → 自我认知/信念/探索欲
  ├─ metacog → latest_thought.json
  └─ cloud_chat.build_system_prompt() → 动态 system prompt
```

### 5.2 记忆构建流程

```
[Proactive Phase 1 prompt 中的 memory_context]
  = 最近对话摘要 (recent.json)
  + 人格画像 (persona.json, 渲染为 markdown)
  + 活动状态快照 (activity tracker)
  + 历史搭话记录 (_proactive_chat_history 缓存)
  + 可选: followup topics (回调话题候选)
```

### 5.3 持久化机制

| 数据 | 存储位置 | 更新时机 | 生命周期 |
|------|---------|---------|---------|
| persona.json | `memory/{name}/` | /settle + 证据更新 | 永久 |
| reflections.json | `memory/{name}/` | /reflect + 后台循环 | 永久 |
| recent.json | `memory/{name}/` | 每轮对话后压缩 | 永久 |
| `_conversation_history` | `OmniOfflineClient._conversation_history` | 每轮对话追加 | session 内 |
| `_proactive_chat_history` | system_router.py 全局变量 | 每次 proactive 投递后 | **纯内存, 不持久化** |
| V5 store | `core/memory_v5/data/v5/v5.db` | store() 调用时 | 永久 (如启用) |

**关键缺陷 1**: `_proactive_chat_history` 是纯内存全局变量，进程重启后丢失。这意味着 AI 不知道它"上次"主动说过什么。

**关键缺陷 2**: `_conversation_history` 不持久化。session 重建后新 session 不知道旧 session 的对话内容。即使 `/new_dialog` 从 Memory Server 获取记忆，也只能拿到压缩摘要，丢失了完整对话的细节。

---

## 6. 响应速度瓶颈与优化

### 6.1 典型响应时间预算

```
一轮完整对话 (从用户发送到收到回复)
═══════════════════════════════════

┌─ 管线阶段 ──────────────┬── 典型延迟 ──┬── 优化方案 ──────────┐
│                         │              │                      │
│ 1. WS 接收 + 路由       │ <1ms         │ -                    │
│                         │              │                      │
│ 2. Session 检查/重建    │ 0-500ms      │ 修复 B: 抢救 history │
│                         │              │                      │
│ 3. Ikaros V5 集成       │ 5-15s ★★★   │ 可选关闭 (V5_PROXY=0)│
│   (asyncio.to_thread)   │              │ 或用本地 LLM 兜底    │
│                         │              │                      │
│ 4. LLM API 调用         │ 2-8s ★★     │ 缓存/更小模型/流式   │
│   (OpenAI/DeepSeek)     │              │                      │
│                         │              │                      │
│ 5. stream_to_neko       │ 340-1000ms   │ 减少 sleep(0.02)→5ms │
│   人工分块延迟          │              │                      │
│                         │              │                      │
│ 6. TTS 管线             │ 1-3s (不阻塞)│ - (异步队列)         │
│                         │              │                      │
│ 7. WS 推送              │ <1ms         │ -                    │
├─────────────────────────┼──────────────┼──────────────────────┤
│ 总计 (用户感知延迟)      │ 3-15s        │ 优化后: 2-5s         │
└─────────────────────────┴──────────────┴──────────────────────┘
```

### 6.2 前 3 大瓶颈

#### 瓶颈 #1：LLM 调用延迟 (80-90% 响应时间)

| 阶段 | 调用位置 | 超时 | 典型延迟 |
|------|---------|------|---------|
| Proactive Phase 1 | system_router.py:6618 | 16s | 2-5s |
| Proactive Phase 2 | system_router.py:7249 | 25s | 2-5s |
| 格式修正重生成 | system_router.py:7398 | 20s | 0 (偶尔 2-5s) |
| BM25 重生成 | system_router.py:7626 | 25s | 0 (偶尔 2-5s) |
| Ikaros orchestrator | ikaros_integration.py:74 | 30s+ | 5-15s |

**优化方案**:
1. **LLM 响应缓存**: 对高频 query (问好、表情) 使用 LRU 缓存
2. **模型选择**: 主动搭话用本地 Qwen3-1.7B (:8080)，用户消息用 DeepSeek 云端
3. **Phase 1 超时缩短**: 16s → 8s (Phase 1 只是主题筛选，不需要深度推理)
4. **格式修正降级**: BM25 触发时不重新生成，改为在 prompt 尾部追加"避免重复"指令

#### 瓶颈 #2：同步文件 I/O 阻塞事件循环 (10-50ms/请求)

| 文件 | 位置 | 当前 I/O | 优化方案 |
|------|------|---------|---------|
| persona.py | 第 299 行 | `open()` + `json.load()` (同步) | → `asyncio.to_thread` + 进程内 LRU 缓存 |
| facts.py | 第 163 行 | `open()` + `json.load()` (同步) | → `asyncio.to_thread` |
| reflections.json | 单请求读 3 次 | 无跨请求缓存 | → `/new_dialog` 内合并为 1 次读取 |

#### 瓶颈 #3：Ikaros 集成人工延迟 (340-1000ms)

```python
# ikaros_integration.py:149
await asyncio.sleep(0.02)  # 每 6 字符 20ms × N 块
```

**优化**: 移除 `sleep`，或将 20ms → 5ms。原生 LLM 流式本身已有自然的字符间隔，不需要人工模拟。

### 6.3 主动搭话优化专项

当前每次 proactive 周期包含 **2-4 次串行 LLM 调用**，典型耗时 6-15s：

```
Phase 0: 信息收集 ──── 200-800ms (并行)
Phase 1: LLM 主题筛选 ─ 2-5s (串行)
Phase 2: LLM 生成 ──── 2-5s (串行)
可选的: 格式修正 ──── 0-5s (串行, 偶尔)
可选的: BM25 重生成 ── 0-5s (串行, 偶尔)
─────────────────────────────────
总计: 6-15s (可优化至 3-8s)
```

**优化方案**:
1. **Phase 1 + Phase 2 合并**: 一次 LLM 调用完成"筛选 + 生成"，通过 prompt 约束 `[PASS] | <生成文本>` 二选一输出
2. **BM25 预检查**: 在 LLM 调用前先做 BM25 检查，高重复时直接跳过
3. **格式修正内联**: 在 prompt 中加 `只回复对话内容，不加任何前缀` 约束，减少格式修正触发概率
4. **信息源并行 + 超时缩短**: 信息源获取当前 5s 超时 → 改为 2s

---

## 附录：修复优先级矩阵

| 编号 | 问题 | 影响范围 | 难度 | 优先级 | 涉及文件 |
|------|------|---------|------|--------|---------|
| A | proactive→user 上下文桥接 (history 抢救) | 语音用户上下文断裂 | ⭐⭐ | **P0** | core.py |
| B | OmniRealtimeClient 加 `_conversation_history` | 语音用户 session 连续性 | ⭐ | **P0** | omni_realtime_client.py |
| C | Idle reset 前持久化抢救 | 长时间隔用户 | ⭐⭐ | P1 | core.py |
| D | Phase 2 注入 session 历史上下文 | Proactive 上下文感知 | ⭐⭐ | P2 | system_router.py |
| E | 移除 stream_to_neko 人工 sleep | 响应速度 (~1s) | ⭐ | P1 | ikaros_integration.py |
| F | persona.py 同步 I/O 改异步 | 事件循环阻塞 | ⭐⭐ | P2 | persona.py |
| G | Proactive Phase 1+2 合并为一次 LLM | 搭话响应速度 (3-5s) | ⭐⭐⭐ | P2 | system_router.py |
| H | LLM 响应缓存 | 高频 query 加速 | ⭐⭐ | P2 | core.py |
