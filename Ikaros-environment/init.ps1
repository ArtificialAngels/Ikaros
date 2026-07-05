# ============================================================
# init.ps1 - Ikaros Environment Single Entry Point (PowerShell)
# ============================================================
#  Single 入口: 让任何 ps1 脚本第一行 dot-source 此文件
#  就自动获得 $env:IKAROS_* 环境变量与 PATH/PYTHONPATH 等效层。
#
#  用法 (从任意位置):
#    . "E:\Ikaros\Ikaros-environment\init.ps1"
#  它会:
#    1) Detect IKAROS_ROOT (5 优先级, see scripts/detect-root.ps1)
#    2) dot-source ikaros-env.ps1 (核心 11 步, 见 PATH-LAYER.md)
#    3) Echo 自检 (告诉 caller "init OK", 失败抛错)
# ============================================================

# `return` 在 script-scope 是 no-op (只会 break 当前语句)。
# wrap whole logic in function so `return` 真的能从外层调用 short-circuit。
function Invoke-IkarosInit {
    trap {
        Write-Host "[init.ps1 FAIL] $_" -ForegroundColor Red
        return
    }

    # ---- Step 1: 检测 IKAROS_ROOT ----
    if (-not $env:IKAROS_ROOT) {
        try {
            $detected = & "$PSScriptRoot\scripts\detect-root.ps1" 2>$null
            if ($detected) { $env:IKAROS_ROOT = "$detected".Trim() }
        } catch { }
    }
    if (-not $env:IKAROS_ROOT) {
        # 兜底: 脚本位置 /.. 推导
        $env:IKAROS_ROOT = (Resolve-Path "$PSScriptRoot\..").Path.TrimEnd('\')
    }
    if (-not $env:IKAROS_ROOT) {
        Write-Host "[init.ps1 FAIL] IKAROS_ROOT not detected" -ForegroundColor Red
        return
    }

    # ---- Step 2: dot-source ikaros-env.ps1 (核心 11 步) ----
    try {
        . "$env:IKAROS_ROOT\Ikaros-environment\ikaros-env.ps1"
    } catch {
        Write-Host "[init.ps1 FAIL] ikaros-env.ps1 failed: $_" -ForegroundColor Red
        return
    }

    # ---- Step 3: 自检标记 ----
    $env:IKAROS_INIT_DONE = '1'
    Write-Host "[Ikaros init.ps1 OK] IKAROS_ROOT=$env:IKAROS_ROOT"
    Write-Host "[Ikaros init.ps1 OK] python=$env:IKAROS_PYTHON"
    Write-Host "[Ikaros init.ps1 OK] node=$env:IKAROS_NODE"
}

# 调用, 用 if 守卫 — 只有在没 init 过时才跑函数
if (-not $env:IKAROS_INIT_DONE) {
    Invoke-IkarosInit
}
