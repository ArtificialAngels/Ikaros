#!/usr/bin/env bash
# ============================================================
# llama-supervisor.sh — 本地 LLM 后台常驻 + 崩溃自愈
# 用法：llama-supervisor.sh
# 在 entrypoint.sh 里以后台方式 nohup 启动
# ============================================================
set -euo pipefail

# ---- 路径 ----
MODEL_DIR="${HERMES_LOCAL_MODEL_DIR:-/data/models}"
CONFIG_FILE="${HERMES_LOCAL_MODEL_CONFIG:-/data/config/models.yaml}"
LOG_DIR="/opt/llama/logs"
PID_FILE="/tmp/llama-server.pid"
LOG_FILE="$LOG_DIR/llama-server.log"
LLAMA_BIN="${LLAMA_BIN:-/usr/local/bin/llama-server}"
HOST="${HERMES_LOCAL_LLM_HOST:-127.0.0.1}"
PORT="${HERMES_LOCAL_LLM_PORT:-8080}"

mkdir -p "$LOG_DIR" "$(dirname "$PID_FILE")"

# ---- 加载模型配置 ----
load_model_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        err "Model config not found: $CONFIG_FILE"
        return 1
    fi
    # 默认激活的模型
    ACTIVE_MODEL=$(grep -E '^[[:space:]]*active:' "$CONFIG_FILE" | head -1 | awk '{print $2}' | tr -d '"' || echo "default")
    if [ -z "$ACTIVE_MODEL" ] || [ "$ACTIVE_MODEL" = "null" ]; then
        ACTIVE_MODEL="default"
    fi

    # 解析对应模型路径
    CHAT_MODEL_PATH=$(awk -v active="$ACTIVE_MODEL" '
        /^models:/ { in_models=1; next }
        in_models && /^[[:space:]]{2}[a-zA-Z]/ { current=$1; gsub(/:/,"",current) }
        in_models && /chat:/ && current==active { print $2; exit }
    ' "$CONFIG_FILE" | tr -d '"' | tr -d "'" || true)

    EMBED_MODEL_PATH=$(awk -v active="$ACTIVE_MODEL" '
        /^models:/ { in_models=1; next }
        in_models && /^[[:space:]]{2}[a-zA-Z]/ { current=$1; gsub(/:/,"",current) }
        in_models && /embedding:/ && current==active { print $2; exit }
    ' "$CONFIG_FILE" | tr -d '"' | tr -d "'" || true)

    if [ -z "$CHAT_MODEL_PATH" ]; then
        # 回退：在 MODEL_DIR 里找第一个 .gguf
        CHAT_MODEL_PATH=$(find "$MODEL_DIR" -maxdepth 2 -name "*.gguf" -size +500M 2>/dev/null | head -1)
    fi
    if [ -z "$EMBED_MODEL_PATH" ]; then
        # embedding 模型一般 < 1GB
        EMBED_MODEL_PATH=$(find "$MODEL_DIR" -maxdepth 2 -name "*embed*.gguf" -o -name "*Embed*.gguf" 2>/dev/null | head -1)
    fi
}

# ---- 健康检查 ----
wait_ready() {
    local timeout="${1:-120}"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        if curl -fsS --max-time 2 "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
        printf "."
    done
    return 1
}

# ---- 启动 llama-server ----
start_server() {
    if [ -n "${LLAMA_PID:-}" ] && kill -0 "$LLAMA_PID" 2>/dev/null; then
        log "llama-server already running (PID $LLAMA_PID)"
        return 0
    fi

    load_model_config

    if [ -z "$CHAT_MODEL_PATH" ] || [ ! -f "$CHAT_MODEL_PATH" ]; then
        warn "No chat model found in $MODEL_DIR"
        warn "Run scripts/setup-model.sh to download a model"
        return 1
    fi

    log "Starting llama-server..."
    log "  Model: $CHAT_MODEL_PATH"
    log "  Host:  $HOST:$PORT"
    log "  Embed: ${EMBED_MODEL_PATH:-<not configured>}"

    # 构造启动参数
    local args=(
        -m "$CHAT_MODEL_PATH"
        --host "$HOST"
        --port "$PORT"
        # 性能调优（按 U 盘机器常见配置：8-16GB 内存）
        --ctx-size "${LLAMA_CTX_SIZE:-4096}"
        --threads "${LLAMA_THREADS:-4}"
        --batch-size "${LLAMA_BATCH_SIZE:-512}"
        --ubatch-size "${LLAMA_UBATCH_SIZE:-128}"
        --n-gpu-layers "${LLAMA_NGL:-0}"  # 默认纯 CPU，0=无 GPU
        # OpenAI 兼容
        --jinja
        # 监控
        --log-disable
    )

    # GPU 支持（如果有 NVIDIA + Container Toolkit）
    if [ "${LLAMA_USE_GPU:-0}" = "1" ]; then
        args+=(--n-gpu-layers 99)
    fi

    # 嵌入模型（如果存在）
    if [ -n "$EMBED_MODEL_PATH" ] && [ -f "$EMBED_MODEL_PATH" ]; then
        args+=(--embedding --embd-separator "<#sep#>")
        # 同一个 server 可以同时跑 chat + embedding
    fi

    # 启动到后台
    nohup "$LLAMA_BIN" "${args[@]}" > "$LOG_FILE" 2>&1 &
    LLAMA_PID=$!
    echo "$LLAMA_PID" > "$PID_FILE"
    log "PID: $LLAMA_PID  (log: $LOG_FILE)"

    # 等待就绪
    if wait_ready "${LLAMA_START_TIMEOUT:-180}"; then
        log "llama-server ready ✓"
        return 0
    else
        err "llama-server failed to start within timeout"
        err "Last 30 lines of log:"
        tail -n 30 "$LOG_FILE" >&2 || true
        return 1
    fi
}

# ---- 优雅停止 ----
stop_server() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log "Stopping llama-server (PID $pid)..."
            kill -TERM "$pid" 2>/dev/null || true
            for i in 1 2 3 4 5; do
                sleep 1
                kill -0 "$pid" 2>/dev/null || break
            done
            kill -KILL "$pid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi
}

# ---- 主入口 ----
case "${1:-start}" in
    start)
        start_server
        ;;
    stop)
        stop_server
        ;;
    restart)
        stop_server
        sleep 2
        start_server
        ;;
    status)
        if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "running (PID $(cat "$PID_FILE"))"
            exit 0
        else
            echo "stopped"
            exit 1
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac

log()  { printf '\033[1;36m[llama]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[llama]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[llama]\033[0m %s\n' "$*" >&2; }
