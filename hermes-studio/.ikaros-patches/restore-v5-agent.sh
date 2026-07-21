#!/bin/bash
# ========================================
# Ikaros V5 Global Agent 自动恢复脚本
# ========================================
# 用途：在 Hermes Studio 更新后自动恢复 V5 Agent 注册
# 调用时机：通过 package.json postinstall 钩子自动调用
# 手动调用：npm run restore-v5-agent

set -e  # 遇到错误立即退出

# ========================================
# 配置
# ========================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDIO_ROOT="${SCRIPT_DIR}/../../hermes-studio"
PATCHES_DIR="${SCRIPT_DIR}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ========================================
# 检查环境
# ========================================
if [ ! -d "$STUDIO_ROOT" ]; then
  log_error "Hermes Studio 源码不存在: $STUDIO_ROOT"
  log_info "请确保 Studio 源码路径正确，或设置 HERMES_STUDIO_ROOT 环境变量"
  exit 1
fi

log_info "Studio 源码路径: $STUDIO_ROOT"
log_info "补丁目录: $PATCHES_DIR"

# ========================================
# 1. 复制 V5 Agent Manager
# ========================================
log_info "复制 V5 Agent Manager..."

V5_MANAGER_SRC="$PATCHES_DIR/v5-agent-manager.ts"
V5_MANAGER_DST="$STUDIO_ROOT/packages/server/src/services/v5-agent/manager.ts"

if [ -f "$V5_MANAGER_SRC" ]; then
  mkdir -p "$(dirname "$V5_MANAGER_DST")"
  cp "$V5_MANAGER_SRC" "$V5_MANAGER_DST"
  log_success "✓ V5 Agent Manager 已复制"
else
  log_error "V5 Agent Manager 源码不存在: $V5_MANAGER_SRC"
  exit 1
fi

# ========================================
# 2. 复制 V5 Agent Run Handler
# ========================================
log_info "复制 V5 Agent Run Handler..."

V5_HANDLER_SRC="$PATCHES_DIR/handle-v5-agent-run.ts"
V5_HANDLER_DST="$STUDIO_ROOT/packages/server/src/services/hermes/run-chat/handle-v5-agent-run.ts"

if [ -f "$V5_HANDLER_SRC" ]; then
  mkdir -p "$(dirname "$V5_HANDLER_DST")"
  cp "$V5_HANDLER_SRC" "$V5_HANDLER_DST"
  log_success "✓ V5 Agent Run Handler 已复制"
else
  log_error "V5 Agent Run Handler 源码不存在: $V5_HANDLER_SRC"
  exit 1
fi

# ========================================
# 3. 创建 V5 类型定义
# ========================================
log_info "创建 V5 类型定义..."

V5_TYPES_DST="$STUDIO_ROOT/packages/server/src/services/hermes/run-chat/types-v5.ts"
cat > "$V5_TYPES_DST" << 'EOF'
// V5 Agent 类型定义（与 Ekko Agent 保持一致）
export interface V5AgentRunSocketData {
  input: string | any[]
  display_input?: string | any[] | null
  display_role?: 'user' | 'command'
  storage_message?: string
  session_id?: string
  profile?: string
  provider?: string
  model?: string
  workspace?: string | null
  baseUrl?: string
  base_url?: string
  apiKey?: string
  api_key?: string
  mode?: 'scoped' | 'global'
  source?: string
  peerExcludeSocketId?: string
  queue_id?: string
  onEvent?: (event: string, payload: any) => void
  coding_agent_id?: string
  agent_id?: string
}
EOF

log_success "✓ V5 类型定义已创建"

# ========================================
# 4. 检查路由文件
# ========================================
log_info "检查路由文件..."

CHAT_RUN_ROUTE="$STUDIO_ROOT/packages/server/src/routes/hermes/chat-run.ts"

if [ -f "$CHAT_RUN_ROUTE" ]; then
  # 检查是否已有 V5 导入
  if grep -q "handle-v5-agent-run" "$CHAT_RUN_ROUTE"; then
    log_success "✓ V5 导入已存在"
  else
    log_warn "⚠ V5 导入不存在，需要手动添加"
    log_info "请在 $CHAT_RUN_ROUTE 添加导入："
    echo "  import { handleV5AgentRun } from '../../services/hermes/run-chat/handle-v5-agent-run'"
  fi

  # 检查是否已有 V5 分支
  if grep -q "ikaros-v5" "$CHAT_RUN_ROUTE"; then
    log_success "✓ V5 分支已存在"
  else
    log_warn "⚠ V5 分支不存在，需要手动添加"
    log_info "请参考 $PATCHES_DIR/ROUTE_PATCH_INSTRUCTIONS.md"
  fi
else
  log_warn "⚠ 路由文件不存在: $CHAT_RUN_ROUTE"
fi

# ========================================
# 5. 检查 index.ts
# ========================================
log_info "检查主入口文件..."

INDEX_FILE="$STUDIO_ROOT/packages/server/src/index.ts"

if [ -f "$INDEX_FILE" ]; then
  # 检查是否已有 V5 关闭处理
  if grep -q "shutdownV5AgentManager" "$INDEX_FILE"; then
    log_success "✓ V5 关闭处理已存在"
  else
    log_warn "⚠ V5 关闭处理不存在，需要手动添加"
    log_info "请在 $INDEX_FILE 添加导入和关闭钩子"
    echo "  import { shutdownV5AgentManager } from './services/v5-agent/manager'"
  fi
else
  log_warn "⚠ 主入口文件不存在: $INDEX_FILE"
fi

# ========================================
# 6. 构建提示
# ========================================
log_info "检查是否需要重新构建..."

if [ -f "$STUDIO_ROOT/package.json" ]; then
  cd "$STUDIO_ROOT"
  # 检查是否有修改
  if git diff --quiet 2>/dev/null; then
    log_info "没有修改，不需要重新构建"
  else
    log_warn "检测到修改，建议重新构建"
    echo ""
    log_info "运行以下命令重新构建："
    echo "  cd $STUDIO_ROOT"
    echo "  pnpm build"
    echo ""
  fi
fi

# ========================================
# 完成
# ========================================
echo ""
log_success "========================================="
log_success "Ikaros V5 Global Agent 恢复完成"
log_success "========================================="
echo ""
log_info "下一步："
echo "1. 检查并手动添加 V5 导入和分支（如需要）"
echo "2. 运行 pnpm build 重新构建"
echo "3. 重启 Hermes Studio"
echo ""
log_info "详细说明见: $PATCHES_DIR/ROUTE_PATCH_INSTRUCTIONS.md"