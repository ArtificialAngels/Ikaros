# See docs/scripts/core/env/ikaros-env-ps1.md

# ---- Step 1: 检测 IKAROS_ROOT ----
if (-not $env:IKAROS_ROOT) {
    # 脚本位于 E:\Ikaros\core\env\ikaros-env.ps1
    # 所以 IKAROS_ROOT = 脚本位置的上一级目录
    $env:IKAROS_ROOT = (Resolve-Path "$PSScriptRoot\..").Path
}
# 去掉末尾反斜杠
$env:IKAROS_ROOT = $env:IKAROS_ROOT.TrimEnd('\')

# ---- Step 2: 核心路径 ----
$env:IKAROS_PYTHON       = "$env:IKAROS_ROOT\runtime\portable-python\python.exe"
$env:IKAROS_RUNTIME      = "$env:IKAROS_ROOT\runtime"
$env:IKAROS_NODE         = "$env:IKAROS_ROOT\runtime\node\node.exe"
$env:IKAROS_DATA         = "$env:IKAROS_ROOT\data"
$env:IKAROS_BIN          = "$env:IKAROS_ROOT\bin"
# ---- ThirdSpace Vault（外部知识库层，thirdspace-bridge skill 用）----
$env:THIRDSPACE_VAULT  = "$env:IKAROS_ROOT\data\thirdspace-vault"
$env:IKAROS_CONFIG       = "$env:IKAROS_ROOT\config"
$env:IKAROS_MODULES      = "$env:IKAROS_ROOT\modules"
$env:IKAROS_DEPS         = "$env:IKAROS_ROOT\deps"
$env:IKAROS_LOGS         = "$env:IKAROS_ROOT\data\logs"
$env:IKAROS_DATA_MODELS  = "$env:IKAROS_ROOT\data\models"

# ---- Step 3: Hermes 组件路径 ----
$env:IKAROS_HERMES_AGENT = "$env:IKAROS_ROOT\core/hermes"
$env:IKAROS_HERMES_HOME  = "$env:IKAROS_ROOT\data\hermes-agent"
$env:IKAROS_BRIDGE       = "$env:IKAROS_ROOT\bridge"
$env:IKAROS_HERMES       = "$env:IKAROS_ROOT\hermes"

# ---- Step 3b: N.E.K.O Frontend (Electron desktop + FastAPI) ----
$env:IKAROS_NEKO         = "$env:IKAROS_ROOT\core\neko"
$env:IKAROS_NEKO_PYTHON  = "$env:IKAROS_NEKO\.venv\Scripts\python.exe"
$env:IKAROS_NEKO_SERVER  = "app.main_server"
$env:IKAROS_NEKO_DESKTOP = "$env:IKAROS_NEKO\N.E.K.O.exe"
$env:IKAROS_NEKO_STATIC  = "$env:IKAROS_NEKO\static"
$env:IKAROS_NEKO_TEMPLATES = "$env:IKAROS_NEKO\templates"
$env:IKAROS_NEKO_PORT    = "48911"

# ---- Step 4: Ikaros 专用模块路径 ----
$env:IKAROS_MEMORY          = "$env:IKAROS_ROOT\core\memory_v5"
$env:IKAROS_MEMORY_DATA     = "$env:IKAROS_MEMORY\data"
$env:IKAROS_MEMORY_MODELS   = "$env:IKAROS_MEMORY\models"
$env:IKAROS_MEMORY_SERVICES = "$env:IKAROS_MEMORY\services"
$env:IKAROS_MEMORY_SCRIPT   = "$env:IKAROS_MEMORY\store.py"

# ---- N.E.K.O Live2D (取代旧 Ikaros-Live2D) ----
$env:IKAROS_LIVE2D       = $env:IKAROS_NEKO
$env:IKAROS_NODE_MODULES = "$env:IKAROS_RUNTIME\node\node_modules"

# ---- Step 4c: Portable Rust toolchain (standalone rustc + cargo) ----
# No rustup needed - just bin/ on PATH. Truly portable, zero registry deps.
$env:IKAROS_RUST         = "$env:IKAROS_RUNTIME\rust"

# ---- Step 5: llama-server 路径 ----
# b9867 是 llama.cpp 构建版本号，位于 runtime\llama\ 下
if (-not $env:IKAROS_LLAMA_VERSION) { $env:IKAROS_LLAMA_VERSION = "b10000-cuda" }
$env:IKAROS_LLAMA_DIR     = "$env:IKAROS_RUNTIME\llama\$env:IKAROS_LLAMA_VERSION"
$env:IKAROS_LLAMA_SERVER  = "$env:IKAROS_LLAMA_DIR\llama-server.exe"
$env:IKAROS_LLAMA_CLI     = "$env:IKAROS_LLAMA_DIR\llama-cli.exe"

# ---- herdr (agent-aware 终端多路复用器，作为受控引擎接入 Ikaros) ----
$env:IKAROS_HERDR        = "$env:IKAROS_ROOT\runtime\herdr\herdr.exe"

# ---- Step 6: 模型路径 ----
$env:IKAROS_MODEL_EMBEDDING = "$env:IKAROS_MEMORY_MODELS\nomic-embed-text-v2-moe.f32.gguf"
# IKAROS_MODEL_LLM intentionally NOT set — watchdog picks the default local LLM via resolver

# ---- Step 7: 服务端口 ----
$env:IKAROS_PORT_EMBEDDING      = "8587"
$env:IKAROS_PORT_LLM            = "8080"
$env:IKAROS_PORT_BRIDGE         = "7860"
# 2026-07-05: hermes-web-ui 卸了 (哥哥).  :8648 让给 Ikaros-Live2D Tauri webview.
$env:IKAROS_PORT_LIVE2D_WEBVIEW          = "8648"
$env:IKAROS_PORT_LIVE2D_WEBVIEW_INTERNAL = "8649"
$env:IKAROS_PORT_LLAMA          = "8080"

# ---- Step 8: Python 环境变量 ----
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8       = "1"
$env:PYTHONPATH       = "$env:IKAROS_ROOT;$env:IKAROS_HERMES_AGENT"

# ---- Step 9: PATH 增强 ----
# 项目内 portable 版本优先于系统版本
$pathParts = @(
    "$env:IKAROS_RUST\bin",
    $env:IKAROS_LLAMA_DIR,
    $env:IKAROS_RUNTIME,
    "$env:IKAROS_RUNTIME\node",
    "$env:IKAROS_RUNTIME\aria2",
    "$env:IKAROS_RUNTIME\gopeed",
    "$env:IKAROS_RUNTIME\rpc-server",
    "$env:IKAROS_ROOT\runtime\portable-python\Scripts",
    "$env:IKAROS_ROOT\runtime\portable-python",
    "$env:IKAROS_ROOT\runtime\herdr"
)
$env:PATH = ($pathParts -join ';') + ';' + $env:PATH

# ---- Step 10: 防干扰措施 ----
$env:NODE_PATH         = "$env:IKAROS_RUNTIME\node\node_modules"
$env:NPM_CONFIG_PREFIX = $null
$env:PYTHONHOME        = $null

# ---- Step 11: HERMES 兼容变量 (供旧脚本使用) ----
$env:HERMES_ROOT       = $env:IKAROS_ROOT
$env:HERMES_BIN        = $env:IKAROS_BIN
$env:HERMES_PYTHON     = $env:IKAROS_PYTHON
$env:HERMES_DATA       = $env:IKAROS_DATA
$env:HERMES_HOME       = $env:IKAROS_HERMES_HOME
$env:HERMES_RUNTIME    = $env:IKAROS_RUNTIME
$env:HERMES_DEPS       = $env:IKAROS_DEPS
$env:HERMES_MODULES    = $env:IKAROS_MODULES
$env:HERMES_LOGS       = $env:IKAROS_LOGS
$env:HERMES_CONFIG     = "$env:IKAROS_CONFIG\hermes.yaml"
$env:HERMES_MODELS     = $env:IKAROS_MEMORY_MODELS
$env:HERMES_AGENT_ROOT = $env:IKAROS_HERMES_AGENT
