# ikaros-env.ps1 — Ikaros 便携环境 (单一权威源, 自锚定)
# 由 PowerShell 脚本 dot-source 本文件; 锚点 = $PSScriptRoot 规范化推导, 不写死盘符
# 重构: 2026-08-18 (移除 hermes/neko, 新增 dsh)
$env:IKAROS_ROOT = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path.TrimEnd('\')
$root = $env:IKAROS_ROOT

$env:IKAROS_BIN = Join-Path $root "bin"
$env:IKAROS_CONFIG = Join-Path $root "config"
$env:IKAROS_DATA = Join-Path $root "data"
$env:IKAROS_RUNTIME = Join-Path $root "runtime"
$env:IKAROS_PYTHON = Join-Path $root "runtime\portable-python\python.exe"
$env:IKAROS_NODE = Join-Path $root "runtime\node\node.exe"
$env:IKAROS_NODE_MODULES = Join-Path $root "runtime\node\node_modules"
$env:IKAROS_LOGS = Join-Path $root "data\logs"
$env:IKAROS_MODULES = Join-Path $root "modules"

# Memory V5
$env:IKAROS_MEMORY = Join-Path $root "core\memory_v5"
$env:IKAROS_MEMORY_DATA = Join-Path $root "core\memory_v5\data"
$env:IKAROS_MEMORY_MODELS = Join-Path $root "core\memory_v5\models"
$env:IKAROS_MEMORY_SCRIPT = Join-Path $root "core\memory_v5\store.py"
$env:IKAROS_MODEL_EMBEDDING = Join-Path $root "core\memory_v5\models\nomic-embed-text-v1.5.Q8_0.gguf"
$env:IKAROS_MODEL_LLM = Join-Path $root "core\memory_v5\models\Phi-4-mini-instruct-Q4_K_M.gguf"

# DeepSeek Harness (dsh) 工作引擎
$env:IKAROS_DSH = Join-Path $root "runtime\dsh"
$env:IKAROS_DSH_SOURCE = Join-Path $root "runtime\deepseek-harness-master"
$env:IKAROS_DSH_PROFILE = Join-Path $root "data\dsh\profiles"
$env:IKAROS_DSH_WEB_PORT = "3080"
$env:IKAROS_DSH_OVERLAY = Join-Path $root "core\ikaros-dsh\cordis.patch.yml"

# omp (oh-my-pi) 编码 agent
$env:IKAROS_OMP_AGENT = Join-Path $root "data\omp\agent"
$env:PI_CODING_AGENT_DIR = $env:IKAROS_OMP_AGENT

# llama.cpp
$env:IKAROS_LLAMA_VERSION = "b10000-cuda"
$env:IKAROS_LLAMA_DIR = Join-Path $root "runtime\llama\$($env:IKAROS_LLAMA_VERSION)"
$env:IKAROS_LLAMA_SERVER = Join-Path $env:IKAROS_LLAMA_DIR "llama-server.exe"

# 其它
$env:IKAROS_RUST = Join-Path $root "runtime\rust"
$env:IKAROS_HERDR = Join-Path $root "runtime\herdr\herdr.exe"
$env:THIRDSPACE_VAULT = Join-Path $root "data\thirdspace-vault"

# 端口
$env:IKAROS_PORT_EMBEDDING = "8587"
$env:IKAROS_PORT_LLM = "8080"
$env:IKAROS_PORT_LLAMA = "8080"
