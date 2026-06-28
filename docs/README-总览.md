# Hermes Agent — 文档总览(中文)

> 本文档是文档库的入口索引。**详细分篇在 `docs/` 目录下**。
> 用户视角介绍见 [`README.md`](../README.md)(GitHub 渲染的入口)。
> 项目内部状态见 [`AGENTS.md`](../AGENTS.md)(架构 / 决策 / gotcha / 历史)。

## 30 秒速览

| 项 | 值 |
|---|---|
| 产品名 | **Hermes Agent**(赛博游民数字管家,v3 / 2026-06-13) |
| 内核 | Python 3.12.12(便携) + FastAPI(bridge) + Koa 2(webui BFF) + llama.cpp b9503+(LLM 引擎) |
| LLM | 云端(OpenAI / Anthropic / OpenRouter / MiniMax)+ 本地(任意 GGUF) |
| Web UI | **Hermes Web UI**(`http://localhost:8648`,主入口)— 来自 [EKKOLearnAI](https://github.com/EKKOLearnAI/hermes-web-ui) |
| 启动器 | 双击 `bin\hermes-all.bat` 一键启动 |
| 模型 | 用户自备(放 `data\models\`),任何 GGUF 格式 |
| 记忆 / 知识库 / 技能 | `data\{memory, knowledge, skills}\`(git ignored) |
| 模块架构 | `modules/{env_bootstrap, llm_engine, bridge, webui, model_manager, supervisor}` |

**一句话总结**:**便携 Python** + **llama.cpp router 模式** + **云端 LLM 路由** + **自描述模块化服务** + **完整 Web UI**。

## 三个端口(防止混淆)

| Port | 进程 | 角色 |
|------|------|------|
| **8080** | llama-server | LLM 推理,OpenAI 兼容 API。**内部端口** — 不对浏览器。 |
| **7860** | Hermes FastAPI (bridge) | embeddings / RAG / sessions / kanban / crons。**内部端口**。 |
| **8648** | **Hermes Web UI** | Vue 3 SPA + Koa BFF + Socket.IO。**浏览器打开这个**。 |

## 怎么启动

```
双击 bin\hermes-all.bat
```

自动启动:
- llama-server(本地 LLM,端口 8080,router 模式扫 `data\models\`)
- Hermes FastAPI bridge(端口 7860,内部 API)
- **Hermes Web UI**(端口 8648,**主入口**)
- 浏览器自动打开 `http://localhost:8648/`

或者分步:
- `bin\hermes-models.py list` — 看本地模型
- `bin\hermes-console.bat` — 进入交互式控制台(Switch-Model / Status / Restart-Module)
- `bin\hermes-stop.bat` — 优雅停止全部

## 文档导航

> 点击跳转(都是相对路径)

### 整合包根(全局)

- [README.md](../README.md) — GitHub 介绍(用户视角)
- [AGENTS.md](../AGENTS.md) — 项目记忆库(架构 / 决策 / gotcha / 历史)
- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) — 开发者视角精简地图(架构 / 模块 / 不变式 / 文件索引)
- [00-速览.md](00-速览.md) — 整合包完整信息表
- [01-这是什么.md](01-这是什么.md) — 定义 / 能力范围 / 协议
- [02-怎么启动.md](02-怎么启动.md) — 启动器用法 + 命令行 + 配置解读
- [03-目录结构.md](03-目录结构.md) — 完整目录树 + 注释
- [14-维护与升级.md](14-维护与升级.md) — 升级 + 备份 + 重置
- [15-故障排查.md](15-故障排查.md) — 出问题去哪查 + 救命命令
- [16-资源链接.md](16-资源链接.md) — 官网 / 文档 / 模型站
- [附录-镜像与代理.md](附录-镜像与代理.md) — 国内网络配置

### 关键代码

- [bin/hermes-all.bat](../bin/hermes-all.bat) — 一键启动
- [bin/hermes-root.py](../bin/hermes-root.py) — 路径解析单一源真理
- [bin/hermes-supervisor.py](../bin/hermes-supervisor.py) — 进程编排器
- [modules/](../modules/) — 自描述服务模块
- [deps/hermes-env.bat](../deps/hermes-env.bat) — 14 个 HERMES_* 变量装载器
- [bridge-rs/src/main.rs](../bridge-rs/src/main.rs) — FastAPI bridge(:7860)
- [hermes/](../hermes/) — Python 桥接层

### 上游干净副本(只读)

- [hermes-agent/](../hermes-agent/) — [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) v0.16.0
- [hermes-web-ui/](../hermes-web-ui/) — [EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui) v0.6.x

### 配置

- [config/hermes.yaml](../config/hermes.yaml) — 主配置文件(LLM / router / 网络)
- [config/models.yaml](../config/models.yaml) — 模型配置
- [.env.example](../.env.example) — 环境变量模板(填值后另存为 `.env`)
- [data/models/router-preset.example.ini](../data/models/router-preset.example.ini) — Per-model NGL/ctx 模板

### Examples

- [docs/examples/skills/](../docs/examples/skills/) — 参考 skill 模板(`note.py` / `weather.py`)
- [docs/examples/skills/README.md](../docs/examples/skills/README.md) — 如何写自己的 skill

## 致谢

本项目是**整合 + 二次开发**,站在以下巨人的肩膀上:

| 上游项目 | 链接 | 用途 | 协议 |
|----------|------|------|------|
| **Hermes Web UI** | [EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui) | 主 Web 界面(Vue 3 + Koa + Socket.IO) | BSL-1.1 |
| **Hermes Agent** | [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | Agent 核心 / CLI | MIT |
| **llama.cpp** | [ggerganov/llama.cpp](https://github.com/ggml-org/llama.cpp) | 本地 LLM 推理 | MIT |
| **Qwen** | [QwenLM/Qwen](https://github.com/QwenLM/Qwen) | 默认本地模型 | Apache 2.0 |

## 你的"第一天"建议

1. 先读 [`README.md`](../README.md) 了解产品
2. 用 `bin\hermes-all.bat` 一键启动
3. 浏览器打开 `http://localhost:8648/` 开始对话
4. 探索侧边栏的 Memory / Skills / Models / Kanban / Crons 面板
5. 出问题看 [`docs/15-故障排查.md`](15-故障排查.md)

> 文档由 Hermes 在 2026-06-13 基于实际项目结构生成。
> 内容**针对 Hermes Agent v3**;不同版本会有差异。