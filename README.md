# Hermes Portable Agent — 完全自包含版

> **赛博游民数字管家** · 装在 U 盘里 · 插到任何 Windows 电脑就能跑 · **零依赖**（无需 Python、无需联网）

📖 **完整文档**：[docs/README-总览.md](docs/README-总览.md) — 速览 / 启动 / 目录 / 维护 / 故障排查

---

## 🙏 致谢

本项目集成了 [**Hermes Web UI**](https://github.com/EKKOLearnAI/hermes-web-ui) 作为主要的 Web 界面。

**特别感谢 [EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui) 项目**：
- 🌟 功能丰富的 Vue 3 + TypeScript 前端
- 🌟 支持 AI 聊天、多会话管理、用量分析、定时任务、模型管理
- 🌟 支持 8 个平台渠道配置（Telegram/Discord/Slack/WhatsApp/Matrix/飞书/微信/企业微信）
- 🌟 群聊、文件浏览器、技能管理、日志查看等完整功能
- 🌟 多语言支持（中文/英文/日文等 10+ 语言）

> **项目地址**：https://github.com/EKKOLearnAI/hermes-web-ui

---

## 🎯 核心理念

把 Hermes Agent 完整打包成"开箱即用"：
- ✅ **自带 Python**（embeddable 版本，无需安装）
- ✅ **自带 llama.cpp**（Windows 二进制 + DLL）
- ✅ **自带所有 Python 包**（pip 预装到 portable-python）
- ✅ **自带模型**（Qwen2.5-3B + 7B + Qwen3.5 MoE）
- ✅ **自带启动器**（双击即用）
- ✅ **零网络依赖**（除非用云端 LLM）
- ✅ **内置完整 Web UI**（基于 Hermes Web UI）

---

## 🏗️ 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                         用户浏览器                               │
│                    http://localhost:8648/                       │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Hermes Web UI (:8648)                        │
│              (EKKOLearnAI/hermes-web-ui)                        │
│         Vue 3 + Koa + Socket.IO 实时聊天                        │
└─────────────────────────────────────────────────────────────────┘
                                │
            ┌───────────────────┼───────────────────┐
            ▼                   ▼                   ▼
┌───────────────────┐ ┌─────────────────┐ ┌─────────────────────┐
│  llama-server     │ │  Hermes API     │ │  Hermes Agent       │
│  (:8080)          │ │  (:7860)        │ │  Bridge             │
│  本地 LLM 推理    │ │  FastAPI 后端   │ │  Python 桥接        │
│  OpenAI 兼容 API  │ │  记忆/知识/技能 │ │  Agent 执行         │
└───────────────────┘ └─────────────────┘ └─────────────────────┘
```

---

## 🚀 使用方式

### 方式 1：一键启动（推荐）
```
双击 bin\hermes-all.bat
```
自动启动：
- llama-server（本地 LLM，端口 8080）
- Hermes API（FastAPI 后端，端口 7860）
- **Hermes Web UI**（完整前端，端口 8648）
- 浏览器自动打开 `http://localhost:8648/`

### 方式 2：纯 CLI 对话
```
双击 bin\hermes.bat
```
直接进入交互式命令行

### 方式 3：仅启动 Web UI
```
双击 bin\webui-new.bat
```
启动 Hermes Web UI（需要 llama-server 已运行）

### 方式 4：仅本地 LLM
```
双击 bin\start-llm-smart.bat
```
启动 llama-server，可被任何 OpenAI 客户端调用

---

## 📦 目录结构

```
Hermes Agent\                                    ← 这就是 U 盘
│
├── portable-python\                             ← 自带 Python 3.12.10
│   ├── python.exe                               ← 解释器
│   └── Lib\site-packages\                       ← 所有 pip 包
│
├── runtime\                                     ← 自带 llama.cpp
│   ├── llama-server.exe                         ← CPU 版本
│   ├── llama-server-cuda-12.4.exe               ← NVIDIA RTX 20/30/40/50
│   ├── llama-server-cuda-11.8.exe               ← 旧版 NVIDIA
│   ├── llama-server-vulkan.exe                  ← AMD / Intel
│   └── *.dll                                    ← 运行时 DLL
│
├── data\
│   ├── models\                                  ← GGUF 模型文件
│   │   ├── Qwen2.5-3B-Instruct-Q4_K_M.gguf
│   │   ├── Qwen2.5-7B-Instruct-Q4_K_M.gguf
│   │   └── Qwen3.5-35B-A3B-Q4_K_M.gguf
│   ├── webui-new\                               ← ★ Hermes Web UI
│   │   ├── app\                                 ← Web UI 应用代码
│   │   └── data\                                ← Web UI 数据
│   ├── hermes-agent\                            ← Hermes Agent 数据
│   ├── memory\                                  ← 记忆库
│   ├── knowledge\                               ← 知识库
│   └── logs\                                    ← 日志
│
├── hermes\                                      ← Agent 框架
│   ├── agent.py                                 ← 主 Agent
│   ├── llm.py                                   ← LLM 路由
│   ├── memory.py                                ← 记忆系统
│   ├── knowledge.py                             ← 知识库
│   ├── skills.py                                ← 技能系统
│   ├── server.py                                ← FastAPI 服务
│   ├── sessions.py                              ← 会话持久化
│   ├── workspace.py                             ← 文件浏览器
│   ├── kanban.py                                ← 看板系统
│   ├── cron.py                                  ← 定时任务
│   └── static\                                  ← 静态 Web 资源
│
├── hermes-agent-source\                         ← Hermes Agent 源码
│
├── bin\                                         ← 启动器
│   ├── hermes-all.bat                           ← ⭐ 一键启动
│   ├── webui-new.bat                            ← ⭐ Web UI 启动
│   ├── hermes.bat                               ← CLI
│   ├── hermes-stop.bat                          ← 停止所有
│   ├── hermes-console.bat                       ← 控制台
│   ├── hermes-trace.bat                         ← 日志追踪
│   ├── hermes-model-run.bat                     ← LLM 日志查看
│   └── start-llm-smart.bat                      ← LLM 服务
│
├── config\                                      ← 配置
│   ├── hermes.yaml
│   └── models.yaml
│
├── docs\                                        ← 文档
│
├── README.md                                    ← 本文件
└── AGENTS.md                                    ← 项目记忆库
```

---

## 🆕 2026-06-08 更新：集成 Hermes Web UI

### 新增功能

本次更新引入了 [**Hermes Web UI**](https://github.com/EKKOLearnAI/hermes-web-ui) 作为主要的 Web 界面，提供：

| 功能模块 | 说明 |
|---------|------|
| **AI 聊天** | 实时流式聊天、多会话管理、Markdown 渲染、工具调用详情 |
| **平台渠道** | 8 个平台统一配置（Telegram/Discord/Slack/WhatsApp/Matrix/飞书/微信/企业微信）|
| **用量分析** | Token 用量、费用追踪、模型分布、30 天趋势 |
| **定时任务** | Cron 任务创建/编辑/暂停/恢复/立即执行 |
| **模型管理** | 自动发现模型、Provider 管理、OAuth 登录 |
| **多 Profile** | 配置文件隔离、导入/导出/克隆 |
| **文件浏览器** | 远程文件浏览、上传/下载/重命名/删除 |
| **群聊** | 多 Agent 聊天房间、@提及路由、上下文压缩 |
| **技能管理** | 浏览/搜索已安装技能、查看详情 |
| **日志查看** | Agent/Server/Error 日志、多维度过滤 |
| **Web 终端** | 集成终端、多会话支持 |

### 兼容性适配

为将 Hermes Web UI 集成到本地便携环境中，我们解决了以下兼容性问题：

1. **Python 环境适配**：配置 `HERMES_AGENT_BRIDGE_PYTHON` 指向便携 Python
2. **路径隔离**：设置 `HERMES_WEB_UI_HOME` 和 `HERMES_HOME` 到本地数据目录
3. **网关禁用**：设置 `HERMES_WEB_UI_DISABLE_GATEWAY_AUTOSTART=1` 避免自动启动网关
4. **LLM 端点配置**：将 `llama-server` 的 OpenAI 兼容 API 注入配置
5. **启动器封装**：创建 `bin\webui-new.bat` 统一管理环境变量和启动流程

### 启动方式

```bash
# 方式 1：一键启动全部服务（推荐）
bin\hermes-all.bat

# 方式 2：单独启动 Web UI（需 llama-server 已运行）
bin\webui-new.bat start

# Web UI 命令
bin\webui-new.bat start    # 后台启动
bin\webui-new.bat stop     # 停止
bin\webui-new.bat restart  # 重启
bin\webui-new.bat status   # 查看状态
bin\webui-new.bat fg       # 前台运行（调试）
```

---

## 📊 已下载资源（U 盘用量）

| 资源 | 大小 | 备注 |
|------|------|------|
| portable-python\ | ~80 MB | Python 3.12 + 标准库 + site-packages |
| runtime\ | ~34 MB | llama.cpp Windows 二进制 |
| models\ | ~30 GB | GGUF 模型文件 |
| data\webui-new\ | ~100 MB | Hermes Web UI 应用 |
| hermes\ | <1 MB | 框架代码 |
| **总计** | **~31 GB** | |

---

## 🔧 在全新 Windows 电脑上使用

### 场景 1：纯本地模式（无需网络）
1. 插入 U 盘
2. 双击 `bin\hermes-all.bat`
3. 等待 5-10 秒
4. 浏览器打开 http://localhost:8648
5. 开始聊天！

### 场景 2：想要云端 LLM 增强
1. 编辑 `E:\Hermes Agent\.env`，填入 API Key：
   ```
   OPENAI_API_KEY=sk-xxx
   ```
2. 双击 `bin\hermes-all.bat`
3. 在线用云端 API，离线自动降级到本地

---

## 🛠️ 维护与升级

> 📖 详见 [docs/14-维护与升级.md](docs/14-维护与升级.md) — 升级 / 备份 / 重置完整指南

---

## 🐛 故障排查

> 📖 详见 [docs/15-故障排查.md](docs/15-故障排查.md) — 完整的问题→解决表格 + 救命命令

---

## 📈 性能预期

| 模型 | 首次响应 | 后续响应 | 内存占用 |
|------|---------|---------|---------|
| Qwen2.5-3B (CPU) | 30-120s | 2-8s | ~4GB |
| Qwen2.5-3B (GPU) | 5-15s | <1s | ~3GB VRAM |
| Qwen2.5-7B (CPU) | 60-180s | 5-15s | ~8GB |
| Qwen2.5-7B (GPU) | 8-20s | 1-3s | ~6GB VRAM |
| Qwen3.5-35B MoE (GPU) | 15-30s | 2-5s | ~8GB VRAM |
| 云端 (GPT-4o) | 1-3s | 0.5-2s | 几乎无 |

---

## ✅ 验证清单（全部完成）

- [x] 嵌入 Python 3.12 + 全部 pip 依赖
- [x] llama.cpp Windows 二进制 + 依赖 DLL
- [x] Qwen2.5-3B / 7B / Qwen3.5 MoE 模型文件
- [x] Hermes 框架（agent / llm / memory / knowledge / skills / server）
- [x] 知识库 + 记忆库
- [x] CLI 启动器
- [x] Web UI 启动器
- [x] 一键启动（LLM + API + WebUI）
- [x] 端到端测试（真实 LLM 验证通过）
- [x] 完全无系统 Python 依赖
- [x] **集成 Hermes Web UI（EKKOLearnAI）**

---

## 📜 许可证

- 本整合包：遵循各组件许可证
- Hermes Web UI：[BSL-1.1](https://github.com/EKKOLearnAI/hermes-web-ui/blob/main/LICENSE)
- llama.cpp：MIT
- Qwen 模型：遵循各自的模型许可证

---

## 🔗 相关链接

| 项目 | 链接 |
|------|------|
| **Hermes Web UI** | https://github.com/EKKOLearnAI/hermes-web-ui |
| Hermes Agent | https://github.com/NousResearch/hermes-agent |
| llama.cpp | https://github.com/ggerganov/llama.cpp |
| Qwen 模型 | https://github.com/QwenLM/Qwen2.5 |

---

*最后一次完整测试：Qwen2.5-3B 真实响应通过 ✓*
*总 U 盘占用：~31 GB*

---

## 🆕 更新日志

### 2026-06-08
- **集成 Hermes Web UI**（来自 [EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui)）
- 新增完整 Web 管理界面（聊天/渠道/用量/任务/模型/文件/群聊/技能/日志/终端）
- 解决便携环境兼容性问题
- 更新启动器 `hermes-all.bat` 集成新 Web UI

### 2026-06-07
- 6 个新模块：sessions / workspace / webui_settings / kanban / cron / llm streaming
- 真实 SSE 流式聊天
- 持久化会话、看板、定时任务

### 2026-06-06
- llama.cpp b9538 升级（Qwen3 MoE 支持）
- 多模型切换 CLI
- GPU 自动检测
