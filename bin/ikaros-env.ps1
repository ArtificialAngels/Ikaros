# ikaros-env.ps1 -- Ikaros portable environment (single source of truth, self-anchored)
# Dot-source this file from PowerShell scripts; anchor = $PSScriptRoot normalized, no hardcoded drive letters.
# Refactored 2026-08-18 (removed hermes/neko, added dsh).
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

# Memory V5
$env:IKAROS_MEMORY = Join-Path $root "core\memory_v5"
$env:IKAROS_MEMORY_DATA = Join-Path $root "core\memory_v5\data"
$env:IKAROS_MEMORY_MODELS = Join-Path $root "core\memory_v5\models"
$env:IKAROS_MEMORY_SCRIPT = Join-Path $root "core\memory_v5\store.py"
$env:IKAROS_MODEL_EMBEDDING = Join-Path $root "core\memory_v5\models\bge-m3-q8_0.gguf"

# DeepSeek Harness (dsh) work engine
$env:IKAROS_DSH = Join-Path $root "runtime\dsh"
$env:IKAROS_DSH_SOURCE = Join-Path $root "runtime\deepseek-harness-master"
$env:IKAROS_DSH_PROFILE = Join-Path $root "data\dsh\profiles"
$env:IKAROS_DSH_WEB_PORT = "3080"
$env:IKAROS_DSH_OVERLAY = Join-Path $root "core\ikaros-dsh\cordis.patch.yml"



# Other
$env:IKAROS_RUST = Join-Path $root "runtime\rust"
$env:THIRDSPACE_VAULT = Join-Path $root "data\thirdspace-vault"

# Ports
$env:IKAROS_PORT_EMBEDDING = "8587"
$env:IKAROS_PORT_LLM = "8080"
$env:IKAROS_PORT_LLAMA = "8080"
