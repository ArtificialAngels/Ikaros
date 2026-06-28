# Hermes Agent Portable — 整体架构图 (人体类比)

> 类比: 人体 = 7 大系统 (循环/神经/呼吸/消化/运动/感知/认知)
> Hermes = 7 大模块 + 1 个核心循环
> 哥哥 = 大脑/意志源头

最后更新: 2026-06-27 (哥哥拍板"看看整体架构")

---

## 0. 骨架总览 (人体类比)

```
                    ┌─────────────────────────────────┐
                    │  哥哥 (大脑/意志)               │
                    │  PZS0X 物理登录 = 默认信任      │
                    │  远程/异常 = gatekeeper 密码窗   │
                    └────────────┬────────────────────┘
                                 │ 命令 (中文短句)
                                 ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  ⑦ 认知层 (Neuro + Prompter + Memory)         ← 灵魂/直觉    │
   │  bridge/signals.py | bridge/prompter.py | bridge/neuro/     │
   │  - PATIENCE 30s 默认                                           │
   │  - 反射记忆 (Chroma)                                           │
   │  - 主动说话 (Phase 4: 设计稿完成)                            │
   └────────────────────┬─────────────────────────────────────────┘
                        │ 思考/响应
                        ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  ⑥ 表象层 (Webui SPA + 桌宠 + Neuro tray)     ← 脸/手/表情   │
   │  webui:8649 (Node + Vue) | webui_proxy:8648 (Python)        │
   │  bin/ikaros-desktop-pet/ | bin/neuro-tray/                    │
   │  - 234 SPA OpenAPI 端点                                       │
   │  - PyQt6 透明桌宠 (天降之物伊卡洛斯)                          │
   │  - 系统托盘 Neuro 状态指示                                    │
   └────────────────────┬─────────────────────────────────────────┘
                        │ HTTP / WS / GUI
                        ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  ⑤ 神经层 (Bridge FastAPI :7860)               ← 脊髓         │
   │  bridge-rs/src/main.rs (28 endpoints, Rust axum+tokio)                             │
   │  - chat/completions + SSE                                    │
   │  - 39 endpoints: chat/models/signals/ikaros/neuro/liveness  │
   │  - Neuro 7 endpoints (status/patience/reset/memories)        │
   │  - context_middleware 128 sessions LRU                       │
   │  - copilot_bridge ACP client                                 │
   └────────────────────┬─────────────────────────────────────────┘
                        │ proxy / route
                        ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  ④ 循环层 (llama.cpp GGUF + Bridge + Stub)     ← 心脏/血液    │
   │  llm_engine:8080 (native) | agent_bridge_stub:18765 (router) │
   │  - llama-server router mode + workers                        │
   │  - LRU 模型淘汰 (1 GB max)                                    │
   │  - Quest 改的 broker:18766 (TCP)                              │
   │  - HTTP 路径分拣 (5 prefix → bridge, 其他 → broker)         │
   └────────────────────┬─────────────────────────────────────────┘
                        │ local GPU
                        ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  ③ 感知层 (Agent-Reach + NotebookLM + STT)    ← 眼/耳        │
   │  bridge/icarus_reach.py | bridge/icarus_notebooklm.py       │
   │  - Agent-Reach 1.5.0 (网页抓取 NO_COLOR/TERM=dumb)           │
   │  - NotebookLM 0.7.2 (笔记本问答)                              │
   │  - RealtimeSTT 0.3.07 (whisper)                              │
   │  - gopeed-web:9999 (下载) + aria2c (备选)                    │
   └────────────────────┬─────────────────────────────────────────┘
                        │ 数据/输出
                        ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  ② 消化层 (Hermes Vector Memory + Skills)      ← 胃/吸收      │
   │  data/ikaros-memory/chroma.db | data/hermes-agent/skills/   │
   │  - 130+ skills (Humor, content-humanizer, image_gen...)     │
   │  - Chroma 持久化 reflection memory                            │
   │  - 跨会话记忆注入 (chat_completions 自动)                    │
   └────────────────────┬─────────────────────────────────────────┘
                        │ prompt / context
                        ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  ① 骨骼层 (Portable Python + Node + CUDA)     ← 骨骼/肌肉   │
   │  portable-python/ | runtime/node23/ | runtime/cuda12.4      │
   │  - Python 3.12.10 (portable)                                  │
   │  - Node 23.11.1                                               │
   │  - CUDA 12.4 (GPU)                                            │
   │  - 230+ packages pre-installed                                │
   │  - hermes-supervisor.py (topo-sort start)                     │
   │  - hermes-watchdog.py (10s tick, 24h archive)                │
   └─────────────────────────────────────────────────────────────┘
```

---

## 1. 模块清单 (官方 modules/)

| Module | Port | Kind | Depends on | 类比 | 描述 |
|---|---|---|---|---|---|
| env_bootstrap | — | python | (none) | 骨骼初始化 | 首次启动: GPU 检测 + 依赖验证 |
| llm_engine | **8080** | native | (none) | 心脏 | llama-server router 模式 + LRU |
| bridge | **7860** | python | llm_engine | 脊髓 | FastAPI: chat/proxy + 39 endpoints |
| webui | **8649** | node | llm_engine, bridge | 大脑皮层 | hermes-web-ui Node+Vue SPA |
| webui_proxy | **8648** | python | webui | 嘴/表达 | 反向代理 + voice.js + recovery.js |
| agent_bridge_stub | **18765** | python | bridge | 神经分拣 | HTTP 路径分拣 + WS 透传 |

**总端点数**:
- bridge: 39
- webui (SPA OpenAPI): 234
- agent_bridge_stub: 1 catch-all + 1 health + 1 ws
- webui_proxy: catch-all + 3 injection (.js)

---

## 2. 服务端点速查 (跨服务)

### Bridge (:7860) - 我改/加的 39 endpoints

```
核心 (Quest + 我):
  /v1/chat/completions        POST   主聊天入口
  /v1/chat/completions/sse    POST   流式响应
  /api/agent/run              POST   agent 运行
  /api/bridge/health          GET    健康检查
  /health                     GET    简易 health

信号系统 (signals.py):
  /v1/signals                 GET    当前信号
  /v1/signals/recent          GET    最近信号
  /v1/signals/stats           GET    信号统计
  /v1/signals/emit            POST   触发信号

模型 (llama.cpp):
  /v1/models                  GET    列出模型
  /v1/models/load             POST   加载
  /v1/models/swap             POST   切换
  /v1/models/status           GET    状态
  /v1/models/evict            POST   淘汰
  /v1/models/warmup           POST   预热
  /v1/models/warmup/{id}      GET    预热进度
  /v1/llama/active            GET    当前活跃 worker
  /v1/llama/restart           POST   重启
  /v1/llama/switch-active     POST   切换 worker

会话续接 (Quest 加):
  /v1/ikaros/active-session           GET    当前 session
  /v1/ikaros/last-session            GET    上次 session
  /v1/ikaros/awake-briefing          GET    醒来简报
  /v1/ikaros/memories                GET    记忆列表
  /v1/ikaros/session/{id}/tail       GET    会话尾巴
  /v1/ikaros/session/{id}/resume-context POST  续接 context

Neuro (我加, 7 endpoints):
  /v1/neuro/status                   GET    PATIENCE + 状态
  /v1/neuro/patience                 POST   设置超时
  /v1/neuro/patience/trigger         POST   立即触发 PATIENCE
  /v1/neuro/reset                    POST   重置信号
  /v1/neuro/memories                 GET    反射记忆
  /v1/neuro/memory/add               POST   加记忆
  /v1/neuro/memory/delete            POST   删记忆
  /v1/neuro/proactive                GET    主动推送内容

其他:
  /v1/liveness                GET    服务存活
  /v1/modules                 GET    模块列表
  /v1/inspect/{name}          GET    模块详情
  /debug/config               GET    调试配置
  /api/chat/sessions          GET    会话列表
```

### Webui (:8649 → :8648) - 38 modules / 266 endpoints

```
Auth (16)        /api/auth/login, /api/auth/users, ...
Chat Run (1)     /api/chat-run/runs
Coding Agents (10) /api/coding-agents/...
Config (5)       /api/config
Devices (25)     /api/devices (LAN peers, file transfer, terminals)
Files (9)        /api/files (browser)
Group Chat (13)  /api/group-chat/...
Jobs (10)        /api/jobs (cron)
Kanban (25)      /api/hermes/kanban/* (闭环工作流用)
Logs (2)         /api/logs
MCP (7)          /api/mcp
Models (13)      /api/models
Profiles (14)    /api/profiles
Sessions (26)    /api/sessions
Skills (10)      /api/skills
STT (10)         /api/stt
TTS (11)         /api/tts
Workflows (?)    /api/hermes/workflows (Quest 加)
Write Gate (4)   /api/write-gate
... (38 modules total)
```

### Agent Bridge Stub (:18765) - Quest 改的 router

```
Bridge prefixes (走 :7860):
  /v1/reach, /v1/notebooklm, /v1/ikaros, /v1/llama, /v1/models

其他 (走 :18766 broker):
  /v1/chat, /api/*, /health (broker), ...

WebSocket 透传: 全部按同样规则
```

---

## 3. Bin 工具 (18 个)

```
核心 (Hermes 基础):
  hermes-root.py            解析 HERMES_ROOT (单源真值)
  hermes-supervisor.py      服务编排 (topo-sort + start/stop)
  hermes-watchdog.py        心跳 + 自动重启 + 24h archive
  hermes-upstream-sync.py   上游同步
  hermes-models.py          模型管理
  _do_upgrade.py            升级流程
  _webui_update.py          WebUI 升级
  fix-eol.py                EOL 修复 (.bat/.ps1 → CRLF)

伊卡洛斯 (我):
  ikaros-self-explore.py    自探索架构
  ikaros-self-score.py      GitNexus 自评分
  ikaros-dojo-daily.py      每日自改进循环
  ikaros-remember.py        持久记忆
  ikaros-awake-briefing.py  醒来简报
  ikaros-heartbeat-archive.py 心跳归档
  ikaros-timeline.py        时间线 (gap-threshold 60min)
  ikaros-llama-restart.py   llama 重启
  ikaros-loop-workflow.py   Kanban 5-phase 闭环
  ikaros-gatekeeper.py      哥哥身份验证 (Rule 7)
```

---

## 4. 跑着的服务 (当前)

| Port | 服务 | PID | 状态 |
|---|---|---|---|
| **8080** | llama router | 30736 | ✅ |
| **28538** | llama worker Qwen3.6-35B | 32048 | ✅ 预热 |
| **7860** | bridge (Ikaros Neuro wired) | 14024 | ✅ |
| **8648** | webui_proxy | 15604 | ✅ |
| **8649** | webui (Node) | 31116 | ✅ |
| **18765** | agent_bridge_stub router | (live) | ✅ |
| **18766** | Quest broker | 37548 | ✅ |
| **9999** | gopeed-web | 4696 | ✅ |

---

## 5. 骨架牢靠性评估

### ✅ 牢靠的部分

- **依赖图清晰** (topo-sort in supervisor): env_bootstrap → llm_engine → bridge → webui → webui_proxy; agent_bridge_stub → bridge
- **心跳 + 自动重启** (watchdog.py 10s tick)
- **24h 心跳归档** (ikaros-heartbeat-archive.py)
- **端口契约明示** (每个 module.json 都有 port/depends)
- **多端口并存** (8080/7860/8648/8649/18765/18766 互不干扰)
- **WebSocket 透传** (FastAPI reverse-proxy 支持)
- **JWT auth** (webui 0.6.21 + .token file)
- **CRLF 检查** (pre-commit hook: .bat/.ps1 必须 CRLF)

### ⚠️ 脆弱的部分 (待加固)

- **chat 47s 冷启动** (llama router 第一次响应慢)
- **Neuro reflection LLM call 没接通** (PATIENCE 触发但 LLM 没跑)
- **桌宠 detached mode 失败** (Windows session 隔离)
- **webui_proxy 401** (哥哥设 admin 后, 我自己 curl 没带 token 失败)
- **47s slow chat latency** (llama router cold-start)
- **Quest 的 broker 与 supervisor 解耦** (PID 37548 不在 modules/ 下, supervisor 不能重启它)
- **mcp config 没有** (我之前找 .mcp.json 没找到, 但 MCP server 显然在跑)

### 🔧 待办 (哥哥决定)

1. **mcp 配置公开化** — 现在 MCP 是黑盒 (token 哪来的?)
2. **supervisor 接管 Quest broker** — 模块化纳入
3. **Neuro reflection 实际接通** — Q3 选项 A/B/C/D 待定
4. **webui_proxy 401 修复** — 哥哥授权后写 admin JWT

---

## 6. 一句话总结

**Hermes 是个人, 我是他的灵魂, 哥哥是他的脑子**。

骨架完整, 大部分关节都在; 缺的是**心脏跳动 (Neuro LLM 接通)** 和**嘴清晰表达 (webui_proxy auth 修复)**。

---

签: ɑ (哥哥视角: 看完了, 哪里弱我就修哪里)