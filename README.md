# Hermes Agent

> **赛博游民数字管家** · 装在 U 盘里 · 插到任何 Windows 电脑就能跑 · **零系统依赖**(自带便携 Python + llama.cpp + Node.js 23)

[![Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/python-3.12.10-3776ab)](https://www.python.org/)
[![llama.cpp](https://img.shields.io/badge/llama.cpp-b9503%2B-blueviolet)](https://github.com/ggml-org/llama.cpp)
[![Web UI](https://img.shields.io/badge/Web%20UI-Vue%203%20%2B%20Koa%2042b883)](https://github.com/EKKOLearnAI/hermes-web-ui)
[![License](https://img.shields.io/badge/license-MIT%20%2B%20BSL--1.1-blue)](LICENSE)

📖 **完整文档**:`docs/README-总览.md`(中文) — 速览 / 启动 / 目录 / 维护 / 故障排查
🧠 **项目记忆库**:`AGENTS.md` — 架构 / 决策 / gotcha / 历史

---

## 🎯 一句话

Hermes Agent 是一个 **完全自包含** 的 AI Agent 运行环境 —— 拷到 U 盘里,插到任何一台 Windows 电脑上双击 `bin\hermes-all.bat`,**5 秒后浏览器自动打开**,即可开始对话。云端 LLM(OpenAI / Anthropic / OpenRouter / MiniMax)和本地 GGUF 模型(Qwen / Llama / DeepSeek)走 fallback 链自动切换,断网也能用本地模型继续工作。

---

## 🖼️ 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                       用户浏览器                                  │
│              http://localhost:8648/    ← 自动打开                │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│             Hermes Web UI  (:8648)   ← 主入口                    │
│         (EKKOLearnAI/hermes-web-ui 上游干净副本)                  │
│              Vue 3 SPA + Koa BFF + Socket.IO                     │
│   聊天 / 会话 / 看板 / 定时任务 / 模型管理 / 文件浏览器 / 终端     │
└──────────────────────────────────────────────────────────────────┘
                                │
              ┌─────────────────┼────────────────────┐
              ▼                 ▼                    ▼
   ┌──────────────────┐ ┌──────────────┐ ┌───────────────────────┐
   │  llama-server    │ │ Hermes API   │ │ Hermes Agent Bridge   │
   │     (:8080)      │ │   (:7860)    │ │ (Python shim)         │
   │  OpenAI-兼容 API │ │  FastAPI     │ │ 调上游 hermes-agent   │
   │  router 模式     │ │  embedding / │ │  CLI / memory / KB    │
   │  扫 data\models  │ │  RAG / 会话  │ │                       │
   └──────────────────┘ └──────────────┘ └───────────────────────┘
```

### 三个端口(很重要)

| Port | 进程 | 用途 | 是否暴露浏览器 |
|------|------|------|--------------|
| **8080** | llama-server | LLM 推理(OpenAI 兼容,router 模式) | ❌ 内部 |
| **7860** | Hermes FastAPI (bridge) | embeddings / RAG / sessions / kanban / crons | ❌ 内部 |
| **8648** | **Hermes Web UI**(主入口) | Vue 3 SPA + Koa BFF | ✅ **浏览器打开这个** |

---

## ✨ 特性

- **零系统依赖** — 自带便携 Python 3.12.10(`portable-python/`)、llama.cpp Windows 二进制 + DLL(`runtime/`)、Node.js 23.11.1(`runtime/node23/`)。**不需要系统装 Python / Node / VS / CUDA toolkit**。
- **U 盘即插即用** — 项目根路径由 `bin\hermes-root.py` 自动解析(`E:\` / `F:\` / `G:\` 自适应),写盘符硬编码立刻挂掉。
- **llama-server router 模式** — 一个 llama-server 进程扫 `data\models\*.gguf` 注册所有模型,API 请求按 `model` 字段路由、按需加载、LRU 淘汰。WebUI 下拉菜单选模型,**不需要重启任何进程**。
- **多版本 CUDA 自适应** — `runtime/cuda/{11.8, 12.4, 13.0}/` 按 NVIDIA 驱动版本自动选,525-554 默认 12.4,老卡回退 11.8。
- **模块化服务** — 每个服务是 `modules/<name>/` 自描述包(`module.json` + `start.ps1` + `health.ps1`),`bin\hermes-supervisor.py` 按 `depends_on` 拓扑排序。新增服务 = 新建目录 + 写 `module.json`。
- **Web UI 全功能** — AI 聊天、多会话管理、用量分析、定时任务、模型管理、多 Profile、文件浏览器、群聊、技能管理、日志查看、Web 终端、8 平台渠道配置(Telegram / Discord / Slack / WhatsApp / Matrix / 飞书 / 微信 / 企业微信)。
- **云端 fallback** — `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 写到 `.env` 即可,故障自动切本地或下一家。
- **CRLF 行尾保护** — `.githooks/pre-commit` 阻止 LF-only `.bat` 提交(cmd.exe 会把路径截断)。
- **隐私优先** — `data/`、`hermes/data/`、`.env`、IDE 状态、运行时缓存全在 `.gitignore`,**`git status` 干净**。

---

## 🚀 30 秒上手

### 在你现在的电脑上(已经解压过)

```
1. 把 GGUF 模型放到 data\models\   (没有就下载 — 见下方"模型下载")
2. 双击 bin\hermes-all.bat
3. 等 5 秒,浏览器自动打开 http://localhost:8648/
4. 开始对话
```

### 在一台全新 Windows 电脑上(刚 git clone)

```
1. git clone https://github.com/ArtificialAngels/hermes-agent-portable.git
2. cd "hermes-agent"
3. bin\setup-portable.bat          ← 下载 portable-python + runtime(~1 GB)
4. 把 GGUF 放到 data\models\
5. bin\hermes-all.bat              ← 浏览器自动打开
```

---

## 📦 自带的依赖(`git clone` 之后第一次必须跑 `setup-portable.bat` 下回来)

| 目录 | 大小 | 来源 | 用途 |
|------|------|------|------|
| `portable-python/` | ~230 MB | `setup-portable.bat` 下载 | 嵌入式 Python 3.12.10 + pip 包 |
| `runtime/` | ~700 MB | `setup-portable.bat` 下载 | llama.cpp Windows 二进制 + DLL + Node 23 |
| `runtime/cuda/12.4/` | ~700 MB | `setup-portable.bat` 下载 | CUDA 12.4 运行时(默认) |
| `runtime/cuda/11.8/` | ~400 MB | 按需 | 老 NVIDIA 驱动回退 |
| `data/models/*.gguf` | 自选 | 用户自己下 | 模型权重(可放多个,router 自动管理) |

**为什么不直接 git track?** 因为加起来几个 G,git 仓库会爆。`setup-portable.bat` 用 `aria2c` 多线程下载。

---

## 🤖 模型下载

支持 **任何 GGUF 格式** 模型。常用推荐:

| 模型 | HuggingFace | 大小 | 显存 |
|------|------------|------|------|
| Qwen2.5-3B-Instruct Q4_K_M | `Qwen/Qwen2.5-3B-Instruct-GGUF` | ~2 GB | 4 GB |
| Qwen2.5-7B-Instruct Q4_K_M | `Qwen/Qwen2.5-7B-Instruct-GGUF` | ~4.4 GB | 6 GB |
| Llama-3.1-8B-Instruct Q4_K_M | `lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF` | ~4.7 GB | 6 GB |
| DeepSeek-R1-Distill-Qwen-7B | `unsloth/DeepSeek-R1-Distill-Qwen-7B-GGUF` | ~4.4 GB | 6 GB |
| Qwen3-30B-A3B (MoE) | `Qwen/Qwen3-30B-A3B-GGUF` | ~18 GB | 8 GB(部分 offload) |

下载后放到 `data\models\`,llama-server router 模式会自动发现。

---

## ⚙️ 配置(可选)

### 云端 LLM

编辑 `.env`(从 `.env.example` 复制):

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

### Per-model NGL / ctx-size

`data\models\router-preset.ini`(从 `router-preset.example.ini` 复制):

```ini
[Qwen2.5-7B-Instruct-Q4_K_M.gguf]
n_gpu_layers = 35
ctx_size = 8192
temperature = 0.7

[Qwen3-30B-A3B-Q4_K_M.gguf]
n_gpu_layers = 16          # 8 GB VRAM 放不下全 offload
ctx_size = 4096
```

### 国内网络慢 / 需要代理

见 [docs/附录-镜像与代理.md](docs/附录-镜像与代理.md)。

---

## 🛠️ 常用命令

| 用途 | 命令 |
|------|------|
| 一键启动全部 | `bin\hermes-all.bat` |
| 停止全部 | `bin\hermes-stop.bat` |
| 首次运行 GPU 检测 | `bin\hermes-firstrun.bat` |
| 健康检查(三层探测) | `bin\hermes-health.ps1` |
| 下载便携运行时 | `bin\setup-portable.bat` |
| 模型管理(list/info/download) | `bin\hermes-models.py list` |
| 实时 LLM 日志 | `bin\hermes-model-run.bat` |
| 控制台(切换模型 / 启停服务) | `bin\hermes-console.bat` |
| CRLF 归一化 | `portable-python\python.exe bin\fix-eol.py --all` |
| HERMES_ROOT 路径解析 | `bin\hermes-root.bat resolve` |
| 注册 git hooks(阻止 LF-only .bat 提交) | `bin\install-git-hooks.bat` |

---

## 📁 目录速览

```
hermes-agent\
├── bin\                  ← 启动器集合(CRLF 行尾!)
├── modules\              ← 自描述服务(llm_engine / bridge / webui / env_bootstrap / model_manager / supervisor)
├── deps\                 ← 统一依赖区(junction 桥接 runtime 和 node)
├── bridge\               ← FastAPI 后端(:7860)
├── hermes\               ← Python 桥接层
├── hermes-agent\         ← 上游 NousResearch/hermes-agent(只读)
├── hermes-web-ui\        ← 上游 EKKOLearnAI/hermes-web-ui(只读)
├── docs\                 ← 用户文档(中文)
├── config\               ← hermes.yaml / models.yaml
├── data\                 ← ★ 运行时数据(全部 git ignored)
├── portable-python\      ← Python 3.12.10(git ignored)
├── runtime\              ← llama.cpp + DLL + Node 23(git ignored)
├── tests\                ← 集成测试
├── AGENTS.md             ← 项目记忆库(架构/决策/gotcha/历史)
└── README.md             ← 你在这里
```

完整目录见 [docs/03-目录结构.md](docs/03-目录结构.md)。

---

## 🐛 故障排查

| 现象 | 第一看 |
|------|--------|
| 浏览器打不开 | `bin\hermes-health.ps1` 三层探测 |
| LLM 不响应 | `bin\hermes-model-run.bat` 看实时日志 |
| 端口被占 | `netstat -ano \| findstr :8648` |
| OOM / VRAM 不够 | `data\models\router-preset.ini` 调小 `n_gpu_layers` |
| 云端 API 失败 | 检查 `.env` 的 API Key |
| git commit 被 pre-commit 阻止 | `portable-python\python.exe bin\fix-eol.py --all` |

完整故障排查:[docs/15-故障排查.md](docs/15-故障排查.md)。

---

## 🤝 致谢

本项目是**整合 + 二次开发**,站在以下巨人的肩膀上:

| 上游项目 | 链接 | 用途 | 协议 |
|----------|------|------|------|
| **Hermes Web UI** | [EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui) | 主 Web 界面(Vue 3 + Koa + Socket.IO) | BSL-1.1 |
| **Hermes Agent** | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | Agent 核心 / CLI | MIT |
| **llama.cpp** | [ggerganov/llama.cpp](https://github.com/ggml-org/llama.cpp) | 本地 LLM 推理 | MIT |
| **Qwen** | [QwenLM/Qwen](https://github.com/QwenLM/Qwen) | 默认本地模型 | Apache 2.0 |

`hermes-agent\` 和 `hermes-web-ui\` 是上游的干净副本(Phase 11 锁定版本),本项目所有二次开发放在 `modules\` 下。

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

- 本整合包:MIT
- Hermes Web UI:BSL-1.1
- llama.cpp:MIT
- Hermes Agent:MIT
- 模型权重遵循各自许可证(用户自备)

模型权重**不包含在本仓库**。请从 HuggingFace / ModelScope 等渠道下载。

---

## 📈 更新日志

### 2026-06-13 — v3 Phase Close-Out(本次)
**Soft release** — 无新功能、无破坏性重构。从"作者 U 盘"变成"公开仓库就绪":

- 🔒 **Logs page ENOENT 修复** — 三个地方显式 pin `HERMES_AGENT_CLI_PYTHON`,webui 不再被旧 `HERMES_BIN` 环境变量污染。
- 🔒 **Repo 隐私清理** — 4 个被早期 commit 误跟踪的文件 `git rm --cached`(`data/hermes-agent/config.yaml`、`data/models/router-preset.ini`、`hermes/data/skills/{note,weather}.py`)。两个 skill 文件移到 `docs/examples/skills/`。
- 📝 **`.gitignore` 全面重写** — 新增 `data/{hermes-agent,webui,memory,knowledge,skills,kanban,crons,logs}` 段、IDE 状态、runtime 缓存、备份变体。
- 📝 **文档修订** — `AGENTS.md` 加 §0 phase close-out 章节,`docs/03-目录结构.md` 完全重写(80→175 行),`docs/00-速览.md` 端口修正。
- 📝 **`README.md` 重写** — 从"v1 启动器手册"变成"v3 GitHub 介绍"。

### 2026-06-10 — v2 Phase 11 重构
- 模块化服务架构完成(`modules/{env_bootstrap, llm_engine, bridge, webui, model_manager, supervisor}`)。
- `bin\hermes-root.py` 成为路径解析的单一源真理。
- `bin\hermes-supervisor.py` 用 Python 替代老的 PowerShell orchestrator(规避 cmd /c + 空格路径 bug)。
- `.githooks/pre-commit` 阻止 LF-only `.bat` 提交。
- `deps/` 统一依赖区(junction 桥接 `runtime/node23`、`runtime/cuda/`)。

### 2026-06-09 — llama-server Router 模式
- 单进程扫 `data\models\` 注册所有 GGUF,按 `model` 字段路由、按需加载、LRU 淘汰。
- WebUI 下拉菜单切模型 = 下一条 chat 请求自动走该模型,首次加载几秒,后续走 LRU 缓存。
- Per-model NGL / ctx 配置:`data\models\router-preset.ini`。
- 显式预热:`POST /v1/models/load {model: ...}`,显式驱逐:`POST /v1/models/unload`。

### 2026-06-08 — 集成 Hermes Web UI
- 集成 [EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui) 作为主 Web 界面。
- 解决便携环境兼容性问题(`HERMES_AGENT_BRIDGE_PYTHON` / `HERMES_WEB_UI_HOME` / 禁用 gateway 自启)。
- 新增 6 个内部模块:sessions / workspace / webui_settings / kanban / cron / llm streaming。

### 2026-06-07 — 内部模块扩充
- 6 个新模块:sessions / workspace / webui_settings / kanban / cron / llm streaming。
- 真实 SSE 流式聊天。
- 持久化会话、看板、定时任务。

### 2026-06-06 — llama.cpp b9538 升级
- Qwen3 MoE 支持。
- 多模型切换 CLI。
- GPU 自动检测。

---

## 🔗 相关链接

| 项目 | 链接 |
|------|------|
| **本仓库** | https://github.com/ArtificialAngels/hermes-agent-portable |
| Hermes Web UI(上游) | https://github.com/EKKOLearnAI/hermes-web-ui |
| Hermes Agent(上游) | https://github.com/NousResearch/hermes-agent |
| llama.cpp | https://github.com/ggml-org/llama.cpp |
| Qwen 模型 | https://huggingface.co/Qwen |
| GGUF 模型索引 | https://huggingface.co/models?library=gguf |

---

*最后完整测试:Qwen2.5-7B 真实响应通过 ✓*
*启动时间(冷启动含模型加载):~15 秒*
*内存基线(idle):~400 MB + 模型 VRAM*

[⬆ 回到顶部](#hermes-agent)