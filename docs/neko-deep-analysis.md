# N.E.K.O 模块深度分析报告

> **日期**: 2026-07-24
> **范围**: `E:\Ikaros\apps\neko` 全量代码分析
> **版本**: N.E.K.O 0.8.3
> **目的**: 为 Ikaros V5 全量接管 neko 功能提供架构依据

> **⚠️ 2026-07-27 状态校正**: `apps/neko` 保持**独立完整**，**不**迁移/合并到 V5(`core/memory_v5`)。
> 本分析文档作为 neko 架构参考保留；其"迁移到 V5"的改造建议**已作废**,不再执行。
> 相关迁移脚本 `bin/migrate-neko-to-v5.py` 与方案文档 `docs/memory-replacement-plan.md` /
> `docs/memory-server-proxy-plan.md` 已移除。neko 继续使用自有 `memory_server.py` 记忆系统。

---

## 目录

1. [主动搭话机制](#1-主动搭话机制)
2. [会话管理架构](#2-会话管理架构)
3. [Ikaros 智能体接入现状](#3-ikaros-智能体接入现状)
4. [组件与功能盘点](#4-组件与功能盘点)
5. [架构改造可行性评估](#5-架构改造可行性评估)

---

## 1. 主动搭话机制

### 1.1 三层触发体系

```
┌─────────────────────────────────────────────────────────────┐
│                    前端调度层 (app-proactive.js)               │
│  scheduleProactiveChat() → 递归 setTimeout 链                │
│  Leader选举跨窗口去重 (BroadcastChannel, TTL=15s)            │
│  三段式退避 (10s→60min) / 固定间隔 / 语音固定间隔             │
└──────────────────┬──────────────────────────────────────────┘
                   │ HTTP POST /api/proactive_chat
                   ▼
┌─────────────────────────────────────────────────────────────┐
│              服务端路由层 (system_router.py)                  │
│  try_start_proactive() 原子抢占SM (IDLE→PHASE1)             │
│  409并发拒绝 / 播放门 / 游戏路由 guard                       │
│  生成callback → 入队ProactiveDeliveryManager                 │
└──────────────────┬──────────────────────────────────────────┘
                   │ deliver_batch
                   ▼
┌─────────────────────────────────────────────────────────────┐
│              后端核心层 (core.py)                             │
│  trigger_agent_callbacks() → prompt_ephemeral()             │
│  → OmniOfflineClient.astream() → LLM生成                    │
│  TTS管线 → 播报 / WS推送                                    │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 触发条件分类

| 触发源 | 机制 | 位置 | 优先级 |
|--------|------|------|--------|
| **定时器（主要）** | 前端递归 setTimeout → HTTP POST | `app-proactive.js:scheduleProactiveChat()` | 低（受退避算法调节） |
| **事件驱动（语音播放结束）** | playback_end → 后端 pump 释放 batch | `proactive_delivery.py:on_playback_end()` | 中（min_gap 2s 防洪水） |
| **事件驱动（插件 push_message）** | 插件生命周期 → proactive_bridge → 入队 | `plugin/server/messaging/proactive_bridge.py` | 高（priority 0–9） |
| **状态机抢占** | try_start_proactive() 原子 claim | `session_state.py:251` | 入口门控 |
| **后端看门狗** | asyncio.call_later 超时降级 | `core.py:_schedule_proactive_retry()` | 兜底 |

### 1.3 消息流转完整链路

1. **前端定时器到期** → `triggerProactiveChat()` 检查 leader 身份、播放门、游戏路由 → 收集截图/新闻/视频等模式数据
2. **HTTP POST** `/api/proactive_chat` → `system_router.py` 处理，先跑 `can_start_proactive()` → `try_start_proactive()` 原子抢 SM（`NONE→PHASE1`）
3. **prompt_ephemeral()** 组装角色 + 记忆上下文 + 趋势内容 → 调 LLM（云端优先）
4. **LLM 回复**: 返回文本或 `[PASS]`
5. **交付到 ProactiveDeliveryManager**: 按 priority 排序 + coalescing（合并） + 播放门控
6. **deliver_batch** → `trigger_agent_callbacks()` → 文本模式投 OmniOfflineClient，语音模式投 OmniRealtimeClient
7. **TTS 管线** → 语音播报 或 **WS 推送** → 前端渲染

### 1.4 前端定时器调度（app-proactive.js）

#### Leader 选举机制

由于 Pet 主窗口 (`index.html`) 和聊天浮窗 (`chat.html`) 共享 `app-proactive.js`，两者会同时跑 `setTimeout` 调度，导致双倍 LLM 调用。解决方案是跨窗口 Leader 选举：

- **BroadcastChannel** `'neko_proactive_leader'`
- **心跳周期**：5s（`PROACTIVE_LEADER_HEARTBEAT_MS = 5000`）
- **TTL**：15s（`PROACTIVE_LEADER_TTL_MS = 15000`）
- **优先级**：Pet 窗口 rank=0，chat.html rank=1，其它页面 rank=99（不参与）
- 非 leader 每 8s recheck 一次，leader 失联时自动接班

#### 三段式退避算法

文本模式下，长时间无交互时间隔从 10s 逐渐增长到 60min 上限：

- **Tier 1** (level < cap1): `base × M1^level × (1±12%)`，每次必升
- **Tier 2** (cap1 ≤ level < cap2): 收敛值 × M2^(level-cap1) × (1±12%)，9% 概率升
- **Tier 3** (level ≥ cap2): 同上，每次必升，硬顶 60min
- M1=1.09167, M2=1.55, 收敛目标 120s

### 1.5 优先级与冲突处理

- **状态机独占锁**：`try_start_proactive()` 在 `_write_lock` 内完成"检查+占坑"，保证同一时刻只有一路 proactive 进入 PHASE1
- **用户抢占（Preemption）**：用户任何输入（`USER_INPUT` 事件）翻转 `_preempted=True`，proactive 在任意 phase 的 `is_proactive_preempted()` 检查中立即 abort
- **播放门看门狗**：45s 内 `voice_play_end` 未到 → 强制释放播放锁（防止前端断开后永久卡死）
- **inflight 超时**：release 后 12s 内无 `voice_play_start` → 超时释放单航班锁
- **TTL 过期**：cue 在队列中超过 90s → 静默丢弃

### 1.6 状态机模型（session_state.py）

核心是 `SessionStateMachine`，定义了关键枚举和状态转换：

```
TurnOwner: NONE | USER | PROACTIVE
ProactivePhase: IDLE → PHASE1 (fetch+LLM) → PHASE2 (astream→TTS) → COMMITTING → IDLE
CognitionMode: REGULAR → FOCUS (凝神) → TRUE_NAME (v2)
SessionEvent: USER_INPUT / USER_ACTIVITY / PROACTIVE_START / PROACTIVE_CLAIM / PROACTIVE_PHASE2 / PROACTIVE_COMMITTING / PROACTIVE_DONE / FOCUS_ENTER / FOCUS_EXIT
```

Focus 凝神模式使用 leaky accumulator 算法：
- 每轮得分 (score ∈ [0,1]) 进入泄漏积分器
- `new_charge = clamp(charge * retention + score, 0, cap)`
- 超过 enter 阈值 → 进入 FOCUS 模式，LLM thinking-on
- 低于 exit 阈值 → 退出 FOCUS
- 硬上限回合数 (hard_cap_turns) → 强制退出
- 时间维度双衰减（空闲时 vs 激活时衰减速率不同）

---

## 2. 会话管理架构

### 2.1 当前 Session 存储模型

```python
# OmniOfflineClient (main_logic/omni_offline_client.py:705)
self._conversation_history = []  # 纯内存 List[BaseMessage]
# 每轮追加 HumanMessage / AIMessage / SystemMessage

# LLMSessionManager (main_logic/core.py:865)
self.session = None  # 指向 OmniOfflineClient 或 OmniRealtimeClient 实例
self.is_active = False
```

**结论：当前会话完全是 内存态、无持久化。** Session 表现为一个 Python 对象引用 + 一个 `_conversation_history` 列表。

### 2.2 生命周期管理

| 阶段 | 触发 | 行为 |
|------|------|------|
| **创建** | `start_session()` | 初始化 `OmniOfflineClient`，构建 `_conversation_history = [SystemMessage]` |
| **活跃** | 对话轮换 | append HumanMessage/AIMessage，token 计数持续增长 |
| **归档（token 超阈值）** | 每轮结束时检查 `_conversation_history` 总 token >= `SESSION_ARCHIVE_TRIGGER_TOKENS` | 触发 `handle_proactive_complete()`，但**仅指主动搭话收口，不持久化历史** |
| **销毁** | `end_session()` | `_init_renew_status()` → `SM.reset(force=True)` → `_cleanup_pending_session_resources()` → `session=None` |
| **WS 断开** | `WebSocketDisconnect` | 同 `end_session(by_server=True)`，清空所有内存状态 |

### 2.3 前端长连接维护

- WebSocket 主路由 (`main_server.py:2887`): `ws_ping_interval=20.0s`
- 前端 `app-websocket.js` 处理重连逻辑（onclose → 自动重连）
- BroadcastChannel 跨窗口同步（两个前端窗口可以同时连接同一套 session）

### 2.4 独立 Agent Session（brain/agent_session.py）

CUA/Browser Use Agent 使用独立的 `AgentSessionManager`：
- 纯内存 `Dict[str, AgentSession]`
- 10 分钟空闲 TTL → 自动清理
- 简单的 `TaskRecord`（instruction + result_summary + success + timestamp）
- **完全独立于对话 session**，不与聊天的 `_conversation_history` 互通

### 2.5 跳出 Session 模型的可行性分析

**方案：由 Ikaros 建立"自由调度"架构**

当前的核心制约：

```
制约1: _conversation_history 是 OmniOfflineClient.astream() 的入参
制约2: LLM API 调用直接消费 history 列表
制约3: session 与 WS 连接 1:1 绑定
```

要跳出传统 session 模型，需要的改造：

| 组件 | 现状 | 目标态 |
|------|------|--------|
| 会话存储 | 纯内存 List | Ikaros V5 持久化（SQLite FTS5 + ChromaDB） |
| 上下文管理 | 全量 history 注入 | Ikaros 提炼最优提示词（总结+检索+综合） |
| 任务调度 | 无（WS session 即任务） | Ikaros orchestrator 自由调度，子会话 + 可复用标题 |
| 生命周期 | WS 绑定 | Ikaros 记忆系统管理（attach/detach） |

**可行性：技术上完全可行。** Ikaros V5 已有：
- `v5.store` — FTS5 全文检索 + ChromaDB 向量检索
- `v5.entity_graph` — 实体图谱
- `core/memory_v5/data/v5/*.json` — 持久化的自我模型/情感/状态文件（注：路径已随 2026-07-26 重命名更新）
- `v5.orchestrator` — 统一的 agent/companion 双模调度

**路径**：N.E.K.O 退化为纯 UI 层和 TTS 管线，所有对话/记忆/任务调度由 Ikaros V5 接管。

---

## 3. Ikaros 智能体接入现状

### 3.1 已接入范围

**入口**: `main_logic/ikaros_integration.py`（208 行）

```python
IKAROS_NEKO_INTEGRATION=1  # 默认启用 (环境变量)
IKAROS_NEKO_MODE=companion  # 默认模式，可选 agent
```

**现状**：Ikaros V5 已路由到 neko 的对话流程中，但**调用位置很浅**。

### 3.2 集成链路

```python
# core.py 调用路径 (约 10000+ 行文件中的唯一集成点)
_process_stream_data_internal(user_text, ...) → 
    if ikaros_integration.is_enabled():
        ikaros_integration.stream_to_neko(user_text, session_id, session_manager, history)
            → _call_ikaros()  # 在线程池中运行
                → v5.orchestrator.run(user_text, history, session_id, mode)
                    # 成功 → 返回文本
                    # 失败 → fallback to cloud_chat_sync(user_text, history, session_id)
            → _chunk_for_streaming(reply)  # 分 6 字符/块
            → session_manager.send_lanlan_response(chunk, is_first_chunk)  # 逐块推送 WS
```

### 3.3 职责边界

| 维度 | 当前边界 |
|------|----------|
| **何时接管** | 仅用户发送文本消息时（`_process_stream_data_internal`） |
| **何时不接管** | 主动搭话（proactive）不走 V5、语音模式（Realtime API）不走 V5、工具调用不走 V5 |
| **记忆查询** | `get_ikaros_memories()` 暴露 V5 记忆检索给 neko，但 neko 只在 memory_server 的少数路径中使用 |
| **降级策略** | Ikaros 失败 → 回退到 `cloud_chat_sync`（独立的同步聊天） |
| **流式支持** | `stream_to_neko()` 自己分块推送，不经过 LLM 原生流式接口 |

### 3.4 关键问题

**问题 1：Ikaros 只处理用户主动发起的对话，不参与主动搭话。** 所有 proactive chat 的 LLM prompt/pipeline 仍在 neko 自身的 `prompt_ephemeral()` 内部，V5 的 think.py 5 分钟循环完全独立于 neko 的 proactive 调度。

**问题 2：Ikaros response 被当作纯文本块，没有经过 neko 的 tool_calling/agent_event_bus。** 这意味着 Ikaros 的 agent 模式无法感知或使用 neko 的插件系统（智能家居、B站弹幕、备忘录等）。

**问题 3：记忆双轨。** neko 有自己的 `memory_server.py` (226KB) 独立记忆系统（Fact/Persona/Reflection + SQLite + Chroma），Ikaros 也有 V5 记忆系统（`v5.store` + Chroma + EntityGraph）。两者互不连通，存在记忆断层。

**问题 4：V5 `call_llm()` 非流式。** 当前 `v5.reflect.llm_client.call_llm()` 是同步阻塞调用，neko 的流式体验是通过 `_chunk_for_streaming()` 事后分块模拟的，不是真正的 LLM token 流式。

---

## 4. 组件与功能盘点

### 4.1 完整组件清单

#### 前端层

| 组件 | 位置/文件 | 规模 | 功能 | 可否由 Ikaros 接管 |
|------|-----------|------|------|-------------------|
| Vanilla JS 主 UI | `static/app.js` + 子模块 (~30 文件) | 大量 | 桌面 Pet 主窗口 UI、菜单、按钮 | ❌ 保留为 UI 层 |
| 聊天面板 React SPA | `frontend/react-neko-chat/` (~25 文件) | React 18 + Vite | 聊天消息列表、气泡、智能文本块 | ❌ 保留为 UI 层 |
| 插件管理 Vue SPA | `frontend/plugin-manager/` | Vue 3 + Element Plus | 插件安装/管理/配置 UI | ❌ 保留为 UI 层 |
| 主动搭话调度 | `static/app-proactive.js` | 2000+ 行 | 前端定时器、Leader选举、三段式退避 | ✅ 调度逻辑可移入 V5 think.py |
| Avatar 渲染 | `static/live2d-core.js`, `vrm-init.js`, `mmd-manager.js` | ~10 文件 | Live2D/VRM/MMD 多形态 Avatar | ❌ 保留为前端渲染层 |
| WebSocket 客户端 | `static/app-websocket.js` | 中 | WS 长连接维护、重连 | ❌ 保留 |
| 音频客户端 | `static/app-audio-playback.js` | 中 | 音频播放、TTS 音频流接收 | ❌ 保留 |
| 音频捕获 | `static/app-audio-capture.js` | 中 | 麦克风捕获、VAD | ❌ 保留 |

#### 服务端层 — 核心逻辑

| 组件 | 文件 | 规模 | 功能 | 可否由 Ikaros 接管 |
|------|------|------|------|-------------------|
| 对话流程主控 | `main_logic/core.py` | 10000+ 行 | 对话全流程（TTS/LLM/WS/Proactive/Prompt/Tool） | ✅ **核心改造目标** |
| 会话状态机 | `main_logic/session_state.py` | 701 行 | 事件驱动状态机（TurnOwner/Phase/CognitionMode） | ✅ 可简化/吸收到 V5 |
| 主动交付管理 | `main_logic/proactive_delivery.py` | 424 行 | 主动交付节流/排序/合并/播放门控 | ✅ 可在 V5 层重构 |
| Ikaros 集成桥 | `main_logic/ikaros_integration.py` | 208 行 | 现有集成桥（chat/stream/memory） | ✅ **作为改造起点** |
| 离线对话客户端 | `main_logic/omni_offline_client.py` | 1600+ 行 | OpenAI SDK 对话（history/tool/stream） | ✅ 被 V5 orchestrator 取代 |
| 实时语音客户端 | `main_logic/omni_realtime_client.py` | 大 | Realtime API（VAD/中断/回声） | ⚠️ 语音特殊处理 |
| 工具调用引擎 | `main_logic/tool_calling.py` | 大 | 工具注册/执行/结果回写 | ✅ 被 V5 agent 模式取代 |
| Agent 事件总线 | `main_logic/agent_event_bus.py` | 中 | 事件发布（dispatch_text_user_message 等） | ✅ 被 V5 内部调度取代 |
| 生命周期总线 | `main_logic/lifecycle_bus.py` | 中 | 会话生命周期事件 | ✅ 可简化 |
| core.py 内部的 LLM 调用 | `main_logic/core.py` 中的 `prompt_ephemeral()` | 大 | LLM prompt 组装 + 调用 + 流式处理 | ✅ 被 V5 cloud_chat 取代 |

#### 服务端层 — 三服务器架构

| 组件 | 文件 | 规模 | 功能 | 可否由 Ikaros 接管 |
|------|------|------|------|-------------------|
| 主 HTTP/WS 服务器 | `app/main_server.py` | 138KB | WS 连接管理、TTS 管线、跨进程通信 (ZMQ)、CORS | ❌ 保留（基础设施） |
| Agent 服务器 | `app/agent_server.py` | 285KB | Agent 端推理（屏幕分析/注意力/子话题） | ⚠️ Agent 推理可被 V5 吸收 |
| 记忆服务器 | `app/memory_server.py` | 226KB | 独立记忆系统（Fact/Persona/Reflection/Embedding） | ✅ **与 V5 记忆融合** |
| 监控服务 | `app/monitor.py` | 19.7KB | 运行时健康监控 | ⚠️ 可保留或迁入 V5 |

#### 记忆系统（memory/ — neko 自有）

| 模块 | 功能 | 与 V5 对比 |
|------|------|-----------|
| `memory/temporal.py` | 时态记忆 | V5 `store.py` SQLite FTS5 |
| `memory/reflection.py` | 记忆反思/合成 | V5 `reflect/` 三级提取链 |
| `memory/refine.py` | 记忆精炼 | V5 无直接对应 |
| `memory/facts.py` | 事实提取 | V5 `store.add()` |
| `memory/persona.py` | 人格画像 | V5 `self_model.json` + `profile.py` |
| `memory/embeddings.py` | 嵌入生成 | V5 ChromaDB + nomic-embed-text |
| `memory/hybrid_recall.py` | 混合检索 | V5 `search.py` 三路检索 |
| `memory/anti_repeat.py` | 去重 | V5 无直接对应（fact_dedup.py 有类似） |

**核心发现**：neko 的记忆系统与 Ikaros V5 的记忆系统在功能上高度重叠。neko 另起了一套完全独立的记忆系统，存在大量重复建设。

#### 基础设施与配置

| 组件 | 文件/目录 | 功能 | 可否由 Ikaros 接管 |
|------|-----------|------|-------------------|
| 配置常量 | `config/__init__.py` | APP_VERSION=0.8.3, 所有常量 | ⚠️ 部分可合并 |
| API 提供商配置 | `config/api_providers.json` | LLM/TTS/Embedding 提供商列表 | ❌ 保留 |
| 角色配置 | `config/characters/*.json` | 多语言角色定义 | ❌ 保留 |
| 提示词模板 | `config/prompts/*.py` | sys/chara/memory/agent/activity/proactive | ✅ V5 cloud_chat 可引用 |
| 插件系统 | `plugin/` 全套 | 插件注册/SDK/HTTP/WS/生命周期 | ⚠️ 可 bridge 到 V5 或保持独立 |
| 已安装插件 | `plugin/plugins/` (12+) | app_launcher/bilibili/claude/minecraft/mijia 等 | ⚠️ bridge 保留 |
| LLM 客户端 | `utils/llm_client.py` | HTTP LLM 调用封装 | ✅ 被 V5 llm_client 取代 |
| 工具路由 | `main_routers/tool_router.py` | 工具调用端点 | 取决于插件体系 |
| 系统路由 | `main_routers/system_router.py` | 主动搭话入口 | ⚠️ 保留 API 端点，实现迁后端 |

---

## 5. 架构改造可行性评估

### 5.1 总体评价：可行，需要分阶段实施

| 维度 | 评分 | 说明 |
|------|------|------|
| **复杂度** | ⭐⭐⭐⭐ (高) | 涉及 10000+ 行 core.py + 226KB memory_server 的重构 |
| **风险等级** | ⭐⭐⭐ (中高) | 语音管线和插件生态是最大风险 |
| **预期收益** | ⭐⭐⭐⭐⭐ | 消除记忆双轨，实现超长对话周期，Ikaros 真正拥有"灵魂" |

### 5.2 可复用组件清单

以下组件应保留原位，改造后继续使用：

| 组件 | 保留原因 |
|------|----------|
| `app/main_server.py` | WebSocket 长连接管理、TTS 管线编排、跨进程通信 (ZMQ) |
| `app/monitor.py` | 运行时健康监控 |
| `static/` 全部前端文件 | UI/Avatar 渲染层与后端逻辑解耦 |
| `frontend/react-neko-chat/` | React 聊天面板 SPA |
| `frontend/plugin-manager/` | 插件管理 UI |
| `plugin/` 系统 | 成熟插件生态（B站/备忘录/智能家居等） |
| `main_routers/` 路由层 | API 端点保持不变，后端实现迁移 |
| TTS 管线 (`main_logic/tts_client/`) | 完整的多 provider TTS 系统 |
| `main_logic/session_state.py` | 状态机设计可简化后复用 |
| `main_logic/proactive_delivery.py` | 交付节流逻辑可复用 |
| `config/characters/` | 角色定义 i18n |
| `config/prompts/` | 提示词资源可被 V5 引用 |

### 5.3 需新增/改造的模块

> ⚠️ **2026-07-27 作废声明**：自本节以下「5.3 / 5.4 / 5.5」的改造方案**全部作废，不再执行**。
> 决策：`apps/neko` 保持独立完整，不迁移/合并到 V5（`core/memory_v5`）。
> 保留本节仅作为"若未来重新评估合并"的历史参考；当前 neko 与 V5 的边界见文档顶部状态横幅。

| 模块 | 职责 | 改造难度 | 优先级 |
|------|------|----------|--------|
| ~~**V5 对话路由桥**~~ | ~~接收 neko 的所有用户消息，通过 V5 orchestrator 调度并流式返回~~ | ⭐⭐⭐ | ~~P0~~ |
| ~~**V5 流式接口**~~ | ~~当前 V5 `call_llm()` 同步非流式 → 需加 `astream_chunks()` 或 `aiohttp` SSE 流式~~ | ⭐⭐ | ~~P0~~ |
| ~~**V5 主动搭话控制器**~~ | ~~将 neko 的 proactive timer 调度移入 V5 `think.py` 循环~~ | ⭐⭐ | ~~P1~~ |
| ~~**V5 Session 管理器**~~ | ~~Ikaros 端建立统一的 session 抽象（可持久化、可检索、可子会话）~~ | ⭐⭐⭐⭐ | ~~P1~~ |
| ~~**V5 上下文提炼器**~~ | ~~超长对话自动摘要 → 提炼最优 prompt 上下文（替代全量 history 注入）~~ | ⭐⭐⭐⭐ | ~~P2~~ |
| ~~**记忆融合层**~~ | ~~融合 neko memory_server + V5 store/chroma 为统一记忆系统~~ | ⭐⭐⭐⭐⭐ | ~~P2~~ |
| ~~**V5 插件接口**~~ | ~~允许 V5 agent 模式调用 neko 的插件系统~~ | ⭐⭐⭐ | ~~P2~~ |
| ~~**V5 → neko TTS/WS bridge**~~ | ~~V5 生成的文本/语音通过 neko 的 WS/TTS 管道推送到前端~~ | ⭐⭐ | ~~P0~~ |

### 5.4 分阶段改造方案

#### Phase 1：V5 对话接管（低风险，2-4 周）

```
当前: neko core.py → OpenAI SDK → LLM API → 流式返回
改造: neko core.py → V5 orchestrator → V5 llm_client → LLM API → 流式(新) → neko TTS/WS
```

核心改动：

1. **扩展 `ikaros_integration.py`**
   - 增加流式支持（`astream_to_neko()` 使用 `aiohttp`/SSE 接收 LLM 流式）
   - 覆盖 `_process_stream_data_internal()` 全路径，不再只做文本块模拟流式

2. **V5 `call_llm()` 加流式接口**
   - 新增 `call_llm_stream()` 返回 async generator / SSE stream
   - provider="deepseek" 和 provider="local" 都支持

3. **V5 `cloud_chat.build_system_prompt()` 集成 neko 角色配置**
   - 读取 `config/characters/*.json` 注入 V5 的 system prompt
   - 保持角色定义在 neko config 中单源

4. **neko core.py 调用路径精简**
   - `_process_stream_data_internal()` 改为 "V5 优先" 架构
   - 保留 `prompt_ephemeral()` 作为 V5 不可用时的 fallback

#### Phase 2：主动搭话迁移（中风险，2-3 周）

```
当前: 前端 timer → HTTP POST → system_router.py → core.py → LLM
改造: V5 think.py 循环 → V5 orchestrator → bridge → neko TTS/WS 管道
```

核心改动：

1. **V5 `think.py` 扩展 proactive 调度**
   - 5 分钟循环中增加 proactive 决策点（基于情感/精力/关系/时间）
   - 替代前端 scheduleProactiveChat 的决策逻辑
   - 保留前端 passive 模式（仅 UI 展示，不决策）

2. **V5 → neko bridge**
   - V5 生成主动搭话消息 → 通过 ZMQ/HTTP bridge 注入 neko 的 TTS/WS 管道
   - 复用 `ProactiveDeliveryManager` 的交付节流逻辑

3. **前端 app-proactive.js 精简**
   - 去掉决策逻辑（退避/Leader选举/模式选择）
   - 保留"接收并显示主动消息"的 UI 能力
   - 降级为 "passive display" 模式

#### Phase 3：记忆融合（高风险，3-5 周）

```
当前: neko memory_server (独立 SQLite+Chroma) + V5 store (SQLite+Chroma)
改造: 统一到 V5 记忆架构，neko memory_server 降级为 V5 的记忆查询代理
```

核心改动：

1. **记忆写入统一**
   - neko 记忆写入全部迁移到 V5 store
   - 保留 `memory/temporal.py` 和 `memory/event_log.py` 的时态事件日志（V5 无此能力）

2. **neko memory_server.py 降级**
   - 从 226KB 完整服务/数据库降级为 V5 记忆的 HTTP 查询代理
   - API 兼容层：对外接口不变，内部调 V5 store/search

3. **数据迁移**
   - neko memory_server 的 SQLite + Chroma → V5 的 SQLite + Chroma
   - 确保持久化 ID 映射，零丢失

4. **统一 embedding**
   - 全部使用 V5 的 nomic-embed-text (:8587)
   - 去掉 neko memory/embeddings.py 中的重复 embedding 配置

#### Phase 4：超长上下文 + 自由调度（高风险，4-6 周）

```
新模块: Ikaros Session Manager
├─ 会话创建/销毁/检索 (持久化 SQLite, 脱离 WS 绑定)
├─ 子会话 (task session: 标题化, 可复用)
├─ 上下文提炼 (检索 + 摘要 + 权重排序 → 最优 prompt)
└─ 会话与 WS 解耦 (Ikaros 自由调度, neko 仅做渲染管道)
```

核心改动：

1. **Ikaros Session Manager**（新模块）
   - 持久化会话：`session_id` + `title` + `created_at` + `last_active` + `summary` + `turns` + `context_refs`
   - 子会话 API：`create_sub_session(parent_id, title)` → 生成独立 session_id
   - 检索 API：`search_sessions(query)` → 按内容/时间/标题搜索历史会话

2. **上下文提炼引擎**
   - 超长对话时自动触发摘要（调用 LLM 生成会话摘要）
   - 注入 LLM prompt 时：当前轮完整 history + 历史会话摘要 + V5 记忆检索
   - 动态窗口：当 token 超过限额时，自动压缩早期轮次为摘要

3. **WS 解耦**
   - 引入 `SessionTicket` 概念：WS 连接时 attach ticket，断开时 detach
   - Ikaros 可以在没有 WS 连接时继续执行后台任务（定时思考/记忆整合）
   - 用户重新连接时，自动 attach 到最近的活跃 ticket

### 5.5 潜在技术风险

| 风险 | 等级 | 说明 |
|------|------|------|
| **语音模式断流** | 🔴 P0 | 语音模式 (`OmniRealtimeClient`) 有复杂的 VAD/中断/回声逻辑，迁移到 V5 会打断完整的 Realtime API 管线。Realtime API 的 conversation.create + response.create 流程与 neko 的 `agent_event_bus` 紧耦合 |
| **插件生态断裂** | 🔴 P0 | 12+ 个已安装插件直接操作 `core.py` 的 `enqueue_agent_callback`/`trigger_agent_callbacks`，V5 接管后所有插件的交付路径需重接。涉及：B站弹幕/minecraft/备忘录/智能家居等 |
| **记忆数据丢失** | 🔴 P0 | neko memory_server 已有生产数据（Fact/Persona/Reflection 库），融合到 V5 过程中需保证零丢失。Schema 差异可能导致迁移脚本复杂 |
| **TTS 管线耦合** | 🟡 P1 | TTS 线程与 core.py 的 `_process_stream_data_internal` 紧耦合（TTS 请求队列/响应队列/中断信号），V5 流式结果需精确对齐 TTS 管线的中断边界 |
| **Focus 凝神模式丢失** | 🟡 P1 | Focus 模式实现在 `session_state.py` + `core.py` 的 `update_focus()` 中，使用 leaky accumulator 算法。V5 的 metacog / affect 可替代但需重新接线，Focus 的热路径（每 chunk 检查）要求极低延迟 |
| **前端定时器冲突** | 🟡 P1 | 前端 app-proactive.js 的 setTimeout 链与 V5 think.py 的 5 分钟循环可能冲突。过渡期间需要精确的"谁负责调度"边界定义，否则双倍触发 |
| **双模式回退复杂性** | 🟡 P1 | 改造期间 Ikaros 有时不可用（本地 LLM Qwen3-1.7B 崩溃、CUDA 故障），需要健壮的回退到 neko 原生模式。每个 Phase 的改造点都需要配套的 fallback 机制 |
| **并发写 `data/v5/*.json`** | 🟢 P2 | 已知 V5 的多个状态文件（latest_thought/self_model/affect.json）无统一锁保护，多源写入（neko + V5 think 循环 + orchestrator）竞争可能导致状态损坏 |
| **Hestia 替代 QwenPaw** | 🟢 P2 | QwenPaw (:8088) 当前是 Hermes Dashboard 入口，与 neko 集成相关。若改造涉及 QwenPaw 路径，需同步考虑端口/路由变更 |

### 5.6 改造优先路线图

```
Phase 1 ─ 对话接管 ───── 立即（当前条件已基本成熟）
  ├─ 已有: ikaros_integration.py (implements chat + stream_to_neko)
  ├─ 需加: LLM native streaming support in V5
  └─ 需加: neko 全量消息路由到 V5（包括 proactive 和 tool calling）

Phase 2 ─ 主动搭话 ───── 短期
  ├─ V5 think.py extension for proactive scheduling
  ├─ V5 → neko TTS bridge
  └─ Fallback to neko native proactive

Phase 3 ─ 记忆融合 ───── 中期
  ├─ neko memory_server → V5 proxy
  ├─ Data migration (neko SQLite/Chroma → V5)
  └─ Unified embedding service

Phase 4 ─ 超长上下文 ──── 长期
  ├─ Session Manager (persisted, free scheduling)
  ├─ Context distillation engine
  └─ Task sub-sessions with reusable titles
```

---

## 附录 A：关键文件索引

| 文件路径 | 规模 | 职责 |
|----------|------|------|
| `apps/neko/main_logic/core.py` | 10000+ 行 | 对话流程主控（核心改造目标） |
| `apps/neko/main_logic/session_state.py` | 701 行 | 会话状态机 |
| `apps/neko/main_logic/proactive_delivery.py` | 424 行 | 主动交付管理 |
| `apps/neko/main_logic/ikaros_integration.py` | 208 行 | Ikaros 集成桥 |
| `apps/neko/main_logic/omni_offline_client.py` | 1600+ 行 | 离线 LLM 对话客户端 |
| `apps/neko/main_logic/omni_realtime_client.py` | 大 | 实时语音对话客户端 |
| `apps/neko/app/main_server.py` | 138KB | 主 HTTP/WS 服务器 |
| `apps/neko/app/agent_server.py` | 285KB | Agent 推理服务器 |
| `apps/neko/app/memory_server.py` | 226KB | 记忆服务器 |
| `apps/neko/brain/agent_session.py` | 161 行 | Agent 会话管理器 |
| `apps/neko/static/app-proactive.js` | 2000+ 行 | 前端主动搭话调度 |
| `apps/neko/main_routers/system_router.py` | 大 | 主动搭话路由入口 |
| `apps/neko/config/prompts/prompts_proactive.py` | 0.3MB | 主动搭话提示词模板 |

## 附录 B：端口映射

| 端口 | 服务 | 组件路径 |
|------|------|---------|
| :48911 | Neko 主服务 (HTTP/WS) | `apps/neko/app/main_server.py` |
| :48912 | Neko 记忆服务 | `apps/neko/app/memory_server.py` |
| :48915 | Neko Agent 服务 | `apps/neko/app/agent_server.py` |
| :8080 | 本地 LLM (Qwen3-1.7B) | Ikaros memory-watchdog |
| :8587 | Embedding (nomic) | Ikaros memory-watchdog |
| :9119 | Hermes Dashboard | `core/hermes/` |

---

*本报告基于 2026-07-24 代码快照生成。后续架构决策应以此报告为基准进行更新。*

## 附录 C：N.E.K.O ↔ Ikaros V5 接口映射（来自 ikaros-neko-integration.md）

### C.1 对话管线映射

| N.E.K.O 接口 | 方法 | Ikaros 等价实现 | 映射说明 |
|---|---|---|---|
| `POST /api/ikaros/chat` | HTTP | `orchestrator.run(user_text)` | Bridge 代理，加情感+记忆上下文 |
| `WS /ws/ikaros` | WebSocket | `cloud_chat.cloud_chat_sync()` | Bridge 处理 ws↔sync 转换 |
| `POST /api/ikaros/message` | HTTP | `store.store(content, type="conversation")` | 存储消息到 V5 记忆 |

### C.2 情感/Affect 映射

| N.E.K.O 接口 | Ikaros 等价实现 | 映射说明 |
|---|---|---|
| `main_logic/emotion_analysis.py` | `v5/affect.py` (PAD 6D) | Ikaros affect 更丰富 (6D vs N.E.K.O 3D)，以 Ikaros 为主 |
| `GET /api/emotion/analysis` | `affect.json` 读取 | Bridge 读取 affect.json 并返回兼容格式 |

### C.3 记忆系统映射

| N.E.K.O 接口 | Ikaros 等价实现 | 映射说明 |
|---|---|---|
| `memory/recent.py` | `v5/store.py` (short_term) | Ikaros 记忆为 SQLite + ChromaDB + Entity Graph，比 N.E.K.O 的 5D 更丰富 |
| `memory/facts.py` | `v5/store.py` (type="fact") | 直接映射 |
| `memory/persona.py` | `v5/self_model.py` | Ikaros self_model 提供持久身份 |
| `memory/reflection.py` | `v5/metacog.py`, `v5/reflect/` | Ikaros 有更深层元认知反射 |
| `memory/embeddings.py` | `v5/search.py` (ChromaDB) | 两者都用向量嵌入 |
| `GET /api/memory/recent_files` | `store.list_all(limit=N)` | Bridge 查询 V5 记忆 |

### C.4 Agent/工具映射

| N.E.K.O 接口 | Ikaros 等价实现 | 映射说明 |
|---|---|---|
| `brain/task_executor.py` | `v5/task_runner.py` | Ikaros 委托任务给 Hermes |
| `brain/browser_use_adapter.py` | Hermes browser tools | 使用 Hermes 进行浏览器自动化 |
| `brain/computer_use.py` | Ikaros 无对应 | 保留 N.E.K.O CUA 用于计算机控制 |
| `plugin/mcp_adapter/` | `v5/mcp_server.py` (25 工具) | Ikaros MCP server 可注册为 N.E.K.O 插件 |
