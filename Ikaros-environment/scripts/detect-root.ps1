# ============================================================
# detect-root.ps1 - 自动检测 IKAROS_ROOT
# ============================================================
#  从多个来源尝试解析 Ikaros 项目根目录。
#  输出: 解析到的根目录路径，或抛出错误。
#
#  用法:
#    $root = . "$PSScriptRoot\detect-root.ps1"
#    或
#    $root = & "E:\Ikaros\Ikaros-environment\scripts\detect-root.ps1"
# ============================================================

$ErrorActionPreference = "Stop"

function Find-IkarosRoot {
    # 优先级 1: 环境变量
    if ($env:IKAROS_ROOT -and (Test-Path $env:IKAROS_ROOT)) {
        $root = (Resolve-Path $env:IKAROS_ROOT).Path
        if (Test-Path "$root\portable-python\python.exe") {
            return $root
        }
    }

    # 优先级 2: HERMES_ROOT 环境变量 (兼容旧脚本)
    if ($env:HERMES_ROOT -and (Test-Path $env:HERMES_ROOT)) {
        $root = (Resolve-Path $env:HERMES_ROOT).Path
        if (Test-Path "$root\portable-python\python.exe") {
            return $root
        }
    }

    # 优先级 3: 从脚本位置推导
    # 脚本在 E:\Ikaros\Ikaros-environment\scripts\
    $scriptDir = $PSScriptRoot
    $envDir = Split-Path $scriptDir -Parent  # Ikaros-environment
    $candidate = Split-Path $envDir -Parent   # Ikaros
    if (Test-Path "$candidate\portable-python\python.exe") {
        return (Resolve-Path $candidate).Path
    }

    # 优先级 4: 从当前工作目录向上查找
    $dir = Get-Location
    while ($dir -ne $null) {
        if (Test-Path "$dir\portable-python\python.exe" -and
            Test-Path "$dir\hermes-agent" -and
            Test-Path "$dir\Ikaros-environment") {
            return (Resolve-Path $dir).Path
        }
        $dir = Split-Path $dir -Parent
    }

    # 优先级 5: 扫描盘符
    $drives = Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null }
    foreach ($drive in $drives) {
        $candidate = Join-Path $drive.Root "Ikaros"
        if (Test-Path "$candidate\portable-python\python.exe") {
            return (Resolve-Path $candidate).Path
        }
    }

    throw "无法找到 Ikaros 根目录。请设置 IKAROS_ROOT 环境变量。"
}

# 执行并输出结果
$root = Find-IkarosRoot
Write-Output $root
