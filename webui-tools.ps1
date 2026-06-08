# Hermes Web UI 工具脚本
# 用于方便执行 Web UI 相关命令

$env:HERMES_AGENT_BRIDGE_PYTHON = "$PSScriptRoot\portable-python\python.exe"
$env:HERMES_WEB_UI_HOME = "$PSScriptRoot\data\webui-new\data"
$env:HERMES_WEB_UI_DISABLE_GATEWAY_AUTOSTART = "1"
$NODE = "$PSScriptRoot\runtime\node23\node.exe"
$WEBUI_DIR = "$PSScriptRoot\data\webui-new\app"

function Show-Help {
    Write-Host "Hermes Web UI 工具" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "用法:" -ForegroundColor Yellow
    Write-Host "  .\webui-tools.ps1 reset-password    - 重置默认密码"
    Write-Host "  .\webui-tools.ps1 clear-locks       - 清除登录锁定"
    Write-Host "  .\webui-tools.ps1 restart           - 重启 Web UI"
    Write-Host "  .\webui-tools.ps1 stop              - 停止 Web UI"
    Write-Host "  .\webui-tools.ps1 start             - 启动 Web UI"
    Write-Host "  .\webui-tools.ps1 status            - 查看状态"
    Write-Host ""
}

if ($args.Count -eq 0) {
    Show-Help
    exit 0
}

$command = $args[0]

Set-Location $WEBUI_DIR

switch ($command) {
    "reset-password" {
        & $NODE bin\hermes-web-ui.mjs reset-default-login
    }
    "clear-locks" {
        & $NODE bin\hermes-web-ui.mjs clear-login-locks
    }
    "restart" {
        & $NODE bin\hermes-web-ui.mjs restart
    }
    "stop" {
        & $NODE bin\hermes-web-ui.mjs stop
    }
    "start" {
        & $NODE bin\hermes-web-ui.mjs start
    }
    "status" {
        & $NODE bin\hermes-web-ui.mjs status
    }
    default {
        Write-Host "未知命令: $command" -ForegroundColor Red
        Show-Help
    }
}
