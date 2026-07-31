# ikaros-env.bat — 统一路径配置（Windows bat）

> 源文件：`Ikaros-environment/ikaros-env.bat`
> 作用：集中定义所有 `IKAROS_*` 与 `HERMES_*` 环境变量，供 portable 使用。
> 由 `init.bat` 调用，**不要直接调用**。

## 重要约束

- **不使用 `setlocal`**：所有变量导出给调用方（父脚本）。
- 幂等：重复调用安全。

## 变量分组

### 根目录
- `IKAROS_ROOT`：自动从脚本位置推导（`Ikaros-environment\` 的上一级），并去除末尾反斜杠。

### 核心路径
| 变量 | 值 |
| --- | --- |
| `IKAROS_PYTHON` | `%IKAROS_ROOT%\runtime\portable-python\python.exe` |
| `IKAROS_RUNTIME` | `%IKAROS_ROOT%\runtime` |
| `IKAROS_NODE` | `%IKAROS_ROOT%\runtime\node\node.exe` |
| `IKAROS_DATA` | `%IKAROS_ROOT%\data` |
| `IKAROS_BIN` | `%IKAROS_ROOT%\bin` |
| `IKAROS_CONFIG` | `%IKAROS_ROOT%\config` |
| `IKAROS_MODULES` | `%IKAROS_ROOT%\modules` |
| `IKAROS_LOGS` | `%IKAROS_ROOT%\data\logs` |

### Hermes Agent 路径
- `IKAROS_HERMES_AGENT` = `%IKAROS_ROOT%\core/hermes`
- `IKAROS_HERMES_HOME` = `%IKAROS_ROOT%\data\hermes-agent`

### Hermes Agent 服务变量
- `HERMES_BIN` = `%IKAROS_HERMES_AGENT%\venv\Scripts\hermes.exe`
- `HERMES_AGENT_CLI_PYTHON` / `HERMES_AGENT_BRIDGE_PYTHON` = `%IKAROS_HERMES_AGENT%\venv\Scripts\python.exe`
- `HERMES_AGENT_NODE` = `C:\Program Files\nodejs\node.exe`

### Ikaros Memory 路径
- `IKAROS_MEMORY` = `%IKAROS_ROOT%\core\memory_v5`
- `IKAROS_MEMORY_DATA` = `%IKAROS_MEMORY%\data`
- `IKAROS_MEMORY_MODELS` = `%IKAROS_MEMORY%\models`
- `IKAROS_MEMORY_SCRIPT` = `%IKAROS_MEMORY%\store.py`

### Ikaros Live2D（Tauri 桌宠）
- `IKAROS_LIVE2D` = `%IKAROS_ROOT%\Ikaros-Live2D`
- `IKAROS_NODE_MODULES` = `%IKAROS_RUNTIME%\node\node_modules`
- **Pet node_modules 联结（portable）**：若 `%IKAROS_LIVE2D%\node_modules\vue` 不存在，尝试 `mklink /J` 把桌宠的 `node_modules` 联结到 `%IKAROS_NODE_MODULES%`（失败仅告警，不致命）。

### Portable Rust
- `IKAROS_RUST` = `%IKAROS_RUNTIME%\rust`（独立工具链，无 rustup，仅把 `bin\` 放 PATH）

### llama-server（llama.cpp）
- `IKAROS_LLAMA_VERSION`：默认 `b10000-cuda`（若已定义则沿用）
- `IKAROS_LLAMA_DIR` = `%IKAROS_RUNTIME%\llama\%IKAROS_LLAMA_VERSION%`
- `IKAROS_LLAMA_SERVER` = `%IKAROS_LLAMA_DIR%\llama-server.exe`

### 模型路径
- `IKAROS_MODEL_EMBEDDING` = `%IKAROS_MEMORY_MODELS%\nomic-embed-text-v2-moe.f32.gguf`
- `IKAROS_MODEL_LLM`：**故意不在此设置**——交由 watchdog 经 resolver 选择默认本地 LLM。

### 服务端口
- `IKAROS_PORT_EMBEDDING` = `8587`
- `IKAROS_PORT_LLAMA` = `8080`

### Python / PATH
- `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`
- `PYTHONPATH=%IKAROS_ROOT%;%IKAROS_HERMES_AGENT`
- `PATH` 前置：`%IKAROS_RUST%\bin;%IKAROS_LLAMA_DIR%;%IKAROS_RUNTIME%;%IKAROS_RUNTIME%\node;%IKAROS_ROOT%\runtime\portable-python\Scripts;%IKAROS_ROOT%\runtime\portable-python;...`
- `NODE_PATH=%IKAROS_RUNTIME%\node\node_modules`
- `PYTHONHOME=`（清空，避免系统 Python 干扰）

### HERMES_* 兼容变量（供旧脚本）
- `HERMES_ROOT=%IKAROS_ROOT%`、`HERMES_HOME=%IKAROS_HERMES_HOME%`、
  `HERMES_PYTHON=%IKAROS_PYTHON%`、`HERMES_RUNTIME=%IKAROS_RUNTIME%`、
  `HERMES_AGENT_ROOT=%IKAROS_HERMES_AGENT%`

## 端口与模型（已于 2026-07-16 对齐 + 2026-07-16 重构）

`.bat` 现已补齐 `.ps1` 的全部端口变量：`IKAROS_PORT_LLM=8080`、
`IKAROS_PORT_BRIDGE=7860`、`IKAROS_PORT_LIVE2D_WEBVIEW=8648`、
`IKAROS_PORT_LIVE2D_WEBVIEW_INTERNAL=8649`。两套环境端口配置已一致。
`IKAROS_MODEL_LLM` 仍**故意不设置**：看门狗与 `start-llm.bat` 都改走
`Ikaros-memory/models/model_config.py` resolver，由 `model_config.json` 决定
初始加载模型（首次运行自动扫描 `models/` 目录创建），不写死任何模型名。

## 关联文档

- `init.md`：调用入口
- `ikaros-env-ps1.md`：PowerShell 等价配置（11 步）
