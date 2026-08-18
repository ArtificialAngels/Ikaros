# ikaros-env.sh — Ikaros 便携环境 (单一权威源, 自锚定)
# 由 bash 会话 source (BASH_ENV) 或手动 source
# 锚点原则: 一切路径相对 IKAROS_ROOT 推导, 不写死盘符
# 重构: 2026-08-18 (移除 hermes/neko, 新增 dsh)
export IKAROS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export IKAROS_BIN="${IKAROS_ROOT}/bin"
export IKAROS_CONFIG="${IKAROS_ROOT}/config"
export IKAROS_DATA="${IKAROS_ROOT}/data"
export IKAROS_RUNTIME="${IKAROS_ROOT}/runtime"
export IKAROS_PYTHON="${IKAROS_ROOT}/runtime/portable-python/python.exe"
export IKAROS_NODE="${IKAROS_ROOT}/runtime/node/node.exe"
export IKAROS_NODE_MODULES="${IKAROS_ROOT}/runtime/node/node_modules"
export IKAROS_LOGS="${IKAROS_ROOT}/data/logs"
export IKAROS_MODULES="${IKAROS_ROOT}/modules"

# Memory V5
export IKAROS_MEMORY="${IKAROS_ROOT}/core/memory_v5"
export IKAROS_MEMORY_DATA="${IKAROS_MEMORY}/data"
export IKAROS_MEMORY_MODELS="${IKAROS_MEMORY}/models"
export IKAROS_MEMORY_SCRIPT="${IKAROS_MEMORY}/store.py"
export IKAROS_MODEL_EMBEDDING="${IKAROS_MEMORY_MODELS}/bge-m3-q8_0.gguf"

# DeepSeek Harness (dsh) 工作引擎
export IKAROS_DSH="${IKAROS_ROOT}/runtime/dsh"
export IKAROS_DSH_SOURCE="${IKAROS_ROOT}/runtime/deepseek-harness-master"
export IKAROS_DSH_PROFILE="${IKAROS_DATA}/dsh/profiles"
export IKAROS_DSH_WEB_PORT="3080"
export IKAROS_DSH_OVERLAY="${IKAROS_ROOT}/core/ikaros-dsh/cordis.patch.yml"

# omp (oh-my-pi) 编码 agent
export IKAROS_OMP_AGENT="${IKAROS_DATA}/omp/agent"
export PI_CODING_AGENT_DIR="${IKAROS_OMP_AGENT}"

# llama.cpp / 本地模型
export IKAROS_LLAMA_VERSION="b10000-cuda"
export IKAROS_LLAMA_DIR="${IKAROS_RUNTIME}/llama/${IKAROS_LLAMA_VERSION}"
export IKAROS_LLAMA_SERVER="${IKAROS_LLAMA_DIR}/llama-server.exe"

# 其它运行时
export IKAROS_RUST="${IKAROS_RUNTIME}/rust"
export IKAROS_HERDR="${IKAROS_RUNTIME}/herdr/herdr.exe"
export THIRDSPACE_VAULT="${IKAROS_DATA}/thirdspace-vault"

# 端口
export IKAROS_PORT_EMBEDDING="8587"
export IKAROS_PORT_LLM="8080"
export IKAROS_PORT_LLAMA="8080"

# omp / bun 便携二进制
export PATH="${IKAROS_ROOT}/runtime/bun/bin:${IKAROS_ROOT}/runtime/node/node_modules/bun/bin:${PATH}"
