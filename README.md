# Ikaros

> **赛博游民数字管家** · 装在 U 盘里 · 插到任何 Windows 电脑就能跑 · **零系统依赖**(自带便携 Python + llama.cpp + DeepSeek Harness 工作引擎)

[![Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/python-3.12.10-3776ab)](https://www.python.org/)
[![llama.cpp](https://img.shields.io/badge/llama.cpp-b10000-blueviolet)](https://github.com/ggml-org/llama.cpp)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

🧠 **项目记忆库**:`AGENTS.md` — 架构 / 决策 / gotcha / 历史
📦 **上游清单**:`UPSTREAM.md` — 有上游的组件怎么拉、落哪、是否入库
📖 **架构文档**:`docs/ARCHITECTURE.md` — 分层 / 端口 / 数据流 / 路径注册表

---

## 🎯 一句话

Ikaros 是一个 **完全自包含** 的 AI Agent 运行环境 —— 拷到 U 盘里,插到任何一台 Windows 电脑上双击 `bin\ikaros-control-panel.bat` 拉起 **控制面板 :9100**,一键启动 **工作引擎(dsh) + 记忆 + 对话树**。云端 LLM(DeepSeek)为主,本地 GGUF 模型(由 `core/memory_v5/models/model_config.json` 决定,当前 Phi-4-mini + bge-m3)**懒加载**备用,记忆系统(V5:SQLite + FTS5 + Chroma 向量)全链路本地运行。

> **仓库是「瘦身版」**:本云端仓库只保留 **Ikaros 原生代码 + 配置 + 上游清单/拉取/配置脚本**。所有「有上游」的组件(runtime 工具链、dsh、各类 MCP)**不入库**,统一由 `scripts/fetch-upstreams.py` 拉取、`scripts/setup-native.py` 落地配置。详见 `UPSTREAM.md`。

---

## 🖼️ 架构 (控制面板统一调度, 2026-08-18)

```
                    ┌──── 控制面板 :9100 ────┐
                    │  bin/ikaros-control-panel.bat │
                    │  一键启停全部组件         │
                    └──────────┬─────────────┘
                               │ start
       ┌───────────────────────┼───────────────────┐
       ▼                       ▼                   ▼
┌─────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ Memory      │      │ dsh 工作引擎      │      │ 对话树 :48920     │
│ :8587 embed │◄────►│ :3080 web        │      │ DeepSeek 直连     │
│ :8080 LLM*  │      │ MCP(48 工具)+    │      │ + 只读工具回路    │
└──────┬──────┘      │ terminal + lsp   │      └──────────────────┘
       │             └──────────────────┘
       ▼
┌─────────────┐
│ V5 数据层   │
│ v5.db +     │
│ ChromaDB    │
└─────────────┘

* :8080 本地 LLM 为懒加载: 看门狗只监测端口, 模型在 agent 首次调用时热载入。
* 对话树 :48920 单模式 DeepSeek 直连(人格 = Ikaros 伴侣), 不可达时降级本地三层链路 + 只读工具回路。
* dsh :3080 为 DeepSeek Harness 工作引擎 (overlay 挂载 memory_v5 MCP: 48 个 v5_* 工具 + terminal + typescript LSP + persona)。
```

### 核心端口

| Port | 组件 | 用途 | 状态 |
|------|------|------|------|
| **9100** | 控制面板 | `bin/ikaros-control-panel.bat` Web UI 启动器 | ✅ 常驻 |
| **8587** | Memory (bge-m3 embed) | embedding 1024 dim, V5 记忆写入/召回 | ✅ 常驻 |
| **8080** | 本地模型 (Local LLM) | Phi-4-mini,**懒加载**(agent 调用时热载入);面板可切换模型 | ⏸ 按需 |
| **3080** | dsh (DeepSeek Harness) | 工作引擎 web 界面 + memory_v5 MCP + 工具链 | ⏸ 按需(面板可启停) |
| **48920** | 对话树 | 树形对话面板(`core/conversation-tree/server.py`),DeepSeek 直连 | ✅ 常驻 |

> **已退役 (2026-08-18)**: Hermes 底座(`:8642` gateway / `:8650` Bridge / `:9119` Dashboard / hermes venv)、N.E.K.O 桌宠(`:48911/:48912/:48915`)、QwenPaw(`:8088`)。工作引擎由 **dsh (deepseek-harness)** 承接。
>
> **更早移除**:语音桥 `:7870`/`:7871`(07-24)、独立记忆 sidecar `:9587`(07-26)、自思考循环 `think.py`(07-26)。

---

## ✨ 特性

- **零系统依赖** — 自带便携 Python **3.12.10**(`runtime\portable-python\`)、Node.js(`runtime\node\`)、llama.cpp Windows 二进制 + DLL(`runtime/llama/`)、**dsh**(`runtime/dsh\`,npm 本地安装)。**不需要系统装 Python / Node / VS / CUDA toolkit**。
- **U 盘即插即用** — 项目根路径由 `bin/ikaros-env.sh/.bat/.ps1`(单一权威源,自锚定 `%%~fI` 归一化)自动解析,不写死盘符;换盘符后无需手工改路径。
- **dsh 工作引擎** — DeepSeek Harness 承接过往 agent 底座职责:web 界面(:3080)+ memory_v5 MCP(48 工具)+ terminal + typescript LSP + persona(overlay `core/ikaros-dsh/cordis.patch.yml`,路径经 `!!js process.env.IKAROS_ROOT` 推导,0 硬编码,可整体移动)。
- **控制面板统一调度** — `:9100` Web UI 一键启停全部组件(含 dsh 启停闭环,精确匹配 dsh CLI 进程、不误伤 DSH Desktop)。
- **本地记忆系统 (V5)** — SQLite(FTS5 关键词)+ Chroma(向量语义)+ 时间范围 **三路融合召回**(`min_fused_score` 默认 0.3),统一入口 `unified_retrieve(scope=auto|semantic|lexical|graph|tree|temporal)`;`store()` 实时写入、`consolidate/distill/reflect` 经云端 LLM 归约。无 Qdrant 依赖。
- **5D 认知注入** — `cogno_5d.py` 在每轮对话注入时间/设备/地理/情绪/上下文锚点。
- **云端 LLM 优先** — 对话走 DeepSeek cloud;本地 Phi-4-mini 仅作兜底,懒加载不占常驻资源。
- **对话树** — `:48920` 树形对话面板,多轮/分支对话以可折叠树呈现;单模式 DeepSeek 直连(人格 = Ikaros 伴侣公理 + SOUL 身份)。
- **CRLF 行尾保护** — `.githooks/pre-commit` 阻止 LF-only `.bat` 提交(cmd.exe 会把路径截断)。
- **隐私优先** — `data/`、`runtime/`、`.env`、IDE 状态全在 `.gitignore`。

---

## 🚀 30 秒上手

### 在一台全新 Windows 电脑上(刚 git clone)

```
1. git clone https://github.com/ArtificialAngels/Ikaros.git
2. cd Ikaros
3. python scripts/fetch-upstreams.py     ← 拉取上游 (runtime / mcp / 模型权重)
4. python scripts/setup-native.py        ← 落地 ikaros-paths.json + dsh profile env 参考
5. bin\ikaros-control-panel.bat          ← 拉起控制面板 :9100, 点 start 启动整栈
```

> `scripts/fetch-upstreams.py` 支持 `--list` / `--dry-run` / 按名拉取;`setup-native.py` 支持 `--check` 校验。详见 `UPSTREAM.md`。

### 在你现在的电脑上(已经解压过)

```
1. 双击 bin\ikaros-control-panel.bat
2. 浏览器开 http://127.0.0.1:9100, 点 start
3. 对话树 / dsh 就绪, 开始对话
```

---

## 📦 上游组件(不入库, 拉取而来)

| 目录 | 上游 | 是否入库 |
|------|------|---------|
| `runtime/` | llama.cpp + CUDA 工具链 + node + portable-python | 否 |
| `runtime/dsh/` | [@deepseek-ai/dsh](https://npmjs.com/package/@deepseek-ai/dsh) (DeepSeek Harness) | 否(npm 本地安装) |
| `runtime/MCPServe/` | 各类 MCP 服务器 | 否 |
| `core/memory_v5/models/` | GGUF 模型权重 | 否 |

完整清单、URL、落点、获取方式见 **`UPSTREAM.md`**。

**为什么不直接 git track?** 上游加起来几十个 G(模型权重 + node_modules + runtime),git 仓库会爆。拉取脚本用 gopeed/aria2 + 镜像多线程下载,且 `.gitignore` 已排除这些目录。

---

## 🤖 本地模型

本地模型用于 **记忆系统**(embedding + 反思归约兜底)与本地 LLM 兜底,chat 默认走云端 (DeepSeek)。

| 模型 | 用途 | 端口 | 加载方式 |
|------|------|------|---------|
| bge-m3 q8_0 | embedding (1024 dim, 中英多语言) | :8587 | 常驻 |
| Phi-4-mini-instruct Q4_K_M | 本地 LLM 推理兜底 | :8080 | **懒加载**(agent 首次调用时热载入) |

> 模型权重**不包含在本仓库**,由 `scripts/fetch-upstreams.py` 或手动从 HuggingFace / ModelScope 下载,落点与当前生效模型见 `core/memory_v5/models/model_config.json` 与 `bin/ikaros-env.bat` 的 `IKAROS_MODEL_EMBEDDING/IKAROS_MODEL_LLM`。

---

## ⚙️ 配置(可选)

### 云端 LLM

编辑 `.env`(从 `.env.example` 复制,只放密钥与非路径覆写):

```bash
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-v4-flash
```

### 本地模型 / 端口 / 路径

见 `core/memory_v5/models/model_config.json`(本地模型单一事实来源)与 `bin/ikaros-env.sh/.bat/.ps1`(环境权威源,设置全部 `IKAROS_*` 路径;路径一律由 IKAROS_ROOT 自锚定推导,不写死盘符)。

> ⚠️ dsh 保留变量: `DEEPSEEK_BASE_URL` 只能由启动环境 `export` 设置,不能放进 `.env`(dsh 会拒绝启动)。需要覆写 base URL 时在启动前 `set DEEPSEEK_BASE_URL=...`。

---

## 🛠️ 常用命令

| 用途 | 命令 |
|------|------|
| 拉起控制面板 | `bin\ikaros-control-panel.bat` |
| 一键启动整栈 | 控制面板 :9100 → 点 `start` |
| 停止全部 | 控制面板 :9100 → 点 `stop` / 各组件 `stop` |
| 拉取上游 | `python scripts/fetch-upstreams.py` |
| 落地原生配置 | `python scripts/setup-native.py` |
| **dsh 启动 (web)** | `bin\start-dsh-ikaros.bat web` → :3080 (或面板 dsh 卡片 start) |
| **dsh 启动 (headless)** | `bin\start-dsh-ikaros.bat headless <task>` |
| dsh 重启 | `bin\restart-dsh-ikaros.ps1` |
| 记忆服务状态 | `bin\llama-help.py --status` |
| 本地 LLM 热载入 | `bin\llama-help.py --hotload` |
| 查看模型配置 | `bin\llama-help.py --config` |

---

## 📁 目录速览

```
Ikaros\
├── bin\                  ← 启动器/桥接脚本 (.py/.bat/.ps1) + ikaros-env(环境权威) + llama-help + start-dsh-ikaros
├── core\                 ← 核心系统
│  ├── memory_v5\         ← ★ V5 灵魂核心 (记忆/情感/认知/反思) + 48 个 v5_* MCP 工具
│  ├── dashboard\         ← 控制面板 Web UI (:9100)
│  ├── env\               ← Python 侧环境引导副本 (ikaros-paths.json / llama_resolver.py)
│  ├── conversation-tree\ ← 对话树面板后端 (:48920)
│  └── ikaros-dsh\        ← dsh overlay (cordis.patch.yml: MCP/terminal/lsp/persona)
├── config\               ← 配置文件 (identity/axiom.md 等)
├── data\                 ← ★ 运行时数据 (全部 git ignored; 含 soul/SOUL.md 身份)
├── docs\                 ← 文档
├── runtime\              ← 运行时依赖 (git ignored): portable-python + node + llama + dsh + omp + herdr + MCPServe
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
| 记忆召回弱 | `bin\llama-help.py --status` 看 `:8587` 心跳 |
| 本地 LLM 没反应 | `bin\llama-help.py --hotload` 手动热载入 `:8080` |
| dsh 没起 / :3080 无响应 | 面板 dsh 卡片 start;日志 `data\logs\dsh.log`(UTF-8) |
| dsh overlay 未加载 | 检查用户层 `~/.dsh/profiles/web/cordis.patch.yml` 与规范源 `core/ikaros-dsh/cordis.patch.yml` 是否同步 |
| 对话树只读工具 | 说明走了本地降级链(DeepSeek 直连失败) |
| 端口被占 | `netstat -ano \| findstr :8587` / `:8080` / `:3080` / `:48920` |
| 云端 API 失败 | 检查 `.env` 的 API Key |
| USB 盘符变了 | 无需处理 — IKAROS_ROOT 自锚定, 一切相对推导 |

---

## 🤝 致谢

本项目是**整合 + 二次开发**,站在以下巨人的肩膀上:

| 上游项目 | 链接 | 用途 | 协议 |
|----------|------|------|------|
| **DeepSeek Harness (dsh)** | [@deepseek-ai/dsh](https://npmjs.com/package/@deepseek-ai/dsh) | 工作引擎 (web + MCP + 工具链) | 上游许可 |
| **llama.cpp** | [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | 本地 LLM 推理 | MIT |
| **DeepSeek** | [deepseek.com](https://platform.deepseek.com) | 云端 LLM | — |
| **Hermes Agent** (历史) | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | 曾为 agent 底座, 2026-08-18 退役 | MIT |
| **N.E.K.O** (历史) | [Project-N-E-K-O/N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) | 曾为桌宠前端, 2026-08-18 退役 | Apache-2.0 |

本项目所有二次开发放在 `core/memory_v5/`、`bin/`、`core/dashboard/`、`core/ikaros-dsh/` 下。

---

## 📜 协议

- 本整合包: MIT
- llama.cpp: MIT
- dsh / 模型权重遵循各自许可证(用户自备)

模型权重**不包含在本仓库**。请从 HuggingFace / ModelScope 等渠道下载。

---

## 📈 更新日志

### 2026-08-18 — Hermes/N.E.K.O 底座退役,切换 dsh 工作引擎
- 🔄 **底座切换** — agent 底座从 `hermes-agent` 整体切换为 **DeepSeek Harness (dsh)**;N.E.K.O 桌宠整体移除(仓库瘦身 ~7.4GB)。删除: `runtime/hermes-agent` / `data/hermes-agent` / `core/hermes-bridge` / `apps/neko` / `patches/hermes` / 7 个 bin hermes 脚本。
- 📦 **env 权威收敛** — 单一权威源 `bin/ikaros-env.{bat,sh,ps1}`(自锚定 IKAROS_ROOT,归一化 `%%~fI`,0 盘符硬编码);根 `.env` 只放密钥;dsh overlay 路径经 `!!js process.env.IKAROS_ROOT` 推导。
- 🎛️ **面板瘦身** — `:9100` 清理 usage/cron/kanban/upstream-repo/hermes 管理功能;组件收敛为 local_model / memory / dsh / conversation_tree / herdr / runtime;dsh 启停闭环(精确匹配 dsh CLI,不误伤 DSH Desktop)。
- 🐛 **dsh 排障** — 修复 broken npm shim(改用本地 runtime/dsh)、`.env` 的 DEEPSEEK_BASE_URL 冲突(移除)、duplicate loader(overlay 单一来源+用户层同步)、pyright 缺失(默认关 python LSP)、bat 参数透传与 GBK 编码。
- 🧹 **P1 清理** — 测试重写(14 文件退役/重写/新增 smoke_ikaros_env)、upstream 脚本清理、死文件/脚本删除、模型权威统一 bge-m3、文档改述。

### 2026-08-13 — omp 配置迁出 C 盘
- 📦 **omp 便携化补齐** — 配置目录迁到 `data/omp/agent/`,经 `PI_CODING_AGENT_DIR` 锚定项目。

### 2026-08-12 — 文档/脚本同步清理
- 📝 `README.md` / `docs/ARCHITECTURE.md` 全面同步:本地模型 Phi-4-mini、`:8642` gateway 重新启用(后于 08-18 退役)、`bin/ikaros-env.sh/.bat` 为环境权威源。

### 2026-08-11 — env 收敛 + omp 便携化
- 📦 **IKAROS_* 全部收敛到 `bin/ikaros-env.sh/.bat`**(自锚定)。

### 2026-08-10 — herdr 接入 omp
- 🤖 herdr agent `pi` = omp(oh-my-pi, go-deepseek 通道),`pi` 纳入 V5 核心。

### 2026-08-05 — Hermes Bridge (历史)
- 🌉 **Hermes Bridge `:8650`** — (2026-08-18 已随 hermes 底座退役)。

### 2026-07-24 — 控制面板定型 + 去语音 (历史)
- 🗑️ 语音桥 `:7870`/`:7871` 移除。

### 2026-07-07 — no-bridge + Tauri 桌宠定型 (历史)
- V4 记忆上线(SQLite + FTS5 + Chroma 双索引)。

---

## 🔗 相关链接

| 项目 | 链接 |
|------|------|
| **本仓库** | https://github.com/ArtificialAngels/Ikaros |
| DeepSeek Harness (dsh) | https://npmjs.com/package/@deepseek-ai/dsh |
| llama.cpp | https://github.com/ggml-org/llama.cpp |
| DeepSeek 平台 | https://platform.deepseek.com |
| GGUF 模型索引 | https://huggingface.co/models?library=gguf |

---

*当前架构: 控制面板 :9100 → 本地模型(:8080 可切模型) + Memory(:8587 可切模型) + dsh 工作引擎(:3080) + 对话树 :48920*
*启动: `bin\ikaros-control-panel.bat` · 故障看: `bin\llama-help.py --status` · dsh 日志: `data\logs\dsh.log`*