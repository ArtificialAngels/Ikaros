# Hermes Agent — 总览

> 本文档是入口索引；**详细分篇在 `docs/` 目录下**。
> 整合包每个目录"自己"有"自己"的说明文件，看哪个目录就打开哪份。

## 30 秒速览

| 项 | 值 |
|---|---|
| 产品名 | **Hermes Agent**（赛博游民数字管家） |
| 内核 | Python 3.12 + FastAPI + llama.cpp |
| LLM | 云端（OpenAI/Anthropic/OpenRouter/MiniMax）+ 本地（Qwen2.5 GGUF） |
| 便携环境 | 自带 Python 3.12（`portable-python/`）+ llama.cpp（`runtime/`） |
| Web UI | 内置 Chat Studio（`http://localhost:7860/chat`） |
| 启动器 | 双击 `launcher.bat` 一键启动 |
| 模型 | Qwen2.5-3B / 7B GGUF（本地）+ 云端多模型 |
| 记忆系统 | 长期记忆 + 知识库 + 技能系统 |

**一句话总结**：**便携 Python** + **llama.cpp 推理** + **云端 LLM 路由** + **记忆/知识/技能三件套** + **内置 Web UI**。

## 怎么启动

```
双击 launcher.bat
```

图形化模型选择器，选模型 → 自动启动 LLM 服务 → 打开 Web UI。

或者分步启动：
- `bin\hermes-web.bat` — 仅 Web UI（需 LLM 服务已运行）
- `bin\hermes.bat` — CLI 对话
- `bin\hermes-all.bat` — 一键启动全部

## 文档导航

> 点击跳转（都是相对路径）

### 整合包根（全局）

- [00-速览.md](00-速览.md) — 整合包完整信息表
- [01-这是什么.md](01-这是什么.md) — 定义 / 能力范围 / 协议
- [02-怎么启动.md](02-怎么启动.md) — 启动器用法 + 命令行 + 配置解读
- [03-目录结构.md](03-目录结构.md) — 完整目录树 + 注释
- [14-维护与升级.md](14-维护与升级.md) — 升级 + 备份 + 重置
- [15-故障排查.md](15-故障排查.md) — 出问题去哪查 + 救命命令
- [16-资源链接.md](16-资源链接.md) — 官网 / 文档 / 模型站

### Hermes 主程序

- [hermes/agent.py](../hermes/agent.py) — 主 Agent 逻辑
- [hermes/llm.py](../hermes/llm.py) — LLM 路由（云端 + 本地）
- [hermes/server.py](../hermes/server.py) — FastAPI 服务端
- [hermes/chat_ui.py](../hermes/chat_ui.py) — 内置 Web UI

### 配置

- [config/hermes.yaml](../config/hermes.yaml) — 主配置文件
- [config/models.yaml](../config/models.yaml) — 模型配置

## 你的"第一天"建议

1. 先跑 `bin\hermes-doctor.bat` 诊断环境
2. 用 `launcher.bat` 选模型启动
3. 浏览器打开 `http://localhost:7860/chat` 开始对话
4. 探索侧边栏的 Memory / Skills / Models 面板

> 文档由 Mavis 在 2026-06-07 基于实际项目结构生成。
> 内容**针对 Hermes Agent 当前版本**；不同版本会有差异。
