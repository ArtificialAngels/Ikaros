# fix-socks-proxy.ps1
# NOTE: 进程级隔离方案(B)已落地 —— server.py / neko-start.bat / ikaros-control.bat
# 已注入 NO_PROXY=*, 系统代理保持原样、Steam 官方 Neko 不受影响。
# 本脚本仅作「手动清理系统 socks 代理」的可选备选; 运行前请确认你确实要改系统代理。
# Remove the broken "socks=" entry from Windows system proxy (registry),
# keeping any working http=/https= proxy intact.
# This fixes: httpx "socks://127.0.0.1:8086 isn't a valid proxy: scheme socks isn't supported"
# Root cause: Windows system proxy had a socks= entry (scheme socks:// is NOT supported by httpx;
# it requires socks5://). Neko/Ikaros inherit this from the registry and fail on every HTTP call.
#
# Usage (on the host machine, admin NOT required for HKCU):
#   powershell -ExecutionPolicy Bypass -File E:\Ikaros\bin\fix-socks-proxy.ps1
# Then RESTART Neko and Ikaros processes so they re-read proxy settings.

$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"

$proxy = $null
try {
    $proxy = (Get-ItemProperty -Path $key -Name ProxyServer -ErrorAction Stop).ProxyServer
} catch {
    Write-Host "No ProxyServer value found in registry. Nothing to remove."
    exit 0
}

if ([string]::IsNullOrWhiteSpace($proxy)) {
    Write-Host "ProxyServer is empty. Nothing to remove."
    exit 0
}

Write-Host "Current ProxyServer: $proxy"

# Split on ';', drop any segment that starts with socks (socks=, socks4=, socks5=)
$parts = $proxy -split ';' | Where-Object { $_ -and $_ -notmatch '^socks' }
$newProxy = ($parts -join ';').Trim(';')

if ($newProxy -eq '') {
    # Only socks was configured -> disable system proxy entirely
    Set-ItemProperty -Path $key -Name ProxyEnable -Value 0
    Write-Host "Removed socks entry; no http/https proxy left -> system proxy DISABLED."
} else {
    Set-ItemProperty -Path $key -Name ProxyServer -Value $newProxy
    Write-Host "Removed socks entry. New ProxyServer: $newProxy"
}

# Refresh proxy settings so running apps pick it up after restart
try { rundll32.exe wininet.dll,ResetIEproxy -silent } catch { }

Write-Host ""
Write-Host "Registry updated. To apply, RESTART Neko (N.E.K.O.exe / control panel) and Ikaros"
Write-Host "(watchdog / think / Neko frontend) processes. They must re-read the proxy env at startup."
