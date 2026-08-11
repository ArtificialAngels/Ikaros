# N.E.K.O 前端聊天系统架构分析

> **日期**: 2026-07-25
> **范围**: `E:\Ikaros\apps\neko\` — 完整前端聊天链路
> **参考项目**: `E:\Ikaros-something\reference project\N.E.K.O-main`

---

## 1. 记忆架构 — 数据存储与检索

### 1.1 存储后端（三种）

| 存储类型 | 文件格式 | 路径模式 | 用途 | 模块 |
|---|---|---|---|---|
| **JSON 文件** | `facts.json`, `persona.json`, `reflections.json`, `recent.json`, `settings.json` | `memory/{name}/*.json` | 持久化长期记忆 | `FactStore`, `PersonaManager`, `ReflectionEngine` |
| **SQLite** | `time_indexed.db` | `memory/{name}/time_indexed.db` | 对话时间索引 | `TimeIndexedMemory` |
| **NDJSON** | `outbox.ndjson`, `events.ndjson` | `memory/{name}/outbox.ndjson` | 异步操作队列 | `Outbox`, `EventLog` |

### 1.2 记忆模块（`memory/__init__.py:54-59`）

| 模块 | 存储位置 | 职责 |
|---|---|---|
| `CompressedRecentHistoryManager` | `recent.json` | 压缩近几轮对话历史（LLM 驱动压缩） |
| `ImportantSettingsManager` | `settings.json` | 旧版重要设置（已废弃，保留 IO） |
| `TimeIndexedMemory` | `time_indexed.db` (SQLite) | 原始对话记录的时间索引存储 |
| `FactStore` | `facts.json` | 从对话中提取的原子事实（Tier 1） |
| `PersonaManager` | `persona.json` | 长期人格画像（Tier 3），含抑制机制 |
| `ReflectionEngine` | `reflections.json` | 反思综合 + 状态机（Tier 2） |

### 1.3 层次结构（Tier 1-2-3）

```
Tier 1: FactStore (facts.json)
   原子事实，SHA-256 key，含 base64 fp16 向量
   ├─ entity: master / neko / relationship
   └─ 状态: absorbed=true/false

Tier 2: ReflectionEngine (reflections.json)
   反思综合，从多个事实提取
   ├─ 状态机: pending → confirmed → promoted → merged (到 persona)
   │          pending → denied → archived
   └─ evidence 驱动: reinforcement / disputation + 半衰期衰减

Tier 3: PersonaManager (persona.json)
   长期人格画像
   ├─ entity: master (用户) / neko (AI) / relationship (关系)
   ├─ 抑制机制: 5h 内提及 > 2 次则隐藏
   └─ 存档分片: persona_archive/{date}.json
```

### 1.4 关键 memory_server 端点

| 端点 | 位置 | 功能 |
|---|---|---|
| `POST /cache/{name}` | `:48912` | **轻量持久化**：写入 `recent.json` + SQLite + outbox，**不调 LLM** |
| `POST /process/{name}` | `:48912` | **完整处理**：同 /cache + 触发记忆 review |
| `GET /new_dialog/{name}` | `:48912` | **LLM 上下文组装**（核心）：聚合 persona + reflections + recent |
| `POST /query_memory/{name}` | `:48912` | **混合检索**：BM25 + 余弦 + RRF 融合 |
| `POST /reflect/{name}` | `:48912` | 反思合成 + 状态迁移 |

---

## 2. Session 通道 — 通信流程与状态管理

### 2.1 WebSocket 连接

```
前端 → ws://host:48911/ws/{lanlan_name}
  │
  ├─ 1. 验证角色，生成 uuid4 session_id
  ├─ 2. mgr.websocket = websocket (立即绑定)
  └─ 3. 进入 action 分发循环 (永久循环)
       while True:
           data = await websocket.receive_text()
           message = json.loads(data)
           action = message.get("action")
           # 按 action 分发
```

### 2.2 Action 列表

| Action | 触发 | 说明 |
|---|---|---|
| `start_session` | 用户点击"开始对话" | 创建 `OmniOfflineClient` / `OmniRealtimeClient` |
| `stream_data` | 用户输入文字/语音/截图 | 主数据通道 |
| `end_session` | 用户点击"结束" | 销毁 session |
| `pause_session` | 超时/切换 | 标记空闲 |
| `greeting_check` | 初始化时 | 检查是否需要主动搭话 |
| `ping` | 心跳 | 保活 |
| `voice_play_start/end` | 语音播放 | 播放边界信号 |

### 2.3 Session 状态机（`session_state.py`）

```
TurnOwner:     NONE | USER | PROACTIVE
ProactivePhase: IDLE → PHASE1 → PHASE2 → COMMITTING → IDLE
CognitionMode:  REGULAR → FOCUS (凝神) → TRUE_NAME (v2)

SessionEvent: USER_INPUT / PROACTIVE_START / PROACTIVE_DONE / FOCUS_ENTER / FOCUS_EXIT
```

**核心设计**: 事件驱动状态机，O(1) 读路径（热路径无锁），写路径走 `_write_lock`。

### 2.4 Session 生命周期

```
start_session()
  ├─ _starting_session_count += 1 (并发守卫)
  ├─ HTTP GET :48912/new_dialog/{name} (获取记忆上下文)
  ├─ _build_initial_prompt() (组装系统提示)
  ├─ new OmniOfflineClient / OmniRealtimeClient
  └─ CAS: self.session = new_session

[对话轮换]
  ├─ _process_stream_data_internal()
  ├─ session.stream_text() / OmniRealtimeClient 处理
  └─ 追加 HumanMessage / AIMessage 到 _conversation_history

end_session()
  ├─ is_active = False
  ├─ cancel message_handler_task (max 3s)
  ├─ session.close()
  └─ self.session = None

WS 断开 → cleanup() (预期 WebSocket 守卫)
Idle 30min → _idle_session_reset_loop → end_session()
```

---

## 3. API 模型 — 数据结构与接口

### 3.1 WebSocket 消息格式

**客户端 → 服务器**（`app-websocket.js`）:
```json
{"action": "start_session", "input_mode": "text|audio"}
{"action": "stream_data", "data": "文本内容", "input_type": "text"}
{"action": "stream_data", "data": [音频采样], "input_type": "audio"}
{"action": "end_session"}
{"action": "ping"}
{"action": "voice_play_end"}
```

**服务器 → 客户端**:
```json
{"type": "pong"}
{"type": "catgirl_switched", "new_catgirl": "...", "old_catgirl": "..."}
// 流式回复数据（聊天气泡）:
// 通过 send_lanlan_response() 发送 → 前端 appendMessage()
```

### 3.2 前端消息渲染（`app-chat.js`）

```json
用户消息: { id, role: "user", author, time, blocks: [{type:"text", text}], status }
AI 消息:   { id, role: "assistant", author, time, blocks: [{type:"text", text}], status }
```

**渲染模式**:
- 合并消息模式：原地更新单个气泡
- 拟真输出模式：`splitIntoSentences` → 2s 间隔队列
- 结构化富文本：markdown / 代码块渲染

### 3.3 HTTP API 端点（main_server :48911）

| 路由前缀 | 模块 | 说明 |
|---|---|---|
| `/ws/*` | `websocket_router` | WebSocket 主通道 |
| `/api/memory/*` | `memory_router` | 记忆检索/管理 |
| `/api/proactive_chat` | `system_router` | 主动搭话入口 |
| `/api/emotion/analysis` | main_server | 情感分析 |
| `/api/screenshot` | main_server | 截图处理 |
| `/api/system/status` | main_server | 系统状态 |
| `/api/config/*` | `config_router` | 配置读写 |
| `/api/characters/*` | `characters_router` | 角色管理 |

---

## 4. 主动搭话 — 触发条件与消息推送

### 4.1 前端调度（`app-proactive.js`）

```
Leader 选举 (BroadcastChannel, TTL=15s)
  └─ rank: Pet(0) > chat(1) > 其他(99)

scheduleProactiveChat() — 递归 setTimeout
  ├─ canTriggerProactively() 门控:
  │   新手教程 / 破冰期 / goodbye / 游戏路由 / 功能关闭 → 跳过
  │
  ├─ 语音模式: 固定间隔 baseInterval
  │
  └─ 文本模式: 三段式退避
       Tier 1: base × 1.09167^level (每次必升)
       Tier 2: 9% 概率升级，收敛目标 120s
       Tier 3: 确定升级至硬顶 3600s
       └─ 额外: 输入放缓 + 启动延迟 6s

触发时:
  ├─ 收集启用模式 (vision/window/news/video/personal/music/meme)
  ├─ 并行获取截图 + 窗口标题 + 微博热搜
  └─ HTTP POST /api/proactive_chat
```

### 4.2 后端处理（`system_router.py:5238`）

```
POST /api/proactive_chat
  │
  ├─ 1. 验证: 角色存在 / goodbye / 游戏路由
  ├─ 2. try_start_proactive() 原子抢占 SM (IDLE→PHASE1)
  ├─ 3. Activity snapshot (状态/倾向/语气)
  │
  ├─ 4. Phase 0: 并行信息源收集 (asyncio.gather)
  │    截图 → 窗口标题 → 新闻 → B站推荐
  │
  ├─ 5. Phase 1: LLM 主题筛选
  │      prompt = 记忆 + 信息源 + 角色 + 指令
  │      输出: [PASS] 跳过 | 选择的话题内容
  │
  ├─ 6. Phase 2: LLM 流式生成搭话文本
  │      独立 prompt (不含 _conversation_history)
  │      astream → 逐 chunk → TTS / WS 推送
  │
  ├─ 7. finish_proactive_delivery()
  │      ├─ send_lanlan_response() → 前端气泡
  │      ├─ append AIMessage → _conversation_history
  │      └─ anti_repeat corpus 记录
  │
  └─ 8. fire(PROACTIVE_DONE) → 状态机复位 IDLE
```

### 4.3 优先级与冲突处理

| 机制 | 说明 |
|---|---|
| **try_start_proactive()** | 原子 check+claim，避免并发 |
| **_preempted 标志** | 用户输入 → 翻转标志 → Phase 2 abort |
| **播放门控** | AI 说话时不释放，`voice_play_end` + 2s 后才放 |
| **inflight 超时** | 释放后 12s 无确认 → 超时释放 |
| **TTL** | cue 排队超 90s → 静默丢弃 |

---

## 5. 回答生成 — 上下文组装与响应流程

### 5.1 上下文组装（`/new_dialog` → `_build_initial_prompt`）

```
GET :48912/new_dialog/{name}
  └─ 返回 PlainTextResponse，层层拼接:

[Persona Header] ← persona.json (渲染为 markdown)
  ├─ pending_reflections (待确认反思)
  ├─ confirmed_reflections (已确认反思)
  └─ persona fact entries

[Inner Thoughts Header] ← 内心活动模板
  └─ 当前时间注入

[Recent History] ← recent.json (压缩)
  └─ 格式: "{speaker} | {content}"

[Chat Gap Notice] (≥30min 无对话时)
[Holiday Context] (节假日时)

结合 _build_initial_prompt() 的:
  SESSION_INIT_PROMPT (基础系统提示)
  + lanlan_prompt (角色人设)
  + agent tasks (如有)
  + ban_topic 指令
  + anti_repeat hint
  
→ 组装为 SystemMessage → 传给 OmniOfflineClient
```

### 5.2 流式响应流程（`omni_offline_client.py:1945`）

```
OmniOfflineClient.stream_text(data)
  │
  ├─ 1. 检查待处理图片 → 切换视觉模型
  ├─ 2. 拼接系统前缀
  ├─ 3. 组装 HumanMessage(content=_user_text)
  ├─ 4. append → _conversation_history
  ├─ 5. LangChain ChatOpenAI.astream(messages)
  │      └─ messages = [SystemMessage, ...全部 _conversation_history...]
  │
  ├─ [流式] 收到 on_chunk(text):
  │      └─ handle_text_data() → TTS 管线 / WS 推送
  │
  ├─ [流式] 收到 on_chunk(tool_call):
  │      └─ 工具执行 → 结果回写 history → 继续流式
  │
  ├─ 6. stream_text 完成
  ├─ 7. handle_text_data() 追加 AIMessage → _conversation_history
  └─ 8. turn end → POST /cache → memory_server 持久化
```

### 5.3 全链路时序

```
用户输入 → WS → main_server
  │
  ├─ websocket_router: stream_data action
  │     → core.py: _process_stream_data_internal()
  │
  ├─ session 检查: 类型不匹配则重建
  │     (text↔audio 切换 → end_session + start_session)
  │
  ├─ session.stream_text(data)
  │     ├─ append HumanMessage → _conversation_history
  │     ├─ ChatOpenAI.astream(all_history)
  │     │  └─ 流式 chunks → handle_text_data callback
  │     │       ├─ send_lanlan_response() → WS → 前端 appendMessage()
  │     │       └─ _enqueue_tts_text_chunk() → TTS 线程 → WS 音频
  │     │
  │     └─ stream_text 完成
  │
  ├─ handle_text_data() 追加 AIMessage → _conversation_history
  │
  └─ turn end → POST /cache/:48912
        ├─ recent.json 更新
        ├─ time_indexed.db 写入
        └─ outbox.ndjson 登记 (异步: 事实提取/反馈检查)
```

---

## 附：关键文件索引

| 文件 | 规模 | 职责 |
|---|---|---|
| `apps/neko/app/main_server.py` | 138KB | 主 HTTP/WS 服务器，路由注册 |
| `apps/neko/app/memory_server.py` | 226KB | 记忆服务器，所有记忆读写端点 |
| `apps/neko/main_logic/core.py` | 10600+ 行 | 对话流程主控，session 管理 |
| `apps/neko/main_logic/session_state.py` | 701 行 | 事件驱动状态机 |
| `apps/neko/main_logic/omni_offline_client.py` | 1600+ 行 | 文本对话客户端，LLM 流式调用 |
| `apps/neko/main_logic/omni_realtime_client.py` | 3200+ 行 | 实时语音对话客户端 |
| `apps/neko/main_routers/websocket_router.py` | 中 | WebSocket 路由和 action 分发 |
| `apps/neko/main_routers/system_router.py` | 大 | 主动搭话路由入口 |
| `apps/neko/main_routers/memory_router.py` | 47KB | 前端记忆 API 路由 |
| `apps/neko/main_logic/proactive_delivery.py` | 424 行 | 主动交付节流/排序 |
| `apps/neko/static/app-proactive.js` | 2000+ 行 | 前端主动搭话调度 |
| `apps/neko/static/app-websocket.js` | 中 | 前端 WebSocket 客户端 |
| `apps/neko/static/app-chat.js` | 中 | ���端聊天渲染 |
| `apps/neko/memory/facts.py` | 72KB | 事实提取与存储 |
| `apps/neko/memory/reflection.py` | 174KB | 反思引擎 |
| `apps/neko/memory/persona.py` | 146KB | 人格管理器 |
| `apps/neko/memory/hybrid_recall.py` | 32KB | BM25 + 余弦混合检索 |
