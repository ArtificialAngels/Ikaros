#!/bin/bash
# ========================================
# 恢复 Ikaros 后端设置 key 继承补丁
# ========================================
# 用途：把 .ikaros-patches/ikaros-backend.ts 恢复到
#       packages/server/src/controllers/ikaros-backend.ts
# 调用时机：Hermes Studio 更新 (git pull / npm install) 后重建本地定制
# 手动调用：bash restore-ikaros-backend.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDIO_ROOT="${SCRIPT_DIR}/../../hermes-studio"
SRC="$SCRIPT_DIR/ikaros-backend.ts"
DEST="$STUDIO_ROOT/packages/server/src/controllers/ikaros-backend.ts"

if [ ! -f "$SRC" ]; then
  echo "[ERROR] 源文件缺失: $SRC"
  exit 1
fi

mkdir -p "$(dirname "$DEST")"
cp "$SRC" "$DEST"
echo "[SUCCESS] ikaros-backend.ts 已恢复到 $DEST"
echo "下一步: cd $STUDIO_ROOT && npm run dev (ts-node 热重载即可生效)"
