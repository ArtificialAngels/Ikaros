# See docs/scripts/core/env/init-ps1.md
# 2026-08-18: env 权威源收敛到 bin/ikaros-env.ps1 (删除 core/env/ 重复副本)

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

    # ---- Step 2: dot-source bin/ikaros-env.ps1 (权威源) ----
    try {
        . "$env:IKAROS_ROOT\bin\ikaros-env.ps1"
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
