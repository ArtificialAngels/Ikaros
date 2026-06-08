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
| Web UI | **Hermes Web UI**（`http://localhost:8648`）来自 [EKKOLearnAI](https://github.com/EKKOLearnAI/hermes-web-ui) |
| 启动器 | 双击 `bin\hermes-all.bat` 一键启动 |
| 模型 | Qwen2.5-3B / 7B / Qwen3.5 MoE（本地）+ 云端多模型 |
| 记忆系统 | 长期记忆 + 知识库 + 技能系统 |

**一句话总结**：**便携 Python** + **llama.cpp 推理** + **云端 LLM 路由** + **记忆/知识/技能三件套** + **完整 Web UI**。

## 怎么启动

```
双击 bin\hermes-all.bat
```

自动启动：
- llama-server（本地 LLM，端口 8080）
- Hermes API（FastAPI 后端，端口 7860）
- **Hermes Web UI**（完整前端，端口 8648）
- 浏览器自动打开 `http://localhost:8648/`

或者分步启动：
- `bin\webui-new.bat` — 仅 Web UI（需 LLM 服务已运行）
- `bin\hermes.bat` — CLI 对话
- `bin\hermes-all.bat` — 启动全部（llama-server router 模式 + Hermes API + Hermes WebUI）

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

### 配置

- [config/hermes.yaml](../config/hermes.yaml) — 主配置文件
- [config/models.yaml](../config/models.yaml) — 模型配置

## 致谢

本项目集成了 [**Hermes Web UI**](https://github.com/EKKOLearnAI/hermes-web-ui) 作为主要的 Web 界面。

**特别感谢 EKKOLearnAI 项目**：
- 功能丰富的 Vue 3 + TypeScript 前端
- 支持 AI 聊天、多会话管理、用量分析、定时任务、模型管理
- 支持 8 个平台渠道配置
- 群聊、文件浏览器、技能管理、日志查看等完整功能

> **项目地址**：https://github.com/EKKOLearnAI/hermes-web-ui

## 你的"第一天"建议

1. 先跑 `bin\hermes-doctor.bat` 诊断环境
2. 用 `bin\hermes-all.bat` 一键启动
3. 浏览器打开 `http://localhost:8648` 开始对话
4. 探索侧边栏的 Memory / Skills / Models 面板

> 文档由 Mavis 在 2026-06-08 基于实际项目结构生成。
> 内容**针对 Hermes Agent 当前版本**；不同版本会有差异。
