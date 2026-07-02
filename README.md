# Ikaros

> **赛博游民数字管家** · 装在 U 盘里 · 插到任何 Windows 电脑就能跑 · **零系统依赖**(自带便携 Python + llama.cpp + Rust bridge)

[![Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/python-3.12.10-3776ab)](https://www.python.org/)
[![llama.cpp](https://img.shields.io/badge/llama.cpp-b9503%2B-blueviolet)](https://github.com/ggml-org/llama.cpp)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

🧠 **项目记忆库**:`AGENTS.md` — 架构 / 决策 / gotcha / 历史
📖 **身体文档**:`data/hermes-agent/ikaros-identity/Ikaros-body.md` — 进程 / 端口 / 数据 / 模块

---

## 🎯 一句话

Ikaros 是一个 **完全自包含** 的 AI Agent 运行环境 —— 拷到 U 盘里,插到任何一台 Windows 电脑上双击 `bin\ikaros-start.bat`,**Hermes Desktop 自动启动**,即可开始对话。云端 LLM(DeepSeek / MiniMax)为主,本地 GGUF 模型备用,记忆系统(Qdrant + embedding + 归约)全链路本地运行。

---

## 🖼️ 架构

```
┌──────────────────────────────────────────────────────────────────┐
│              Hermes Desktop (Electron 主前端)                      │
│              + Ikaros Desktop Pet (系统托盘, cloud chat)           │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│               bridge-rs  (:7860, Rust 主中枢)                     │
│    52 endpoints: chat / voice / neuro / memory / signals / ikaros │
│         axum + tokio, OpenAI-compat /v1/chat/completions          │
└──────────────────────────────────────────────────────────────────┘
              ┌─────────────────┼────────────────────┐
              ▼                 ▼                    ▼
   ┌──────────────────┐ ┌──────────────┐ ┌───────────────────────┐
   │  Qdrant          │ │ nomic-embed  │ │ DeepSeek-R1 (1.5B)    │
   │  (:6333/:6334)   │ │   (:8587)    │ │     (:8589)           │
   │  向量存储 768dim │ │ embedding    │ │ 记忆归约 reduce_to_fact│
   └──────────────────┘ └──────────────┘ └───────────────────────┘
              │                               │
              ▼                               ▼
   ┌──────────────────┐            ┌───────────────────────┐
   │  Cloud LLM       │            │  llama-server (:8080) │
   │  DeepSeek/MiniMax│            │  Phi-4 (disabled)     │
   │  Pet chat 主通道  │            │  本地推理 (备用)       │
   └──────────────────┘            └───────────────────────┘
```

### 核心端口

| Port | 进程 | 用途 | 状态 |
|------|------|------|------|
| **7860** | bridge-rs (Rust) | 主中枢: chat / memory / voice / neuro / 52 endpoints | ✅ 常驻 |
| **6333/6334** | Qdrant 1.14 | 向量存储 (mem0 collection, 768 dim Cosine) | ✅ 常驻 |
| **8587** | llama-server (nomic-embed) | embedding 专用 (768 dim) | ✅ 常驻 |
| **8589** | llama-server (DeepSeek-R1 1.5B) | 记忆归约 (reduce_to_fact) | ✅ 常驻 |
| **8080** | llama-server (Phi-4) | 本地 LLM 推理 | ⚠️ 默认禁用 |

---

## ✨ 特性

- **零系统依赖** — 自带便携 Python 3.12.10(`portable-python/`)、llama.cpp Windows 二进制 + DLL(`runtime/`)、Rust bridge-rs。**不需要系统装 Python / Node / VS / CUDA toolkit**。
- **U 盘即插即用** — 项目根路径由 `bin\hermes-root.py` 自动解析(`E:\` / `F:\` / `G:\` 自适应),写盘符硬编码立刻挂掉。
- **Rust 主中枢 (bridge-rs)** — axum + tokio, 52 endpoints, chat / voice / neuro / memory / signals 全栈。OpenAI-compat `/v1/chat/completions` + SSE。
- **本地记忆系统** — Qdrant 1.14 向量存储 + nomic-embed embedding (768 dim) + DeepSeek-R1 1.5B 记忆归约,全链路本地运行。memory_writer 自动将对话归约为事实存入 Qdrant。
- **云端 LLM 优先** — Pet chat 走 DeepSeek / MiniMax cloud,本地模型备用。fallback 链自动切换。
- **多版本 CUDA 自适应** — `runtime/cuda/{11.8, 12.4, 13.0}/` 按 NVIDIA 驱动版本自动选,525-554 默认 12.4,老卡回退 11.8。
- **模块化服务** — 每个服务是 `modules/<name>/` 自描述包(`module.json` + `start.ps1` + `health.ps1`),`bin\hermes-supervisor.py` 按 `depends_on` 拓扑排序。新增服务 = 新建目录 + 写 `module.json`。
- **Hermes Desktop** — Electron 主前端,完整聊天 / 会话 / 模型管理。便携 userData,不污染宿主系统。
- **Desktop Pet** — Live2D 桌宠,系统托盘驻留,cloud chat,语音气泡联动,右键菜单全功能。
- **CRLF 行尾保护** — `.githooks/pre-commit` 阻止 LF-only `.bat` 提交(cmd.exe 会把路径截断)。
- **隐私优先** — `data/`、`hermes/data/`、`.env`、IDE 状态、运行时缓存全在 `.gitignore`。

---

## 🚀 30 秒上手

### 在你现在的电脑上(已经解压过)

```
1. 双击 bin\ikaros-start.bat
2. Hermes Desktop 自动启动
3. 开始对话
```

### 在一台全新 Windows 电脑上(刚 git clone)

```
1. git clone https://github.com/ArtificialAngels/hermes-agent-portable.git
2. cd "hermes-agent-portable"
3. bin\setup-portable.bat          ← 下载 portable-python + runtime
4. bin\ikaros-start.bat            ← Desktop 自动启动
```

---

## 📦 自带的依赖(`git clone` 之后第一次必须跑 `setup-portable.bat` 下回来)

| 目录 | 大小 | 来源 | 用途 |
|------|------|------|------|
| `portable-python/` | ~230 MB | `setup-portable.bat` 下载 | 嵌入式 Python 3.12.10 + pip 包 |
| `runtime/` | ~700 MB | `setup-portable.bat` 下载 | llama.cpp Windows 二进制 + DLL |
| `runtime/cuda/12.4/` | ~700 MB | `setup-portable.bat` 下载 | CUDA 12.4 运行时(默认) |
| `runtime/cuda/11.8/` | ~400 MB | 按需 | 老 NVIDIA 驱动回退 |

**为什么不直接 git track?** 因为加起来几个 G,git 仓库会爆。`setup-portable.bat` 用 `aria2c` 多线程下载。

---

## 🤖 本地模型

本地模型用于**记忆系统**(embedding + 归约),chat 默认走云端 (DeepSeek / MiniMax)。

| 模型 | 用途 | 端口 | 大小 |
|------|------|------|------|
| nomic-embed-text-v1.5-q4 | embedding (768 dim) | :8587 | 84 MB |
| DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M | 记忆归约 (reduce_to_fact) | :8589 | 1.04 GB |

可选: Phi-4-Mini 3.8B (本地 chat 推理, :8080, 默认禁用)。

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
| 一键启动 | `bin\ikaros-start.bat` |
| 停止全部 | `bin\hermes-stop.bat` 或 `bin\ikaros-sleep.bat` |
| 服务状态 | `bin\hermes-supervisor.bat --status` |
| 启动顺序预览 | `bin\hermes-supervisor.bat --dry-run` |
| 端口契约表 | `bin\hermes-supervisor.bat --ports` |
| 查看模块详情 | `bin\hermes-supervisor.bat --inspect bridge` |
| 重启单个服务 | `bin\hermes-supervisor.bat --restart bridge` |
| 下载便携运行时 | `bin\setup-portable.bat` |
| CRLF 归一化 | `portable-python\python.exe bin\fix-eol.py --all` |
| HERMES_ROOT 路径解析 | `bin\hermes-root.bat resolve` |

---

## 📁 目录速览

```
Ikaros\
├── bin\                  ← 启动器 + 工具脚本
├── bridge-rs\            ← ★ Rust 主中枢 (bridge) 源码
├── modules\              ← 自描述服务 (bridge / env_bootstrap / llm_engine / model_manager)
├── deps\                 ← 环境解析 (hermes-env.bat/ps1)
├── hermes\               ← Python 薄桥接层 (deprecated, Rust 化中)
├── hermes-agent\         ← 上游 NousResearch/hermes-agent (只读)
├── docs\                 ← 用户文档
├── config\               ← hermes.yaml / models.yaml
├── data\                 ← ★ 运行时数据 (全部 git ignored)
├── portable-python\      ← Python 3.12.10 (git ignored)
├── runtime\              ← llama.cpp + DLL + CUDA (git ignored)
├── tests\                ← 集成测试
├── AGENTS.md             ← 项目记忆库 (架构/决策/gotcha/历史)
└── README.md             ← 你在这里
```

---

## 🐛 故障排查

| 现象 | 第一看 |
|------|--------|
| Desktop 无响应 | `bin\hermes-supervisor.bat --status` 查端口 |
| Bridge 不启动 | `data\logs\bridge.err` 查错误日志 |
| 记忆系统异常 | `curl http://localhost:7860/api/memory/stats` |
| 端口被占 | `netstat -ano \| findstr :7860` |
| 云端 API 失败 | 检查 `.env` 的 API Key |
| USB 盘符变了 | `bin\hermes-root.bat resolve` 重新解析 |

---

## 🤝 致谢

本项目是**整合 + 二次开发**,站在以下巨人的肩膀上:

| 上游项目 | 链接 | 用途 | 协议 |
|----------|------|------|------|
| **Hermes Agent** | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | Agent 核心 / CLI | MIT |
| **llama.cpp** | [ggerganov/llama.cpp](https://github.com/ggml-org/llama.cpp) | 本地 LLM 推理 | MIT |
| **Qdrant** | [qdrant/qdrant](https://github.com/qdrant/qdrant) | 向量数据库 | Apache 2.0 |
| **Qwen** | [QwenLM/Qwen](https://github.com/QwenLM/Qwen) | 本地模型 | Apache 2.0 |

`hermes-agent\` 是上游的干净副本,本项目所有二次开发放在 `bridge-rs\` 和 `modules\` 下。

---

## 📊 已下载资源(完整 U 盘用量)

| 资源 | 大小 |
|------|------|
| `portable-python\` | ~230 MB |
| `runtime\` | ~700 MB |
| `runtime\cuda\12.4\` | ~700 MB |
| `data\models\` | 自选(2-30 GB) |
| 全部源码 + 上游 + docs | ~500 MB |
| **合计** | **~3 GB 起步 + 模型** |

---

## 📜 协议

- 本整合包: MIT
- llama.cpp: MIT
- Hermes Agent: MIT
- Qdrant: Apache 2.0
- 模型权重遵循各自许可证(用户自备)

模型权重**不包含在本仓库**。请从 HuggingFace / ModelScope 等渠道下载。

---

## 📈 更新日志

### 2026-07-02 — 架构刷新 (WebUI 移除 + 记忆系统上线)
- 🗑️ **WebUI 完全移除** — hermes_cli gateway (ephemeral port) 取代 webui 套娃。Hermes Desktop (Electron) 成为主前端。
- 🧠 **本地记忆系统** — Qdrant 1.14 + nomic-embed (:8587) + DeepSeek-R1 (:8589) 全链路。memory_writer.rs 自动将对话归约为事实。
- 🔧 **模块清理** — webui / webui_proxy 模块禁用, llm_engine :8080 默认禁用。活跃模块: bridge / env_bootstrap / model_manager。
- 📝 **启动脚本重构** — `ikaros-start.bat` / `hermes-stop.bat` 对齐模块化架构, 云优先。

### 2026-06-16c — Supervisor + Runspace 修复
- 🐛 **PowerShell Runspace 修复** — start.ps1 改用 inherit-stdio, 不再因 Runspace 销毁后崩溃而静默带走子进程。
- 📐 **启动 fail-fast** — supervisor 在启动前调 `hermes-root verify`, USB 盘符换 (E:→F:) 早爆。

### 2026-06-13 — v3 Phase Close-Out
- 🔒 `.gitignore` 全面重写, 隐私清理。
- 📝 `README.md` 重写, `AGENTS.md` 加 §0 phase close-out 章节。

### 2026-06-10 — v2 Phase 11 重构
- 模块化服务架构完成 (`modules/<name>/` 自描述包)。
- `bin\hermes-supervisor.py` 用 Python 替代老的 PowerShell orchestrator。
- `bin\hermes-root.py` 成为路径解析的单一源真理。

---

## 🔗 相关链接

| 项目 | 链接 |
|------|------|
| **本仓库** | https://github.com/ArtificialAngels/hermes-agent-portable |
| Hermes Agent(上游) | https://github.com/NousResearch/hermes-agent |
| llama.cpp | https://github.com/ggml-org/llama.cpp |
| Qdrant | https://github.com/qdrant/qdrant |
| Qwen 模型 | https://huggingface.co/Qwen |
| GGUF 模型索引 | https://huggingface.co/models?library=gguf |

---

*当前架构: bridge-rs (Rust) + Qdrant + nomic-embed + DeepSeek-R1 记忆归约 + Hermes Desktop (Electron)*
*启动: `bin\ikaros-start.bat` · 停止: `bin\hermes-stop.bat`*