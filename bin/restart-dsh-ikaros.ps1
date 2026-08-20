# Ikaros DSH 重启器 — thin wrapper
# 收敛为 ikaros 启动器调用 (2026-08-20, see docs/ikaros-launcher-design.md)
# 自锚定 IKAROS_ROOT (不写死盘符)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path.TrimEnd('\')
& "$root\bin\ikaros-env.ps1"
& "$root\bin\ikaros.ps1" restart web @args
exit $LASTEXITCODE
