#!/bin/bash
# ========================================
# 应用 V5 Agent 路由补丁脚本
# ========================================
# 用途：自动修改 chat-run.ts 和 index.ts 添加 V5 支持
# 调用时机：在 restore-v5-agent.sh 之后运行
# 手动调用：bash apply-v5-route-patch.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDIO_ROOT="${SCRIPT_DIR}/../../hermes-studio"

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# ========================================
# 1. 修改 chat-run.ts
# ========================================
log_info "修改 chat-run.ts..."

CHAT_RUN_ROUTE="$STUDIO_ROOT/packages/server/src/routes/hermes/chat-run.ts"

if [ ! -f "$CHAT_RUN_ROUTE" ]; then
  log_warn "chat-run.ts 不存在，跳过"
else
  # 检查是否已有 V5 导入
  if ! grep -q "handle-v5-agent-run" "$CHAT_RUN_ROUTE"; then
    log_info "添加 V5 导入..."

    # 在导入部分添加 V5 导入
    # 找到 Ekko 导入位置，在它前面添加 V5 导入
    if grep -q "handleEkkoAgentRun" "$CHAT_RUN_ROUTE"; then
      sed -i '/import.*handleEkkoAgentRun/a import { handleV5AgentRun } from '\''../../services/hermes/run-chat/handle-v5-agent-run'\''' "$CHAT_RUN_ROUTE"
      log_success "✓ V5 导入已添加"
    else
      log_warn "未找到 Ekko 导入，跳过导入添加"
    fi
  else
    log_success "✓ V5 导入已存在"
  fi

  # 检查是否已有 V5 分支
  if ! grep -q "ikaros-v5" "$CHAT_RUN_ROUTE"; then
    log_info "添加 V5 分支..."

    # 在 Ekko 分支之前添加 V5 分支
    # 使用 awk 来精确插入
    awk '
      /if.*ekko-agent.*{/,/}$/ {
        if (p == 0) {
          # 在 Ekko 分支之前插入 V5 分支
          print "  // Check for V5 Agent"
          print "  if (data.coding_agent_id === '\''ikaros-v5'\'' || data.agent_id === '\''ikaros-v5'\'') {"
          print "    await handleV5AgentRun(nsp, socket, data, profile, sessionMap, dequeueNextQueuedRun)"
          print "    return"
          print "  }"
          print ""
        }
        p = 1
        print
        next
      }
      /}$/ && p == 1 {
        p = 0
      }
      { print }
    ' "$CHAT_RUN_ROUTE" > "${CHAT_RUN_ROUTE}.tmp" && mv "${CHAT_RUN_ROUTE}.tmp" "$CHAT_RUN_ROUTE"

    log_success "✓ V5 分支已添加"
  else
    log_success "✓ V5 分支已存在"
  fi
fi

# ========================================
# 2. 修改 index.ts
# ========================================
log_info "修改 index.ts..."

INDEX_FILE="$STUDIO_ROOT/packages/server/src/index.ts"

if [ ! -f "$INDEX_FILE" ]; then
  log_warn "index.ts 不存在，跳过"
else
  # 检查是否已有 V5 导入
  if ! grep -q "shutdownV5AgentManager" "$INDEX_FILE"; then
    log_info "添加 V5 关闭处理..."

    # 在导入部分添加 V5 导入
    # 找到 shutdownGlobalAgentServer 导入，在它后面添加 V5 导入
    if grep -q "shutdownGlobalAgentServer" "$INDEX_FILE"; then
      sed -i '/import.*shutdownGlobalAgentServer/a import { shutdownV5AgentManager } from '\''./services/v5-agent/manager'\''' "$INDEX_FILE"
      log_success "✓ V5 导入已添加"
    else
      log_warn "未找到 shutdownGlobalAgentServer 导入，跳过导入添加"
    fi

    # 在 beforeExit 处理中添加 V5 关闭
    if grep -q "beforeExit" "$INDEX_FILE"; then
      sed -i '/beforeExit/,/})/ {
        /shutdownGlobalAgentServer/a\  shutdownV5AgentManager()
      }' "$INDEX_FILE"
      log_success "✓ beforeExit 钩子已添加"
    fi

    # 在 SIGTERM 处理中添加 V5 关闭
    if grep -q "SIGTERM" "$INDEX_FILE"; then
      sed -i '/SIGTERM/,/})/ {
        /shutdownGlobalAgentServer/a\  shutdownV5AgentManager()
      }' "$INDEX_FILE"
      log_success "✓ SIGTERM 钩子已添加"
    fi

    # 在 SIGINT 处理中添加 V5 关闭
    if grep -q "SIGINT" "$INDEX_FILE"; then
      sed -i '/SIGINT/,/})/ {
        /shutdownGlobalAgentServer/a\  shutdownV5AgentManager()
      }' "$INDEX_FILE"
      log_success "✓ SIGINT 钩子已添加"
    fi
  else
    log_success "✓ V5 关闭处理已存在"
  fi
fi

# ========================================
# 完成
# ========================================
echo ""
log_success "========================================="
log_success "V5 Agent 路由补丁应用完成"
log_success "========================================="
echo ""
log_info "下一步："
echo "1. cd $STUDIO_ROOT"
echo "2. pnpm build"
echo "3. 重启 Hermes Studio"