# Ikaros 架构分解：核心（Core） + Live2D 接入模组（Live2D Integration Module）

> 2026-07-20 分解。目标：把 Ikaros 拆成「无界面的 AI 核心」与「可插拔的 Live2D 可视化前端」，
> 让核心像 ekko 一样被 hermes-studio 完整调用，且后端（本地 :8080 / DeepSeek）可由 Studio 页面配置。
> 本次为「接口清晰化」：文件位置大体不动，重点是划清边界 + 定义契约 + 配置化后端。

---

## 1. 总览

```
┌──────────────────────────────┐         ┌──────────────────────────────┐
│   ikaros核心 (Core, 无界面)   │         │  live2d接入模组 (前端)        │
│                              │         │                              │
│  orchestrator.agent_loop ────┼──┐      │  Ikaros-Live2D (Tauri 桌宠)  │
│  cloud_chat (回复生成层)     │  │      │  webview + Live2D 模型       │
│  memory watchdog (:8587/:8080)│  │      │                              │
│  V5 预处理工厂               │  │      └──────────────┬───────────────┘
│  config/ikaros-backend.json │  │                     │ WebSocket :7870
└──────────────┬───────────────┘  │      ┌──────────────▼───────────────┐
               │ agent_loop 入口   │      │ bin/ikaros-voice-ws.py        │
               │                   └──────┤  (接入模组桥: 桌宠 ↔ 核心)    │
               │                          └──────────────┬───────────────┘
               │                                         │ cloud_chat
        ┌──────▼───────────────┐                         │
        │ hermes-studio         │◄────────────────────────┘
        │ (ikaros-agent 注册)   │   新对话下拉选 Ikaros → 调 agent_loop
        └──────────────────────┘
```

---

## 2. ikaros核心（Core）

无界面、可被任意前端（Studio / Live2D / CLI）调用的 AI 大脑。

| 组件 | 位置 | 职责 |
|------|------|------|
| **统一入口** | `Ikaros-memory/v5/orchestrator.py` → `agent_loop(user_text, *, session_id='', max_tokens=200)` | 对外唯一调用入口；Studio/Hermes 均经此进入 |
| **回复生成层** | `bin/cloud_chat.py` → `cloud_chat()` | 生成主回复；支持可配置后端（见 §6） |
| **记忆看门狗** | `bin/ikaros-memory-watchdog.py` | 管 `:8587` embedding(nomic) + `:8080` 本地 LLM(qwen3) |
| **V5 预处理工厂** | `Ikaros-memory/v5/` (rhythm/memory_retrieval/summary/profile/emotional_memory + clock_out) | 节奏、记忆检索、摘要、好恶画像、情感增强、收尾 |
| **后端配置** | `config/ikaros-backend.json` | 声明 AI 后端 provider / base_url / api_key / model |

**核心不依赖 Live2D**：`agent_loop` 不 import 任何 Live2D 代码，可完全 headless 运行。

---

## 3. live2d接入模组（Live2D Integration Module）

把核心「可视化」出来的前端，独立可启停。

| 组件 | 位置 | 职责 |
|------|------|------|
| **桌宠** | `Ikaros-Live2D/` (Tauri v2 + webview + Live2D 模型) | 渲染伊卡洛斯形象、气泡、右键菜单 |
| **接入桥** | `bin/ikaros-voice-ws.py` (WebSocket `:7870`) | 桌宠 webview ↔ 核心 的桥；接收语音/文本 → 调 `cloud_chat` → 回传回复 |

**契约**：桌宠 → `ws://127.0.0.1:7870/v1/voice/ws` → `ikaros-voice-ws.py` → `cloud_chat` → 本地/云端。
接入模组只通过标准协议对接核心，**不反向污染核心**。

---

## 4. 边界与解耦（核心设计约束）

1. 核心对外只暴露 `agent_loop` 这一个入口；任何前端都走它。
2. 接入模组是「客户端」，通过 `:7870` 桥或 `agent_loop` 对接，可单独启动/停止/替换（例如换成网页前端而不动核心）。
3. Studio 直接调用核心 `agent_loop`（经 `v5-agent/manager.ts` 拉起 Python 子进程），**不经 Live2D**。
4. 任一端故障不影响另一端：看门狗/本地 LLM 挂了，桌宠仍可显示离线态；Studio 仍可独立对话。

---

## 5. hermes-studio 集成（仿 ekko）

Ikaros 以 `ikaros-agent` 身份注册进 Studio，完全对齐 ekko 的接线模式：

- **客户端**：新对话下拉含「Ikaros」（始终显示，非仅 DEV）；会话列表/空态/图标 `public/coding-agents/ikaros-agent.png`；i18n `ikarosAgent`。
- **服务端**：`run-chat/index.ts` 的 `isV5AgentExecution()` 命中后分发到 `handle-v5-agent-run.ts` → `getV5AgentManager().run()` → 拉起 Python 子进程跑 `agent_loop`。
- **设置页**：Studio 设置新增「Ikaros 后端」tab（`IkarosBackendPanel.vue`），可改 AI 后端（见 §6）。

相关文件：
- `packages/server/src/services/v5-agent/manager.ts`（拉起 Python + 注入后端 env）
- `packages/server/src/services/hermes/run-chat/handle-v5-agent-run.ts`
- `packages/server/src/routes/ikaros-backend.ts` + `controllers/ikaros-backend.ts`（`GET/PUT /api/ikaros/backend`）
- `packages/client/src/components/hermes/settings/IkarosBackendPanel.vue`

---

## 6. AI 后端配置契约

`config/ikaros-backend.json`：

```json
{
  "provider": "local",
  "local":    { "base_url": "http://127.0.0.1:8080/v1", "model": "local-llm" },
  "deepseek": { "base_url": "https://api.deepseek.com/v1", "api_key": "", "model": "deepseek-chat" }
}
```

- `provider=local`：直连本地 `:8080`，**永不碰云端**（解决 DeepSeek 欠费 429）。
- `provider=deepseek`：直连 OpenAI 兼容接口（可配 base_url/key/model，不限 DeepSeek）。
- `provider=dashboard`（默认/legacy）：走原 Dashboard WebSocket → DeepSeek，失败再本地兜底。

**注入链路**：Studio 页面 `PUT /api/ikaros/backend` 写文件 → `manager.ts` 读文件 → 注入子进程 env
`IKAROS_BACKEND_PROVIDER/BASE_URL/API_KEY/MODEL` → `cloud_chat` 据此选择后端。
文件缺失/损坏时退回 `dashboard`（保持旧行为，零回归）。

---

## 7. 验证

- 类型检查：`packages/server` 的 `tsc --noEmit` + `packages/client` 的 `vue-tsc -b` 均 0 error。
- 运行时：`cloud_chat` 设 `IKAROS_BACKEND_PROVIDER=local` 时直接走本地 `:8080`，不触发 DeepSeek。
- Studio：重启后设置页出现「Ikaros 后端」；改 provider 后发起对话即生效（无需重启看门狗）。
