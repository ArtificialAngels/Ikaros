# Ikaros DSH 工作引擎底座 —— 重启器
# 杀旧 dsh web 进程，用 core/ikaros-dsh/cordis.patch.yml overlay 重启。
# 自锚定 IKAROS_ROOT (不写死盘符)，与 start-dsh-ikaros.bat 使用同一 node bin.js 入口。
$ErrorActionPreference = 'SilentlyContinue'
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path.TrimEnd('\')
$logDir = Join-Path $env:USERPROFILE ".dsh"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "ikaros-dsh-restart.log"
function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Out-File -FilePath $log -Append -Encoding utf8 }

Log "=== restart started (PID $PID) ==="

# 0. 前置缓冲：给父会话留出报告时间，避免立即杀宿主导致报告丢失
Start-Sleep -Seconds 15

# 1. 杀旧 dsh（node bin.js web + npx @deepseek-ai/dsh）
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
  Where-Object { $_.CommandLine -match 'dsh.*bin\.js.*web' -or $_.CommandLine -match 'npx-cli\.js.*@deepseek-ai/dsh' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Log "killed node $($_.ProcessId)" }

Start-Sleep -Seconds 3

# 2. 用 --patch 重启（自锚定规范源，与 start-dsh-ikaros.bat 一致）
$patch = Join-Path $root "core\ikaros-dsh\cordis.patch.yml"
if (-not (Test-Path $patch)) { Log "ERROR: patch not found: $patch"; exit 1 }

$node = Join-Path $root "runtime\node\node.exe"
$dshBin = Join-Path $root "runtime\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js"
if (-not (Test-Path $node)) { Log "ERROR: node not found: $node"; exit 1 }
if (-not (Test-Path $dshBin)) { Log "ERROR: dsh not found: $dshBin"; exit 1 }

Log "starting dsh web --patch $patch (cwd $root)"
$p = Start-Process -FilePath $node -ArgumentList @($dshBin,'web','--patch',$patch) -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "ikaros-dsh-web.out.log") -RedirectStandardError (Join-Path $logDir "ikaros-dsh-web.err.log")
Log "started dsh PID $($p.Id)"

# 3. 验证 3080 监听 + mcp_server spawn
Start-Sleep -Seconds 10
$l = Get-NetTCPConnection -LocalPort 3080 -State Listen -ErrorAction SilentlyContinue
if ($l) { Log "OK: 3080 listening" } else { Log "WARN: 3080 not listening yet — check ikaros-dsh-web.err.log" }

$mcp = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'mcp_server\.py' -and $_.ParentProcessId -eq $p.Id }
if ($mcp) { Log "OK: mcp_server spawned under dsh (PID $($mcp.ProcessId))" } else { Log "WARN: mcp_server not yet spawned directly under dsh $($p.Id)" }

Log "=== restart script done ==="
