# Hermes Portable Agent — 完全自包含版

> **赛博游民数字管家** · 装在 U 盘里 · 插到任何 Windows 电脑就能跑 · **零依赖**（无需 Python、无需联网）

📖 **完整文档**：[docs/README-总览.md](docs/README-总览.md) — 速览 / 启动 / 目录 / 维护 / 故障排查

## 🎯 核心理念

把 Hermes Agent 完整打包成"开箱即用"：
- ✅ **自带 Python**（embeddable 版本，无需安装）
- ✅ **自带 llama.cpp**（Windows 二进制 + DLL）
- ✅ **自带所有 Python 包**（pip 预装到 portable-python）
- ✅ **自带模型**（Qwen2.5-3B + 7B + embed）
- ✅ **自带启动器**（双击即用）
- ✅ **零网络依赖**（除非用云端 LLM）

## 🚀 三种使用方式

### 方式 1：一键启动（推荐）
```
双击 bin\hermes-all.bat
```
自动启动本地 LLM + Web UI，浏览器打开 http://localhost:7860

### 方式 2：纯 CLI 对话
```
双击 bin\hermes.bat
```
直接进入交互式命令行

### 方式 3：Web UI（推荐）
```
双击 bin\hermes-all.bat
```
一键启动 llama-server + Hermes WebUI，浏览器自动打开 `http://localhost:7860/`。
新 UI 是三栏深色面板（左侧 session 列表 / 中间聊天 / 右侧 workspace）。
**WebUI 现在支持**：真实流式聊天（token-by-token SSE）+ 持久化 session + 文件浏览（hermes 项目内的白名单目录）+ 看板任务（boards/tasks）+ 定时任务（cron jobs）+ 设置面板（主题/皮肤/语言等 32 项可配置，原子写入 `data/webui_settings.json`）。

### 方式 4：仅本地 LLM
```
双击 bin\start-llm.bat
```
启动 llama-server，可被任何 OpenAI 客户端调用

## 📦 自包含包结构

```
Hermes Agent\                                    ← 这就是 U 盘
│
├── portable-python\                             ← 自带 Python 3.12.10
│   ├── python.exe                               ← 11 MB 解释器
│   ├── python312.dll
│   ├── python312.zip                            ← 标准库
│   ├── Lib\site-packages\                       ← 所有 pip 包
│   │   ├── fastapi\
│   │   ├── uvicorn\
│   │   ├── httpx\
│   │   ├── pydantic\
│   │   ├── pyyaml\
│   │   ├── rich\
│   │   ├── typer\
│   │   └── ...（30+ 个包）
│   └── get-pip.py                               ← pip bootstrap（备用）
│
├── runtime\                                     ← 自带 llama.cpp
│   ├── llama-server.exe                         ← OpenAI 兼容服务
│   ├── llama.dll                                 ← 推理引擎
│   ├── libomp140.x86_64.dll                      ← OpenMP
│   ├── ggml-cpu-x64.dll                          ← CPU 优化
│   ├── ggml-base.dll
│   └── ... (总计 ~34 MB)
│
├── models\                                      ← 模型（你已有的）
│   ├── Qwen2.5-3B-Instruct-Q4_K_M.gguf          ← 1.96 GB
│   ├── Qwen2.5-7B-Instruct-Q4_K_M.gguf          ← 4.36 GB
│   └── Qwen1.5-1.8B-Chat-Q4_K_M.gguf            ← 1.13 GB
│
├── hermes\                                      ← Agent 框架
│   ├── __init__.py
│   ├── __main__.py
│   ├── agent.py                                  ← 主 Agent
│   ├── llm.py                                    ← LLM 路由
│   ├── memory.py                                 ← 记忆库
│   ├── knowledge.py                              ← 知识库
│   ├── skills.py                                 ← 技能系统
│   ├── server.py                                 ← FastAPI
│   ├── config.py                                 ← 配置
│   ├── data\                                     ← 运行数据
│   │   ├── memory\                               ← 长期记忆
│   │   ├── knowledge\                            ← 知识库
│   │   ├── skills\                               ← 自定义技能
│   │   ├── logs\                                 ← 日志
│   │   └── cache\
│
├── bin\                                         ← 启动器
│   ├── hermes-all.bat                            ← ⭐ 一键启动（LLM + WebUI）
│   ├── hermes.bat                                ← CLI
│   ├── hermes-stop.bat                           ← 停止所有
│   ├── start-llm-smart.bat                       ← 仅 LLM 服务（自动 NGL）
│   └── ...
│
├── config\                                      ← 配置
│   ├── hermes.yaml
│   └── models.yaml
│
├── data\                                        ← 数据
│   ├── logs\                                     ← 日志
│   └── ...
│
├── visual-summary.html                          ← 架构图 v1
├── visual-summary-v2.html                       ← 架构图 v2
├── portable_test.py                             ← 自包含测试
├── quick_test.py                                ← 快速测试
├── README.md                                    ← 本文件
├── Dockerfile                                   ← 可选：Docker 镜像
└── requirements.txt                             ← 依赖清单（已预装）
```

## 🧪 自包含验证（已通过 ✅）

```
[1/6] Python executable: E:\Hermes Agent\portable-python\python.exe
  [OK] Embedded portable Python confirmed

[2/6] Checking bundled dependencies...
  [OK] fastapi
  [OK] uvicorn
  [OK] httpx
  [OK] pydantic
  [OK] yaml
  [OK] rich
  [OK] typer

[3/6] Loading Hermes framework...
  [OK] Hermes v2.0.0

[4/6] Starting local LLM (llama.cpp)...
  [OK] LLM ready in 4s

[5/6] Running Hermes with real LLM...
  [OK] Agent ready, mode: local only

[6/6] Real chat tests:
  Q: Say hi in 5 words.
  A: Hi there.        ← Qwen2.5-3B 真实响应

FULLY PORTABLE - no host Python needed ✓
```

## 📊 已下载资源（U 盘用量）

| 资源 | 大小 | 备注 |
|------|------|------|
| portable-python\ | ~80 MB | Python 3.12 + 标准库 + site-packages |
| runtime\ | ~34 MB | llama.cpp Windows CPU 二进制 |
| models\ | ~7.5 GB | 3 个 GGUF 模型 |
| hermes\ | <1 MB | 框架代码 |
| data\ | <50 MB | 知识库 + 记忆 + 日志 |
| **总计** | **~7.7 GB** | 在 822 GB 可用空间里只占 1% |

## 🔧 在全新 Windows 电脑上使用

### 场景 1：纯本地模式（无需网络）
1. 插入 U 盘
2. 双击 `bin\hermes-all.bat`
3. 等待 5-10 秒（首次启动较慢）
4. 浏览器打开 http://localhost:7860
5. 开始聊天！

### 场景 2：想要云端 LLM 增强
1. 编辑 `E:\Hermes Agent\.env`，填入 API Key：
   ```
   OPENAI_API_KEY=sk-xxx
   ```
2. 双击 `bin\hermes.bat`（CLI）或 `bin\hermes-all.bat`（WebUI）
3. 在线用云端 API，离线自动降级到本地

### 场景 3：嵌入到其他项目
```python
# 在你的项目里直接 import
import sys
sys.path.insert(0, r"E:\Hermes Agent")
from hermes.agent import HermesAgent
from hermes.config import load_config
import asyncio

agent = HermesAgent(load_config(r"E:\Hermes Agent\config\hermes.yaml"))
print(asyncio.run(agent.chat("你好")))
```

## 🛠️ 维护与升级

> 📖 详见 [docs/14-维护与升级.md](docs/14-维护与升级.md) — 升级 / 备份 / 重置完整指南

## 🐛 故障排查

> 📖 详见 [docs/15-故障排查.md](docs/15-故障排查.md) — 完整的问题→解决表格 + 救命命令

## 📈 性能预期

| 模型 | 首次响应 | 后续响应 | 内存占用 |
|------|---------|---------|---------|
| Qwen2.5-3B (CPU) | 30-120s | 2-8s | ~4GB |
| Qwen2.5-3B (GPU) | 5-15s | <1s | ~3GB VRAM |
| Qwen2.5-7B (CPU) | 60-180s | 5-15s | ~8GB |
| Qwen2.5-7B (GPU) | 8-20s | 1-3s | ~6GB VRAM |
| 云端 (GPT-4o) | 1-3s | 0.5-2s | 几乎无 |

> 性能数据基于典型笔记本配置（i7 + 16GB RAM），实际取决于硬件。

## ✅ 验证清单（全部完成）

- [x] 嵌入 Python 3.12 + 全部 pip 依赖
- [x] llama.cpp Windows 二进制 + 依赖 DLL
- [x] Qwen2.5-3B / 7B / 1.5 模型文件
- [x] Hermes 框架（agent / llm / memory / knowledge / skills / server）
- [x] 知识库（6 篇文档 + 自动摄入）
- [x] 记忆库（33+ items 累积）
- [x] CLI 启动器
- [x] Web UI 启动器
- [x] 一键启动（LLM + Web）
- [x] 端到端测试（真实 LLM 验证通过）
- [x] 完全无系统 Python 依赖

## 🎯 下次使用

1. 把 U 盘插到任意 Win10/11 电脑
2. 双击 `E:\Hermes Agent\bin\hermes-all.bat`
3. 浏览器打开 http://localhost:7860
4. 享受与你的"赛博游民数字管家"的对话

**或者用 CLI**：
```
双击 E:\Hermes Agent\bin\hermes.bat
you> 你好
hermes> [响应]
```

---
*最后一次完整测试：Qwen2.5-3B 真实响应通过 ✓*
*总 U 盘占用：~7.7 GB / 822 GB 可用*

## 🆕 2026-06-07 更新

6 个新模块 + 4 个新数据目录已上线（详见 `AGENTS.md` §3/§4/§8/§12/§13）：

- `hermes/sessions.py` — 持久化 chat session（每个 session 一个 JSON）
- `hermes/workspace.py` — 白名单文件浏览（hermes 根目录内）
- `hermes/webui_settings.py` — WebUI 设置原子持久化
- `hermes/kanban.py` — 看板（boards/tasks/events，22 个 endpoint）
- `hermes/cron.py` — 定时任务（croniter 调度，30s 后台循环，10 个 endpoint）
- `hermes/llm.py` — 新增 `stream()` 真实流式输出（SSE 替代旧的假流式）

数据目录：`hermes/data/sessions/` `hermes/data/kanban/` `hermes/data/crons/` `hermes/data/webui_settings.json`

启动方式不变：`双击 bin\hermes-all.bat` 即可。
