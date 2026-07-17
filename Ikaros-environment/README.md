# Ikaros Environment - 路径管理系统

集中管理 Ikaros 项目的所有路径配置，确保完全隔离部署。

> **接手智能体**: 这是 Ikaros 项目的 **PATH / sys.path 等效层** —— 不读 README 也能懂?
> 看 [PATH-LAYER.md](PATH-LAYER.md) (Windows 概念与 Ikaros 实现 1:1 映射, 11 节)。

## 目录结构

```
Ikaros-environment/
├── ikaros-env.bat          # CMD 环境脚本
├── ikaros-env.ps1          # PowerShell 环境脚本
├── ikaros-paths.json       # JSON 路径配置 (供 Python 读取)
├── README.md               # 本文件
└── scripts/
    ├── detect-root.ps1     # 自动检测 IKAROS_ROOT
    └── validate-paths.py   # 路径验证工具
```

## 使用方法

### CMD 批处理

```bat
call "E:\Ikaros\Ikaros-environment\ikaros-env.bat"

REM 现在可以使用所有 IKAROS_* 变量
echo %IKAROS_PYTHON%
echo %IKAROS_LLAMA_SERVER%
echo %IKAROS_MEMORY%
```

### PowerShell

```powershell
. "E:\Ikaros\Ikaros-environment\ikaros-env.ps1"

# 现在可以使用所有 $env:IKAROS_* 变量
Write-Host $env:IKAROS_PYTHON
Write-Host $env:IKAROS_LLAMA_SERVER
```

### Python

```python
import json
from pathlib import Path

# 读取 JSON 配置
paths_json = Path(r"E:\Ikaros\Ikaros-environment\ikaros-paths.json")
config = json.loads(paths_json.read_text(encoding="utf-8"))

python_exe = config["core"]["python"]
llama_server = config["llama"]["server"]
```

## 环境变量列表

### 核心路径

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `IKAROS_ROOT` | 项目根目录 | `E:\Ikaros` |
| `IKAROS_PYTHON` | Python 解释器 | `%IKAROS_ROOT%\runtime\portable-python\python.exe` |
| `IKAROS_RUNTIME` | 运行时目录 | `%IKAROS_ROOT%\runtime` |
| `IKAROS_NODE` | Node.js 可执行文件 | `%IKAROS_ROOT%\runtime\node\node.exe` |
| `IKAROS_DATA` | 数据目录 | `%IKAROS_ROOT%\data` |
| `IKAROS_BIN` | 脚本目录 | `%IKAROS_ROOT%\bin` |
| `IKAROS_CONFIG` | 配置目录 | `%IKAROS_ROOT%\config` |
| `IKAROS_MODULES` | 模块目录 | `%IKAROS_ROOT%\modules` |
| `IKAROS_DEPS` | 依赖目录 | `%IKAROS_ROOT%\deps` |
| `IKAROS_LOGS` | 日志目录 | `%IKAROS_ROOT%\data\logs` |

### Hermes 组件

| 变量名 | 说明 |
|--------|------|
| `IKAROS_HERMES_AGENT` | Hermes Agent 源码 |
| `IKAROS_HERMES_HOME` | Hermes 运行时数据 |
| `IKAROS_BRIDGE` | 桥接层 |
| `IKAROS_HERMES` | Hermes Python 包 |

### Ikaros-memory

| 变量名 | 说明 |
|--------|------|
| `IKAROS_MEMORY` | 记忆模块根目录 |
| `IKAROS_MEMORY_DATA` | 记忆数据目录 |
| `IKAROS_MEMORY_MODELS` | 模型文件目录 |
| `IKAROS_MEMORY_SERVICES` | 服务脚本目录 |
| `IKAROS_MEMORY_SCRIPT` | 主脚本路径 |

### llama-server

| 变量名 | 说明 |
|--------|------|
| `IKAROS_LLAMA_DIR` | llama-server 目录 |
| `IKAROS_LLAMA_SERVER` | llama-server.exe 路径 |
| `IKAROS_LLAMA_CLI` | llama-cli.exe 路径 |
| `IKAROS_LLAMA_VERSION` | 版本号 (默认 `b9867`) |
| `IKAROS_CUDA_VERSION` | CUDA 版本 (默认 `12.4`) |

### 模型路径

| 变量名 | 说明 |
|--------|------|
| `IKAROS_MODEL_EMBEDDING` | Embedding 模型 |
| `IKAROS_MODEL_LLM` | LLM 模型 |

### 服务端口

| 变量名 | 端口 | 说明 |
|--------|------|------|
| `IKAROS_PORT_EMBEDDING` | 8587 | Embedding 服务 |
| `IKAROS_PORT_LLM` | 8589 | LLM 服务 |
| `IKAROS_PORT_BRIDGE` | 7860 | Hermes 桥接 |
| `IKAROS_PORT_WEBUI` | 8648 | Web UI 入口 |
| `IKAROS_PORT_WEBUI_INTERNAL` | 8649 | Web UI 内部 |
| `IKAROS_PORT_LLAMA` | 8080 | llama-server 路由 |

## 兼容性

脚本同时设置 `HERMES_*` 变量，供旧版脚本使用：

- `HERMES_ROOT` = `IKAROS_ROOT`
- `HERMES_BIN` = `IKAROS_BIN`
- `HERMES_PYTHON` = `IKAROS_PYTHON`
- `HERMES_DATA` = `IKAROS_DATA`
- 等等...

## 防干扰措施

1. **PATH 优先级**: 项目内 portable 版本优先于系统版本
2. **清除干扰变量**: 清除 `NODE_PATH`、`NPM_CONFIG_PREFIX`、`PYTHONHOME`
3. **显式路径**: 所有路径显式设置，不依赖系统环境变量

## 验证工具

```bash
# 验证所有路径
python E:\Ikaros\Ikaros-environment\scripts\validate-paths.py

# JSON 输出
python E:\Ikaros\Ikaros-environment\scripts\validate-paths.py --json
```

## 自动检测

`IKAROS_ROOT` 按以下优先级自动检测：

1. 已设置的 `IKAROS_ROOT` 环境变量
2. 已设置的 `HERMES_ROOT` 环境变量 (兼容)
3. 从脚本位置推导 (`Ikaros-environment` 的上一级)
4. 从当前工作目录向上查找
5. 扫描盘符查找 `Ikaros` 目录
