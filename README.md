# Ikaros

> **赛博游民数字管家** — 装在 U 盘里，插到任何 Windows 电脑就能跑。零系统依赖：自带便携 Python + Node.js + DeepSeek Harness (dsh) 工作引擎。

[![Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/python-3.12.10-3776ab)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

---

## 🎯 目标

构建一个**完全自包含**的 AI Agent 运行环境：拷到 U 盘，换一台电脑双击即用，不依赖系统安装任何东西。以 **dsh (DeepSeek Harness)** 为工作引擎核心，联动**本地记忆系统**和**对话树**，云端 LLM 为主、本地模型懒加载备用。

## 💡 理念

- **零系统依赖** — 便携 Python / Node / 运行时全部自带，不写注册表、不装系统服务
- **U盘即插即用** — 根路径自锚定推导，换盘符零配置
- **隐私优先** — 记忆、数据、运行时全部本地存储，不上传
- **云端优先，本地兜底** — 对话走 DeepSeek cloud，embedding 本地运行，本地 LLM 按需恢复

## 🏗️ 框架

```
                    ┌── dsh 工作引擎 :3080 ──┐
                    │   web UI + MCP + 工具链  │
                    └───────────┬──────────────┘
                                │ 插件联动
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
      ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
      │  对话树插件    │  │  记忆系统插件  │  │  MCP 工具链   │
      │  :48920       │  │  :8587 embed │  │  49 个 v5_*  │
      │  树形对话面板  │  │  SQLite+Chroma│  │  记忆操作工具  │
      └──────────────┘  └──────────────┘  └──────────────┘
```

| 层 | 组件 | 说明 |
|---|---|---|
| 工作引擎 | dsh :3080 | DeepSeek Harness，web UI + 插件系统 + MCP |
| 记忆 | memory_v5 | SQLite (FTS5) + Chroma 向量，三路融合召回，全本地 |
| 对话树 | conversation-tree :48920 | 树形多轮对话面板，DeepSeek 直连 |
| Embedding | bge-m3 :8587 | 本地向量编码，1024 维，中英多语言 |

## ⚠️ 项目状态

**这是一个半成品。** 核心链路可运行，但存在大量未优化的逻辑：

- 记忆召回的评分和融合策略尚在调优，召回质量不稳定
- 对话树前端为单文件 vanilla JS，代码组织和性能有待重构
- 插件间通信通过 postMessage / HTTP 桥接，缺少统一的 IPC 层
- 错误处理和降级机制不完整，部分异常路径会静默失败
- 测试覆盖率低，多数模块只有 smoke test
- 配置分散在多个文件，缺少统一的配置管理

欢迎基于此框架继续开发，但请勿将当前状态视为生产可用。

## 🔌 核心 dsh 插件

两个核心能力以独立 dsh 插件形式发布，位于 monorepo 子目录，可通过 `dsh plugin` 安装：

```bash
# 对话树插件 — server.py 看门狗 + sidebar 入口 + 全屏 iframe + dsh 设置面板
dsh plugin --profile web add github:ArtificialAngels/Ikaros#main:core/ikaros-dsh/plugins/ikaros-conversation-tree

# 记忆系统插件 — 自动记忆工程层 + embedding 模型管理 + dsh 设置面板
dsh plugin --profile web add github:ArtificialAngels/Ikaros#main:core/ikaros-dsh/plugins/ikaros-memory
```

> 插件运行时需要完整 Ikaros 仓库（Python 核心 + 便携 Python），通过 `IKAROS_ROOT` 环境变量定位。

## 🚀 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/ArtificialAngels/Ikaros.git
cd Ikaros

# 2. 拉取上游运行时（便携 Python / Node / dsh / 模型权重）
python scripts/fetch-upstreams.py

# 3. 落地配置
python scripts/setup-native.py

# 4. 启动工作引擎
bin\ikaros web
# 浏览器打开 http://127.0.0.1:3080
```

## 📁 目录

```
Ikaros/
├── bin/                  # 启动器 + 环境配置 (ikaros-env 为路径权威源)
├── core/
│   ├── memory_v5/        # 记忆系统 (SQLite + Chroma + 49 MCP 工具)
│   ├── conversation-tree/# 对话树 (index.html + server.py)
│   └── ikaros-dsh/       # dsh 插件 + overlay 配置
├── config/               # 身份 / 公理配置
├── data/                 # 运行时数据 (git ignored)
├── runtime/              # 便携运行时 (git ignored, fetch-upstreams 拉取)
├── scripts/              # 上游拉取 / 配置脚本
└── docs/                 # 架构文档
```

## 📜 协议

MIT。上游组件（dsh / llama.cpp / 模型权重）遵循各自许可证。模型权重不包含在本仓库中。
