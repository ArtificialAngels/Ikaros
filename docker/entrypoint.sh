#!/usr/bin/env bash
# ============================================================
# entrypoint.sh — 容器启动入口
# 流程：网络检测 → 启动本地 LLM → 启动 Mavis
# ============================================================
set -euo pipefail

# ---- 1. 修正 bind mount 进来的 /data 权限 ----
chown -R hermes:hermes /data /opt/llama 2>/dev/null || true
chmod 755 /data 2>/dev/null || true

# ---- 2. 加载 .env（如果存在）----
if [ -f /data/.env ]; then
    log "Loading .env from /data/.env"
    set -a
    # shellcheck disable=SC1091
    . /data/.env
    set +a
fi

# ---- 3. 解密 GPG 加密的 secrets ----
SECRETS_DIR="/data/.secrets"
if [ -d "$SECRETS_DIR" ] && [ -n "${GPG_PASSPHRASE:-}" ]; then
    log "Decrypting secrets from $SECRETS_DIR"
    for enc in "$SECRETS_DIR"/*.gpg; do
        [ -f "$enc" ] || continue
        out="${enc%.gpg}"
        if [ ! -f "$out" ]; then
            echo "$GPG_PASSPHRASE" | gpg --batch --quiet --passphrase-fd 0 \
                --decrypt "$enc" > "$out" 2>/dev/null && \
                log "  ✓ Decrypted: $(basename "$out")" || \
                warn "  ✗ Failed to decrypt: $(basename "$enc")"
        fi
    done
fi

# ---- 4. 网络检测 + LLM 路由策略 ----
# shellcheck source=llama-router.sh
source /usr/local/bin/llama-router.sh
detect_network

# ---- 5. 启动本地 LLM（如果有模型）----
LOCAL_LLM_STARTED=false
if [ -d /data/models ] && ls /data/models/*.gguf &>/dev/null 2>&1; then
    log "Found local models, starting llama-server..."
    if /usr/local/bin/llama-supervisor.sh start; then
        LOCAL_LLM_STARTED=true
    else
        warn "Local LLM failed to start, continuing without it"
    fi
else
    warn "No local models in /data/models — running in cloud-only mode"
    warn "  (Run scripts/setup-model.sh to download a model for offline use)"
fi

# 把路由决策透传给 Mavis
export HERMES_LOCAL_LLM_AVAILABLE="$LOCAL_LLM_STARTED"
export MAVIS_LLM_ROUTER="${HERMES_LLM_MODE}"

# ---- 6. 等待依赖服务 ----
if [ -n "${WAIT_FOR:-}" ]; then
    log "Waiting for: $WAIT_FOR"
    for hp in $WAIT_FOR; do
        host="${hp%:*}"
        port="${hp##*:}"
        for i in $(seq 1 30); do
            (echo > "/dev/tcp/$host/$port") 2>/dev/null && break
            sleep 1
        done
    done
fi

# ---- 7. 用户自定义 pre-start ----
if [ -x /data/config/pre-start.sh ]; then
    log "Running pre-start hook"
    /data/config/pre-start.sh
fi

# ---- 8. 启动 Mavis ----
log "============================================"
log "  Hermes Agent starting"
log "  - Mode:      $HERMES_LLM_MODE"
log "  - Network:   $HERMES_NETWORK"
log "  - Local LLM: $([ "$LOCAL_LLM_STARTED" = true ] && echo 'ready @ :8080' || echo 'N/A')"
log "  - Web UI:    http://localhost:7860"
log "============================================"

# 优雅退出：传信号给后台 llama-server
cleanup() {
    log "Caught signal, shutting down..."
    /usr/local/bin/llama-supervisor.sh stop 2>/dev/null || true
}
trap cleanup INT TERM

exec "$@"
