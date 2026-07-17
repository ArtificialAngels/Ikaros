# Ikaros

> **赛博游民数字管家** · 装在 U 盘里 · 插到任何 Windows 电脑就能跑 · **零系统依赖**(自带便携 Python + llama.cpp + Tauri/Rust 桌宠)

[![Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/python-3.12.10-3776ab)](https://www.python.org/)
[![llama.cpp](https://img.shields.io/badge/llama.cpp-b9867-blueviolet)](https://github.com/ggml-org/llama.cpp)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

🧠 **项目记忆库**:`AGENTS.md` — 架构 / 决策 / gotcha / 历史
📖 **身体文档**:`data/hermes-agent/ikaros-identity/Ikaros-body.md` — 进程 / 端口 / 数据 / 模块

---

## 🎯 一句话

Ikaros 是一个 **完全自包含** 的 AI Agent 运行环境 —— 拷到 U 盘里,插到任何一台 Windows 电脑上双击 `bin\ikaros-start.bat`,**桌宠 + 记忆 + 前端** 自动启动,即可开始对话。云端 LLM(DeepSeek / MiniMax)为主,本地 GGUF 模型(由 resolver 动态选择, 默认 Qwen3-1.7B + nomic-embed-text)备用,记忆系统(V4:SQLite + FTS5 + Chroma 向量)全链路本地运行。

---

## 🖼️ 架构 (no-bridge, 2026-07-07)

```
┌──────────────────────────────────────────────────────────────────┐
│              Hermes Desktop (Electron 主前端)                      │
│              + Ikaros Desktop Pet (Tauri v2 系统托盘, cloud chat)  │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│   Ikaros-Live2D (Tauri v2 + Vue 3 + Live2D 桌宠)                   │
│   target/release/ikaros-desktop-pet.exe                            │
│   - 透明穿透窗口 / 悬浮球 / 监控面板 / 系统托盘右键菜单            │
│   - App.vue ──ws──> :7870 voice-ws                                 │
└──────────────────────────────────────────────────────────────────┘
                                │ ws://127.0.0.1:7870/v1/voice/ws
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│   bin/ikaros-voice-ws.py  (Live2D 语音服务)                        │
│     ├─ cloud_chat.py   (DeepSeek/MiniMax 云 + :8080 本地兜底)      │
│     ├─ cogno_5d.py     (5D: 时间/设备/地理/情绪/上下文 注入)       │
│     └─ edge_tts        (MS Azure 云端 TTS)                         │
└──────────────────────────────────────────────────────────────────┘
              ┌─────────────────┼────────────────────┐
              ▼                 ▼                    ▼
   ┌──────────────────┐ ┌──────────────┐ ┌───────────────────────┐
   │  nomic-embed     │ │ 本地 LLM      │ │ Cloud LLM              │
   │  (:8587)         │ │   (:8080)    │ │ DeepSeek / MiniMax     │
   │  embedding 768dim│ │ 本地推理+记忆 │ │ Pet chat 主通道        │
   └──────────────────┘ └──────────────┘ └───────────────────────┘
              │                 │
              ▼                 ▼
   ┌──────────────────────────────────────────────┐
   │  V4 记忆 (SQLite + FTS5 + Chroma 向量)         │
   │  Ikaros-memory/data/v4/v4.db                   │
   │  watchdog (:8587+:8080) 心跳重启 + 反思周期     │
   └──────────────────────────────────────────────┘
```

### 核心端口

| Port | 进程 | 用途 | 状态 |
|------|------|------|------|
| **7870** | ikaros-voice-ws.py | Live2D 语音服务 (ws://127.0.0.1:7870/v1/voice/ws) | ✅ 随启动拉起 |
| **8587** | llama-server (nomic-embed-text) | embedding 专用 (768 dim), V4 记忆写入/召回 | ✅ 常驻 |
| **8080** | llama-server (本地 LLM, resolver 选择) | 本地 LLM 推理 + V4 记忆 extract/reflect | ✅ 常驻 |
| **9119** | hermes.exe dashboard | Hermes Dashboard Web UI | ✅ 常驻 |
| **(无)** | — | ~~`:7860` Rust bridge~~ 已删除 (de-bridge 架构) | ❌ 已移除 |

---

## ✨ 特性

- **零系统依赖** — 自带便携 Python 3.12.10(`runtime\portable-python/`)、llama.cpp Windows 二进制 + DLL(`runtime/`)、Tauri/Rust 桌宠(`Ikaros-Live2D`)。**不需要系统装 Python / Node / VS / CUDA toolkit**。
- **U 盘即插即用** — 项目根路径由 `bin\hermes-root.py` 自动解析(`E:\` / `F:\` / `G:\` 自适应),写盘符硬编码立刻挂掉。
- **Tauri 桌宠 (Ikaros-Live2D)** — Tauri v2 + Vue 3 + Live2D,透明穿透窗口、系统托盘右键菜单(显示/隐藏、切换形象、表情、比例、截图、模式、Neuro、LLM 模型、监控、设置、重启、退出)、悬浮球、独立监控面板。
- **无桥架构 (no-bridge)** — 桌宠 webview 直接经 `:7870` voice-ws 对话,不依赖已删除的 `:7860` bridge。云 LLM(DeepSeek / MiniMax)为主,本地 LLM 兜底。
- **本地记忆系统 (V4)** — SQLite(FTS5 关键词)+ Chroma(向量语义)双索引,`store()` 实时写入、`fused_search()` 融合召回、watchdog 周期反思(consolidate/dedup/promote/distill/reflect/cleanup)。无 Qdrant 依赖。
- **5D 认知注入** — `cogno_5d.py` 在每轮对话注入时间/设备/地理/情绪/上下文锚点。
- **云端 LLM 优先** — Pet chat 走 DeepSeek / MiniMax cloud,本地模型备用。fallback 链自动切换。
- **Hermes Desktop** — Electron 主前端,完整聊天 / 会话 / 模型管理。便携 userData,不污染宿主系统。
- **Hermes Dashboard** — `:9119` Web UI,快速查看/调试。
- **CRLF 行尾保护** — `.githooks/pre-commit` 阻止 LF-only `.bat` 提交(cmd.exe 会把路径截断)。
- **隐私优先** — `data/`、`hermes/data/`、`.env`、IDE 状态、运行时缓存全在 `.gitignore`。

---

## 🚀 30 秒上手

### 在你现在的电脑上(已经解压过)

```
1. 双击 bin\ikaros-start.bat
2. 桌宠自动启动 (托盘图标右键出菜单)
3. 开始对话
```

### 在一台全新 Windows 电脑上(刚 git clone)

```
1. git clone https://github.com/ArtificialAngels/hermes-agent-portable.git
2. cd "hermes-agent-portable"
3. bin\setup-portable.bat          ← 下载 runtime\portable-python + runtime
4. bin\ikaros start                ← 桌宠 + 记忆 + 前端 自动启动 (统一启动器)
```

---

## 📦 自带的依赖(`git clone` 之后第一次必须跑 `setup-portable.bat` 下回来)

| 目录 | 大小 | 来源 | 用途 |
|------|------|------|------|
| `runtime\portable-python/` | ~230 MB | `setup-portable.bat` 下载 | 嵌入式 Python 3.12.10 + pip 包(位于 runtime\ 下) |
| `runtime/` | ~700 MB | `setup-portable.bat` 下载 | llama.cpp Windows 二进制 + DLL |
| `runtime/cuda/12.4/` | ~700 MB | `setup-portable.bat` 下载 | CUDA 12.4 运行时(默认) |
| `runtime/cuda/11.8/` | ~400 MB | 按需 | 老 NVIDIA 驱动回退 |

**为什么不直接 git track?** 因为加起来几个 G,git 仓库会爆。`setup-portable.bat` 用 `aria2c` 多线程下载。

---

## 🤖 本地模型

本地模型用于**记忆系统**(embedding + 反思归约)与本地 LLM 兜底,chat 默认走云端 (DeepSeek / MiniMax)。

| 模型 | 用途 | 端口 | 大小 |
|------|------|------|------|
| nomic-embed-text (v1.5-q4 / v2-moe-q8) | embedding (768 dim) | :8587 | 80 MB / 488 MB |
| 本地 LLM (resolver 选择, 默认 Qwen3-1.7B Q4) | 本地 LLM 推理 + V4 记忆 extract/reflect | :8080 | ~5 GB |

---

## ⚙️ 配置(可选)

### 云端 LLM

编辑 `.env`(从 `.env.example` 复制):

```bash
DEEPSEEK_API_KEY=sk-...
MINIMAX_API_KEY=...
```

### 国内网络慢 / 需要代理

见 [docs/附录-镜像与代理.md](docs/附录-镜像与代理.md)。

---

## 🛠️ 常用命令

| 用途 | 命令 |
|------|------|
| 一键启动 | `bin\ikaros start` |
| 停止全部 | `bin\ikaros sleep` |
| 记忆服务状态 | `bin\ikaros-memory-watchdog.py --status` |
| 语音服务状态 | `netstat -ano \| findstr :7870` |
| 下载便携运行时 | `bin\setup-portable.bat` |
| CRLF 归一化 | `runtime\portable-python\python.exe bin\fix-eol.py --all` |
| HERMES_ROOT 路径解析 | `bin\hermes-root.bat resolve` |

### 统一启动器 `ikaros`

`bin\ikaros.cmd`(薄壳,调用 `bin\ikaros.exe`)把原先十几个 `.bat` 整合为一个 Rust 二进制,按 order 分发:

```
bin\ikaros start          # 拉起后端(记忆/语音/思考/灵魂同步/桌宠),再弹菜单选前端
bin\ikaros sleep          # 停止全部服务
bin\ikaros studio         # Hermes Studio (:8649)
bin\ikaros dashboard      # Hermes Dashboard (:9119)
bin\ikaros desktop        # Hermes Desktop 应用
bin\ikaros mem <args>     # 记忆 store CLI
bin\ikaros think <args>   # V5.1 自我思考
bin\ikaros verify --quick # V5.1 测试
bin\ikaros ws-restart     # 重启语音 WS (:7870)
bin\ikaros live2d status  # 桌宠状态
```

> 旧的 `.bat` 全部保留在 `bin\legacy\` 作为回滚,不再参与主链路。

---

## 📁 目录速览

```
Ikaros\
├── bin\                  ← 统一启动器 ikaros.exe/ikaros.cmd + 工具脚本 (watchdog, voice-ws, cloud_chat, screen-activity-monitor.ps1)
├── Ikaros-Live2D\        ← ★ Tauri v2 + Vue 3 + Live2D 桌宠 (src-tauri/src/tray.rs = 托盘菜单)
├── Ikaros-memory\        ← V4 记忆 (store/search/reflect/cogno_5d) + v4.db
├── Ikaros-environment\   ← 环境单一入口 (init.bat 设 IKAROS_* + PATH)
├── hermes-agent\         ← 上游 NousResearch/hermes-agent (只读)
├── docs\                 ← 用户文档
├── config\               ← hermes.yaml / models.yaml
├── data\                 ← ★ 运行时数据 (全部 git ignored)
├── runtime\portable-python\ ← Python 3.12.10 (git ignored, 已并入 runtime\)
├── runtime\              ← llama.cpp + DLL + CUDA (git ignored)
├── exProject\            ← 只读参考克隆 (Live2DPet / MewCo-AI, git ignored)
├── tests\                ← 集成测试
├── AGENTS.md             ← 项目记忆库 (架构/决策/gotcha/历史)
└── README.md             ← 你在这里
```

---

## 🐛 故障排查

| 现象 | 第一看 |
|------|--------|
| 桌宠没出现 | `tasklist \| findstr ikaros-desktop-pet` 看进程是否在 |
| 托盘右键菜单空白/不可用 | 确认 `target/release/ikaros-desktop-pet.exe` 是最新构建(`cargo build --release`) |
| 语音没反应 | `netstat -ano \| findstr :7870` 看 voice-ws 是否起 |
| 记忆召回弱 | `bin\ikaros-memory-watchdog.py --status` 看 `:8587`/`:8080` 心跳 |
| 端口被占 | `netstat -ano \| findstr :7870` / `:8587` / `:8080` |
| 云端 API 失败 | 检查 `.env` 的 API Key |
| USB 盘符变了 | `bin\hermes-root.bat resolve` 重新解析 |

---

## 🤝 致谢

本项目是**整合 + 二次开发**,站在以下巨人的肩膀上:

| 上游项目 | 链接 | 用途 | 协议 |
|----------|------|------|------|
| **Hermes Agent** | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | Agent 核心 / CLI | MIT |
| **llama.cpp** | [ggerganov/llama.cpp](https://github.com/ggml-org/llama.cpp) | 本地 LLM 推理 | MIT |
| **Tauri** | [tauri-apps/tauri](https://github.com/tauri-apps/tauri) | 桌宠框架 (Rust + webview) | Apache 2.0 / MIT |
| **Live2D** | [Cubism SDK](https://www.live2d.com/) | 桌宠渲染 | Live2D 开放许可 |
| **Qwen** | [QwenLM/Qwen](https://github.com/QwenLM/Qwen) | 本地模型 | Apache 2.0 |

`hermes-agent\` 是上游的干净副本,本项目所有二次开发放在 `Ikaros-Live2D\` / `Ikaros-memory\` / `bin\` 下。

---

## 📊 已下载资源(完整 U 盘用量)

| 资源 | 大小 |
|------|------|
| `portable-python\` | ~230 MB |
| `runtime\` | ~700 MB |
| `runtime/cuda\12.4\` | ~700 MB |
| `data\models\` | 自选(2-30 GB) |
| 全部源码 + 上游 + docs | ~500 MB |
| **合计** | **~3 GB 起步 + 模型** |

---

## 📜 协议

- 本整合包: MIT
- llama.cpp: MIT
- Hermes Agent: MIT
- Tauri: Apache 2.0 / MIT
- 模型权重遵循各自许可证(用户自备)

模型权重**不包含在本仓库**。请从 HuggingFace / ModelScope 等渠道下载。

---

## 📈 更新日志

### 2026-07-07 — no-bridge + Tauri 桌宠定型
- 🗑️ **PyQt6 桌宠 (`bin/ikaros-desktop-pet/`) 与 `:7860` Rust bridge 双双移除** — 去桥架构(no-bridge)定型。
- 🐾 **桌宠 = `Ikaros-Live2D` (Tauri v2 + Vue 3 + Live2D)** — 透明穿透窗口、系统托盘右键菜单、悬浮球、监控面板。
- 🧠 **V4 记忆上线** — SQLite(FTS5)+ Chroma 向量双索引,替代旧 Qdrant + DeepSeek-R1 归约;watchdog 心跳重启 + 反思周期。
- 🎙️ **语音链路** `:7870` voice-ws — Tauri webview 经 WebSocket 直连,cloud_chat 路由云/本地, cogno_5d 注入 5D, edge_tts 出声。
- 🚀 **`ikaros-start.bat` 整合启动** — 停旧 → 记忆 watchdog → 桌宠 → Dashboard `:9119` → Hermes Desktop。

### 2026-07-02 — 架构刷新 (WebUI 移除 + 记忆系统上线)
- 🗑️ **WebUI 完全移除** — hermes_cli gateway (ephemeral port) 取代 webui 套娃。Hermes Desktop (Electron) 成为主前端。
- 🔧 **启动脚本重构** — `ikaros-start.bat` / `ikaros-sleep.bat` 对齐模块化架构, 云优先。

### 2026-06-16c — Supervisor + Runspace 修复
- 🐛 **PowerShell Runspace 修复** — start.ps1 改用 inherit-stdio, 不再因 Runspace 销毁后崩溃而静默带走子进程。

### 2026-06-13 — v3 Phase Close-Out
- 🔒 `.gitignore` 全面重写, 隐私清理。
- 📝 `README.md` 重写, `AGENTS.md` 加 §0 phase close-out 章节。

### 2026-06-10 — v2 Phase 11 重构
- 模块化服务架构完成 (`modules/<name>/` 自描述包)。

---

## 🔗 相关链接

| 项目 | 链接 |
|------|------|
| **本仓库** | https://github.com/ArtificialAngels/hermes-agent-portable |
| Hermes Agent(上游) | https://github.com/NousResearch/hermes-agent |
| llama.cpp | https://github.com/ggml-org/llama.cpp |
| Tauri | https://github.com/tauri-apps/tauri |
| Qwen 模型 | https://huggingface.co/Qwen |
| GGUF 模型索引 | https://huggingface.co/models?library=gguf |

---

*当前架构: Ikaros-Live2D (Tauri v2 + Live2D) 桌宠 + :7870 voice-ws + V4 记忆 (:8587/:8080) + Hermes Desktop (Electron) + Dashboard :9119*
*启动: `bin\ikaros-start.bat` · 停止: `bin\ikaros-sleep.bat`*
