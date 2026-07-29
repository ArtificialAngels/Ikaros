# ikaros-env.ps1 — 统一路径配置（PowerShell）

> 源文件：`Ikaros-environment/ikaros-env.ps1`
> 作用：集中定义所有 `$env:IKAROS_*` / `$env:HERMES_*`，被其他 ps1 dot-source 后生效。
> 用法：`. "$PSScriptRoot\ikaros-env.ps1"` 或 `. "E:\Ikaros\core\env\ikaros-env.ps1"`

## 检测逻辑

- 若 `IKAROS_ROOT` 未设置，从脚本位置推导（`$PSScriptRoot\..`）。
- 末尾反斜杠去除（`TrimEnd('\')`）。

## 11 个步骤

1. **检测 IKAROS_ROOT**（见上）
2. **核心路径**：`IKAROS_PYTHON`/`RUNTIME`/`NODE`/`DATA`/`BIN`/`CONFIG`/`MODULES`/`DEPS`/`LOGS`/`DATA_MODELS`
3. **Hermes 组件**：`IKAROS_HERMES_AGENT`/`HERMES_HOME`/`BRIDGE`/`HERMES`
4. **Ikaros 专用模块**：`IKAROS_MEMORY`/`MEMORY_DATA`/`MEMORY_MODELS`/`MEMORY_SERVICES`/`MEMORY_SCRIPT`
4b. **Ikaros-Live2D 桌宠**：`IKAROS_LIVE2D`/`NODE_MODULES`
4c. **Portable Rust**：`IKAROS_RUST`（独立 toolchain，无 rustup，仅 `bin\` 放 PATH）
5. **llama-server**：`IKAROS_LLAMA_VERSION`(默认 `b10000-cuda`)/`LLAMA_DIR`/`LLAMA_SERVER`/`LLAMA_CLI`
6. **模型路径**：`IKAROS_MODEL_EMBEDDING`（LLM 模型故意不设，留待 watchdog 默认 local LLM）
7. **服务端口**：
   - `IKAROS_PORT_EMBEDDING=8587`
   - `IKAROS_PORT_LLM=8589`
   - `IKAROS_PORT_BRIDGE=7860`
   - `IKAROS_PORT_LIVE2D_WEBVIEW=8648`（2026-07-05 hermes-web-ui 卸载后让给 Live2D Tauri webview）
   - `IKAROS_PORT_LIVE2D_WEBVIEW_INTERNAL=8649`
   - `IKAROS_PORT_LLAMA=8080`
8. **Python 环境变量**：`PYTHONIOENCODING`/`PYTHONUTF8`/`PYTHONPATH`
9. **PATH 增强**：项目内 portable 版本优先于系统版本（rust/bin、llama、runtime、node、aria2、gopeed、rpc-server、runtime\portable-python）
10. **防干扰**：`NODE_PATH`、`NPM_CONFIG_PREFIX=$null`、`PYTHONHOME=$null`
11. **HERMES 兼容变量**：`HERMES_ROOT`/`BIN`/`PYTHON`/`DATA`/`HOME`/`RUNTIME`/`DEPS`/`MODULES`/`LOGS`/`CONFIG`/`MODELS`/`AGENT_ROOT`

## 已知差异

与 `ikaros-env.bat` 相比，本 `.ps1` 多设了 `IKAROS_PORT_LLM=8589`、
`IKAROS_PORT_BRIDGE=7860`、Live2D webview 端口（`8648`/`8649`）等端口变量。
两套环境配置目前不完全对齐（见 `ikaros-env.md` 说明）。

## 关联文档

- `ikaros-env.md`：bat 等价配置
- `init-ps1.md`：PS1 调用入口
