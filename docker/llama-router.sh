#!/usr/bin/env bash
# ============================================================
# llama-router.sh — 网络检测 + 决定 LLM 路由策略
# 用法：source llama-router.sh
# 设置环境变量：
#   HERMES_NETWORK    = online | offline
#   HERMES_LLM_MODE   = cloud-only | local-only | hybrid
#   HERMES_CLOUD_OK   = true | false
# ============================================================
set -euo pipefail

# ---- 配置：哪些云端 API 需要测试 ----
# 格式：name|base_url|test_path
CLOUD_PROBES=(
    "openai|https://api.openai.com|/v1/models"
    "anthropic|https://api.anthropic.com|/v1/models"
    "google|https://generativelanguage.googleapis.com|/v1beta/models"
    "openrouter|https://openrouter.ai|/api/v1/models"
)

# ---- 通用网络检测（任意一个外网可达就视为 online）----
probe_host() {
    local host="$1"
    # DNS 解析 + TCP 连通 + HTTPS 握手（5s 总超时）
    curl -sS --max-time 3 -o /dev/null -w "%{http_code}" \
        --connect-timeout 2 "https://${host}/" 2>/dev/null | grep -qE '^[1-9]' \
        && return 0
    # fallback：仅 TCP 测试
    timeout 3 bash -c "</dev/tcp/${host}/443" 2>/dev/null
}

probe_api() {
    local name="$1" base="$2" path="$3"
    # 注意：返回 401/403/404 都算"网络通"，只是无权限
    local code
    code=$(curl -sS --max-time 5 -o /dev/null -w "%{http_code}" \
        --connect-timeout 3 "${base}${path}" 2>/dev/null || echo "000")
    [ "$code" != "000" ] && [ "$code" != "" ]
}

# ---- 检测主流程 ----
detect_network() {
    log "Detecting network availability..."

    local network_ok=false
    local cloud_ok=false
    local active_provider=""

    # 1) 检测外网基础连通性（DNS + HTTPS）
    for host in www.google.com www.cloudflare.com 1.1.1.1; do
        if probe_host "$host"; then
            network_ok=true
            log "  ✓ Network reachable via $host"
            break
        fi
    done

    if [ "$network_ok" = false ]; then
        warn "  ✗ No external network detected"
        export HERMES_NETWORK=offline
        export HERMES_CLOUD_OK=false
        export HERMES_LLM_MODE=local-only
        return 0
    fi

    # 2) 探测每个云端 API
    if [ -n "${OPENAI_API_KEY:-}" ] || [ -n "${ANTHROPIC_API_KEY:-}" ] || [ -n "${GOOGLE_API_KEY:-}" ]; then
        IFS='|' read -r p_name p_base p_path <<< "${CLOUD_PROBES[0]}"
        if probe_api "$p_name" "$p_base" "$p_path"; then
            cloud_ok=true
            active_provider="$p_name"
        fi
        for probe in "${CLOUD_PROBES[@]:1}"; do
            IFS='|' read -r name base path <<< "$probe"
            if probe_api "$name" "$base" "$path"; then
                cloud_ok=true
                [ -z "$active_provider" ] && active_provider="$name"
            fi
        done
    else
        warn "  ! No cloud API keys configured (all cloud LLM calls will fail)"
    fi

    # 3) 决策
    if [ "$cloud_ok" = true ]; then
        export HERMES_NETWORK=online
        export HERMES_CLOUD_OK=true
        export HERMES_ACTIVE_CLOUD="${active_provider}"
        # 模式：用户显式强制 > 默认 hybrid
        case "${HERMES_LLM_MODE:-}" in
            cloud-only|local-only) ;;  # 用户强制，保持
            *) export HERMES_LLM_MODE=hybrid ;;
        esac
        log "  ✓ Cloud LLM available ($active_provider)"
    else
        export HERMES_NETWORK=online  # 网络通，但云 API 不可达
        export HERMES_CLOUD_OK=false
        case "${HERMES_LLM_MODE:-}" in
            cloud-only) export HERMES_LLM_MODE=local-only ;;  # 降级
            *) export HERMES_LLM_MODE=local-only ;;
        esac
        warn "  ! Cloud LLM unreachable, falling back to local-only"
    fi

    log "  → HERMES_NETWORK=$HERMES_NETWORK  HERMES_LLM_MODE=$HERMES_LLM_MODE"
}

log()  { printf '\033[1;36m[router]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[router]\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[router]\033[0m %s\n' "$*" >&2; }
