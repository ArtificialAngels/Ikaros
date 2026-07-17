# bin/cloud_chat.py — 桌宠直调 cloud LLM（去桥架构核心）

## 用途（原模块 docstring）
去桥架构核心模块。每次对话自动注入：
- `[soul]` axiom.md 中的伊卡洛斯身份公理
- `[cogno 5D]` 时间 / 设备 / 地理 / 情绪推断 / 上下文压缩

## 用法
```
from cloud_chat import cloud_chat
reply = await cloud_chat("哥哥说的话", session_id="...")
```

## 依赖
- httpx（推荐）或 urllib（回退）
- 环境变量：`DEEPSEEK_API_KEY` 或 `MINIMAX_CN_API_KEY`

## 关键实现（内联要点）
- `_push_monitor(kind, **data)`：推监控事件到循环缓冲区 + 文件（`_MONITOR_FILE`），同名子进程（ikaros-dashboard）通过 tail 读取，实现对话流 + 内心思考的 IPC。
- 监控缓冲 `_MONITOR_LOG` 上限 `_MONITOR_MAX=300`，超出保留最近。
- `build_system_prompt`：仅加载 axiom + cogno_5D + V5 状态（不加载 SOUL/USER/MEMORY），详见 `docs/scripts/...` 相关说明。

> 注：本文件为实时语音/对话核心链路，函数级 docstring 与内联安全提示（鉴权回退、超时）保留在源码，未全量抽离，以免破坏线上链路。如需逐函数抽取，见后续细粒度 pass。
