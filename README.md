# Ikaros

> **赛博游民数字管家** · 装在 U 盘里 · 插到任何 Windows 电脑就能跑 · **零系统依赖**(自带便携 Python + llama.cpp + N.E.K.O 桌宠)

[![Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/python-3.12.10-3776ab)](https://www.python.org/)
[![llama.cpp](https://img.shields.io/badge/llama.cpp-b9867-blueviolet)](https://github.com/ggml-org/llama.cpp)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

🧠 **项目记忆库**:`AGENTS.md` — 架构 / 决策 / gotcha / 历史
📦 **上游清单**:`UPSTREAM.md` — 有上游的组件怎么拉、落哪、是否入库
📖 **身体文档**:`data/hermes-agent/ikaros-identity/Ikaros-body.md` — 进程 / 端口 / 数据 / 模块

---

## 🎯 一句话

Ikaros 是一个 **完全自包含** 的 AI Agent 运行环境 —— 拷到 U 盘里,插到任何一台 Windows 电脑上双击 `bin\ikaros-control.bat` 拉起 **控制面板 :9100**,一键启动 **桌宠 + 记忆 + 前端**。云端 LLM(DeepSeek)为主,本地 GGUF 模型(由配置决定,默认 Qwen3-1.7B + nomic-embed-text)**懒加载**备用,记忆系统(V5:SQLite + FTS5 + Chroma 向量)全链路本地运行。

> **仓库是「瘦身版」**:本云端仓库只保留 **Ikaros 原生代码 + 配置 + 上游清单/拉取/配置脚本**。所有「有上游」的组件(N.E.K.O 桌宠、Hermes Agent、runtime 工具链、各类 MCP)**不入库**,统一由 `scripts/fetch-upstreams.py` 拉取、`scripts/setup-native.py` 落地配置。详见 `UPSTREAM.md`。

---

## 🖼️ 架构 (控制面板统一调度, 2026-07-26)

```
                    ┌──── 控制面板 :9100 ────┐
                    │  bin/ikaros-control.bat │
                    │  一键启停全部组件         │
                    └──────────┬─────────────┘
                               │ start
       ┌───────────────────────┼──────────────────────────┐
       ▼                       ▼                           ▼
┌─────────────┐      ┌──────────────────┐      ┌────────────────────┐
│ Memory      │      │ N.E.K.O 前端      │      │ Hermes Dashboard   │
│ :8587 embed │◄────►│ :48911 (React +   │◄────►│ :9119 (云端 LLM     │
│ :8080 LLM*  │      │  Live2D/VRM/MMD)  │      │  后端 / Web UI)     │
└──────┬──────┘      │ + :48912 mem      │      └─────────┬──────────┘
       │             │ + :48915 agent    │                │
       ▼             └────────┬──────────┘                ▼
┌─────────────┐               │                  ┌──────────────────┐
│ V5 数据层   │               ▼
│ v5.db +     │      ┌──────────────────┐
│ ChromaDB    │      │ QwenPaw :8088    │
└─────────────┘      │ (猫爪工具臂)      │
                     └──────────────────┘

* :8080 本地 LLM 为懒加载: 看门狗只监测端口, 模型在 agent 首次调用时热载入。
```

### 核心端口

| Port | 组件 | 用途 | 状态 |
|------|------|------|------|
| **9100** | 控制面板 | `bin/ikaros-control.bat` Web UI 启动器 | ✅ 常驻 |
| **8587** | Memory (nomic-embed) | embedding 768 dim, V5 记忆写入/召回 | ✅ 常驻 |
| **8080** | 本地模型 (Local LLM) | Qwen3-1.7B,**懒加载**(agent 调用时热载入);面板可切换模型 | ⏸ 按需 |
| **48911** | N.E.K.O 前端 | React 聊天 + Live2D/VRM/MMD 多形态 Avatar | ✅ |
| **48912** | N.E.K.O Memory | N.E.K.O 记忆服务 | ✅ |
| **48915** | N.E.K.O Agent | 键鼠/浏览器/OpenClaw 工具臂 | ✅ |
| **9119** | Hermes Dashboard | LLM 后端管理 + Web UI | ✅ |
| **8088** | QwenPaw | 猫爪服务端(Hermes Agent 驱动) | ✅ |

> **已移除**:语音桥 `:7870`/`:7871`(2026-07-24)、独立记忆 sidecar `:9587`(2026-07-26)、自思考循环 `think.py`(2026-07-26)、Hermes API 网关 `:8642`(2026-07-26)、控制面板 Soul Sync 守护与 Persona Sync `v5-sync-persona.py`(2026-07-26,前者由 V5 每轮自同步覆盖,后者不再需要)——均为噪音或冗余,已从架构剔除。

---

## ✨ 特性

- **零系统依赖** — 自带便携 Python 3.12.10(`runtime\portable-python/`)、llama.cpp Windows 二进制 + DLL(`runtime/`)、N.E.K.O 桌宠(`core/neko`,上游)。**不需要系统装 Python / Node / VS / CUDA toolkit**。
- **U 盘即插即用** — 项目根路径由控制面板自动解析(`E:\` / `F:\` / `G:\` 自适应),写盘符硬编码立刻挂掉。
- **N.E.K.O 桌宠** — React 聊天窗 + 多形态 Avatar(Live2D / VRM / MMD)+ 插件系统 + 工具臂(QwenPaw)。Electron 桌面壳一键启动。
- **控制面板统一调度** — `:9100` Web UI 一键启停全部组件,无需记一堆 `.bat`。
- **本地记忆系统 (V5)** — SQLite(FTS5 关键词)+ Chroma(向量语义)+ 时间范围 **三路融合召回**(`min_fused_score` 默认 0.3);`store()` 实时写入、`consolidate/distill/reflect` 经云端 LLM 归约。无 Qdrant 依赖。
- **5D 认知注入** — `cogno_5d.py` 在每轮对话注入时间/设备/地理/情绪/上下文锚点。
- **云端 LLM 优先** — 对话走 DeepSeek cloud;本地 Qwen3-1.7B 仅作兜底,懒加载不占常驻资源。
- **Hermes Dashboard** — `:9119` Web UI,管理 LLM 后端 / 会话 / 模型。
- **CRLF 行尾保护** — `.githooks/pre-commit` 阻止 LF-only `.bat` 提交(cmd.exe 会把路径截断)。
- **隐私优先** — `data/`、`core/hermes/`（上游干净副本）、`core/neko/resources/`、`runtime/`、`.env`、IDE 状态全在 `.gitignore`。

---

## 🚀 30 秒上手

### 在一台全新 Windows 电脑上(刚 git clone)

```
1. git clone https://github.com/ArtificialAngels/Ikaros.git
2. cd Ikaros
3. python scripts/fetch-upstreams.py     ← 拉取上游(neko / hermes-agent / runtime / mcp)
4. python scripts/setup-native.py        ← 落地 paths.json + hermes config
5. bin\ikaros-control.bat                ← 拉起控制面板 :9100, 点 start 启动整栈
```

> `scripts/fetch-upstreams.py` 支持 `--list` / `--dry-run` / 按名拉取;`setup-native.py` 支持 `--check` 校验。详见 `UPSTREAM.md`。

### 在你现在的电脑上(已经解压过)

```
1. 双击 bin\ikaros-control.bat
2. 浏览器开 http://127.0.0.1:9100, 点 start
3. 桌宠 / 前端自动启动, 开始对话
```

---

## 📦 上游组件(不入库, 拉取而来)

| 目录 | 上游 | 是否入库 |
|------|------|---------|
| `core/neko/` | [Project-N-E-K-O/N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) | 否(`.gitignore`) |
| `hermes-agent/` | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | 否 |
| `runtime/` | llama.cpp + CUDA 工具链 | 否 |
| `data/hermes-agent/skills/...` (MCP) | 各类 MCP 服务器 | 否 |

完整清单、URL、落点、获取方式见 **`UPSTREAM.md`**。

**为什么不直接 git track?** 上游加起来几十个 G(模型权重 + node_modules + venv),git 仓库会爆。拉取脚本用 gopeed/aria2 + 镜像多线程下载,且 `.gitignore` 已排除这些目录。

---

## 🤖 本地模型

本地模型用于 **记忆系统**(embedding + 反思归约兜底)与本地 LLM 兜底,chat 默认走云端 (DeepSeek)。

| 模型 | 用途 | 端口 | 加载方式 |
|------|------|------|---------|
| nomic-embed-text-v2-moe | embedding (768 dim) | :8587 | 常驻 |
| Qwen3-1.7B Q4_K_M | 本地 LLM 推理兜底 | :8080 | **懒加载**(agent 首次调用时热载入) |

> 模型权重**不包含在本仓库**,由 `scripts/fetch-upstreams.py` 或手动从 HuggingFace / ModelScope 下载,落点见 `core/memory_v5/models/model_config.json`（注：原 `core/v5/`，2026-07-26 重命名）。

---

## ⚙️ 配置(可选)

### 云端 LLM

编辑 `.env`(从 `.env.example` 复制):

```bash
DEEPSEEK_API_KEY=sk-...
```

### 本地模型 / 端口

见 `core/memory_v5/models/model_config.json`(本地模型单一事实来源；原 `core/v5/`，2026-07-26 重命名)与 `core/env/ikaros-paths.json`(原生路径注册,相对 `IKAROS_ROOT`)。

### 国内网络慢 / 需要代理

见 [docs/附录-镜像与代理.md](docs/附录-镜像与代理.md)。

---

## 🛠️ 常用命令

| 用途 | 命令 |
|------|------|
| 拉起控制面板 | `bin\ikaros-control.bat` |
| 一键启动整栈 | 控制面板 :9100 → 点 `start` |
| 停止全部 | 控制面板 :9100 → 点 `stop` / 各组件 `stop` |
| 拉取上游 | `python scripts/fetch-upstreams.py` |
| 落地原生配置 | `python scripts/setup-native.py` |
| 记忆服务状态 | `bin\llama-help.py --status` |
| 本地 LLM 热载入 | `bin\llama-help.py --hotload` |
| 查看模型配置 | `bin\llama-help.py --config` |

---

## 📁 目录速览

```
Ikaros\
├── bin\                  ← 启动器/桥接脚本 (.py/.bat/.ps1) + llama-help
├── core\                 ← 核心系统
│  ├── v5\                ← ★ V5 灵魂核心 (记忆/情感/认知/反思)
│  ├── dashboard\         ← 控制面板 Web UI (:9100)
│  ├── env\               ← 环境配置/CLI/初始化脚本
│  └── neko\              ← N.E.K.O 核心组件 (上游, git ignored)
├── config\               ← 配置文件 (identity/ hermes.yaml)
├── data\                 ← ★ 运行时数据 (全部 git ignored)
├── docs\                 ← 文档
├── hermes-agent\         ← 上游 Hermes Agent (git ignored)
├── runtime\              ← 运行时依赖 (git ignored)
├── scripts\              ← 上游拉取 / 原生配置脚本
├── tests\                ← 测试
├── UPSTREAM.md           ← 上游组件清单
├── AGENTS.md             ← 项目记忆库 (架构/决策/gotcha/历史)
└── README.md             ← 你在这里
```

---

## 🐛 故障排查

| 现象 | 第一看 |
|------|--------|
| 控制面板没起来 | `netstat -ano \| findstr :9100` 看进程是否在 |
| 桌宠没出现 | `netstat -ano \| findstr :48911` 看 N.E.K.O 前端是否起 |
| 记忆召回弱 | `bin\llama-help.py --status` 看 `:8587` 心跳 |
| 本地 LLM 没反应 | `bin\llama-help.py --hotload` 手动热载入 `:8080` |
| 端口被占 | `netstat -ano \| findstr :8587` / `:8080` / `:48911` |
| 云端 API 失败 | 检查 `.env` 的 API Key |
| USB 盘符变了 | `scripts/setup-native.py --check` 重新校验路径 |

---

## 🤝 致谢

本项目是**整合 + 二次开发**,站在以下巨人的肩膀上:

| 上游项目 | 链接 | 用途 | 协议 |
|----------|------|------|------|
| **N.E.K.O** | [Project-N-E-K-O/N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) | 桌宠前端 / 多形态 Avatar | Apache-2.0 |
| **Hermes Agent** | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | Agent 核心 / CLI | MIT |
| **llama.cpp** | [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | 本地 LLM 推理 | MIT |
| **Qwen** | [QwenLM/Qwen](https://github.com/QwenLM/Qwen) | 本地模型 | Apache 2.0 |

`core/hermes\` 与 `core/neko\` 是上游的干净副本(经 `UPSTREAM.md` 清单拉取),本项目所有二次开发放在 `core/memory_v5/`、`bin/`、`core/dashboard/` 下（注：`hermes-agent`→`core/hermes`、`core/v5`→`core/memory_v5`，均于 2026-07-26 重命名）。

---

## 📜 协议

- 本整合包: MIT
- llama.cpp: MIT
- Hermes Agent: MIT
- N.E.K.O: Apache-2.0
- 模型权重遵循各自许可证(用户自备)

模型权重**不包含在本仓库**。请从 HuggingFace / ModelScope 等渠道下载。

---

## 📈 更新日志

### 2026-07-26 — 架构收缩 / 去噪音
- 🗑️ **自思考循环 `think.py` 删除** — 定时自循环产出的都是噪音,组件与定时任务一并剔除;`metacog` 仅保留手动/按需反思。
- 🗑️ **独立记忆 sidecar `:9587` (`v5_memory_service.py`) 删除** — 与看门狗记忆完全重复,无消费者;记忆统一走 `store.py` + `ikaros-v5-memory` stdio MCP。
- 🗑️ **弃用组件清理** — 旧 Rust 启动器 `core/env/ikaros-cli/`、legacy `ikaros-think.bat` / `ikaros-start.bat`、孤儿 `supervisor_persist.py`、过时 think 文档全部删除。
- 📦 **仓库瘦身** — 上游组件(neko/runtime/hermes-agent/mcp)移出 git 跟踪,云端只留原生代码 + `UPSTREAM.md` 清单 + `scripts/fetch-upstreams.py` + `scripts/setup-native.py`。
- 🧠 **V5 记忆文档化** — `docs/ARCHITECTURE.md` 校正三路融合、`:8080` 懒加载、结构化守卫;阈值 `min_fused_score` 实配 0.3。
- 🎛️ **控制面板 `:9100` 重构** — `:8080` 与 `:8587` 拆分为两个独立控制格(本地模型 / Memory),均支持面板切换模型;N.E.K.O 三服务合并为「N.E.K.O 服务组」(可一键启动,亦能分开控制),桌面壳 `neko_desktop` 独立;删除 Hermes API `:8642` 网关及脚本、控制面板 Soul Sync 守护与 Persona Sync `v5-sync-persona.py`(前者由 V5 每轮自同步覆盖,后者不再需要)。

### 2026-07-24 — 控制面板定型 + 去语音
- 🗑️ **语音桥 `:7870`/`:7871` 移除** — 语音链路从主架构剔除。
- 🎛️ **控制面板 `:9100` 成为统一启动器** — 一键启停 本地模型(:8080)/ Memory(:8587)/ N.E.K.O 服务组(前端+记忆+Agent 合并)/ Hermes Dashboard(:9119)/ QwenPaw(:8088);`neko` 桌面壳独立;`local_model` 与 `memory` 均可面板切换模型。
- 📁 **目录规范化** — 6 区结构(core/bin/config/data/docs/runtime)。

### 2026-07-07 — no-bridge + Tauri 桌宠定型(历史)
- 去桥架构定型,桌宠 = Tauri v2 + Vue 3 + Live2D。
- V4 记忆上线(SQLite + FTS5 + Chroma 双索引)。

---

## 🔗 相关链接

| 项目 | 链接 |
|------|------|
| **本仓库** | https://github.com/ArtificialAngels/Ikaros |
| N.E.K.O(上游) | https://github.com/Project-N-E-K-O/N.E.K.O |
| Hermes Agent(上游) | https://github.com/NousResearch/hermes-agent |
| llama.cpp | https://github.com/ggml-org/llama.cpp |
| Qwen 模型 | https://huggingface.co/Qwen |
| GGUF 模型索引 | https://huggingface.co/models?library=gguf |

---

*当前架构: 控制面板 :9100 → 本地模型(:8080 可切模型) + Memory(:8587 可切模型) + N.E.K.O 服务组(:48911/:48912/:48915 一键启停) + Hermes Dashboard :9119 + QwenPaw :8088(N.E.K.O 桌面壳独立)*
*启动: `bin\ikaros-control.bat` · 故障看: `bin\llama-help.py --status`*
