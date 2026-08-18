# Ikaros DSH 工作引擎底座 —— 重启器
# 杀旧 dsh web 进程，用 core/ikaros-dsh/cordis.patch.yml overlay 重启。
# 独立进程运行（不依赖当前会话），杀掉 dsh 后自身不受影响。
$ErrorActionPreference = 'SilentlyContinue'
$log = 'C:\Users\PZS0X\.dsh\ikaros-dsh-restart.log'
function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Out-File -FilePath $log -Append -Encoding utf8 }

Log "=== restart started (PID $PID) ==="

# 0. 前置缓冲：给父会话留出报告时间，避免立即杀宿主导致报告丢失
Start-Sleep -Seconds 15

# 1. 杀旧 dsh（node bin.js web + npx @deepseek-ai/dsh）
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
  Where-Object { $_.CommandLine -match 'dsh.*bin\.js.*web' -or $_.CommandLine -match 'npx-cli\.js.*@deepseek-ai/dsh' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Log "killed node $($_.ProcessId)" }

Start-Sleep -Seconds 3

# 2. 用 --patch 重启（自锚定规范源）
$patch = 'E:\Ikaros\core\ikaros-dsh\cordis.patch.yml'
if (-not (Test-Path $patch)) { Log "ERROR: patch not found: $patch"; exit 1 }

Log "starting dsh web --patch $patch (cwd E:\Ikaros)"
$p = Start-Process -FilePath 'dsh' -ArgumentList @('web','--patch',$patch) -WorkingDirectory 'E:\Ikaros' -WindowStyle Hidden -PassThru -RedirectStandardOutput 'C:\Users\PZS0X\.dsh\ikaros-dsh-web.out.log' -RedirectStandardError 'C:\Users\PZS0X\.dsh\ikaros-dsh-web.err.log'
Log "started dsh PID $($p.Id)"

# 3. 验证 3080 监听 + mcp_server spawn
Start-Sleep -Seconds 10
$l = Get-NetTCPConnection -LocalPort 3080 -State Listen -ErrorAction SilentlyContinue
if ($l) { Log "OK: 3080 listening" } else { Log "WARN: 3080 not listening yet — check ikaros-dsh-web.err.log" }

$mcp = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'mcp_server\.py' -and $_.ParentProcessId -eq $p.Id }
if ($mcp) { Log "OK: mcp_server spawned under dsh (PID $($mcp.ProcessId))" } else { Log "WARN: mcp_server not yet spawned directly under dsh $($p.Id)" }

Log "=== restart script done ==="
