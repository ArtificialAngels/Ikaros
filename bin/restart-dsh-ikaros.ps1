# Ikaros DSH 工作引擎底座 —— 重启器
# 杀旧 dsh web 进程，重新拉起（自动加载 ~/.dsh/profiles/web/cordis.patch.yml）。
#
# ⚠️ 2026-08-30 修复两个致命 bug（见 docs/v5-mcp-consolidation.md §9）:
#   1) 旧版本给 web 模式也传了 --patch。但 dsh 的加载顺序是
#      「package.json bundles -> profile 的 cordis.patch.yml -> --patch overlay」
#      (见 ~/.dsh/profiles/web/cordis.yml 顶部注释), profile 里那份已经包含了
#      memory-ikaros-v5, 再叠一份 --patch 就撞 id:
#        duplicate loader entry id: memory-ikaros-v5
#      -> dsh 直接起不来。web 模式本来就不该传 --patch, 只有 headless 需要。
#   2) 旧版本不设 IKAROS_ROOT。patch 里的路径全是 !!js 表达式
#      (process.env.IKAROS_ROOT + "..."), 环境变量缺失时静默算成
#      "undefined\runtime\portable-python\python.exe" -> spawn ENOENT,
#      MCP / 对话树 / 插件全部拉不起来。
#
# 修法: 委托给 `ikarosctl.py dsh sync|restart` —— 与 bin/ikaros.bat 走同一条
#       实现(单一真相源), 不再在 .ps1 里抄一份启动参数。
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path.TrimEnd('\')
$logDir = Join-Path $env:USERPROFILE ".dsh"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "ikaros-dsh-restart.log"
function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Out-File -FilePath $log -Append -Encoding utf8 }

# IKAROS_ROOT 不是注册表持久变量(由 bin/ikaros-env.bat 注入), 这里必须显式设,
# 否则子进程里 patch 的 !!js 路径表达式会算出 "undefined\...".
$env:IKAROS_ROOT = $root

$python = Join-Path $root "runtime\portable-python\python.exe"
$ctl    = Join-Path $root "core\ikarosctl.py"
if (-not (Test-Path $python)) { Log "ERROR: python not found: $python"; exit 1 }
if (-not (Test-Path $ctl))    { Log "ERROR: ikarosctl not found: $ctl"; exit 1 }

Log "=== restart started (PID $PID, IKAROS_ROOT=$env:IKAROS_ROOT) ==="

# 0. 前置缓冲：给调用方留出报告时间，避免立即杀宿主导致报告丢失
Start-Sleep -Seconds 5

# 1. 先把规范源的 patch 同步进 profile（web 模式靠 profile 里那份，不靠 --patch）
Log "syncing cordis.patch.yml -> profile"
& $python $ctl dsh sync 2>&1 | ForEach-Object { Log "  sync: $_" }

# 2. 重启（stop + start web；start 走 start_component('dsh', ('web',))，不传 --patch）
Log "restarting dsh web"
& $python $ctl dsh restart 2>&1 | ForEach-Object { Log "  restart: $_" }

# 3. 验证：3080 监听 + mcp_server 拉起 + 对话树端口
Start-Sleep -Seconds 20
$port = $env:IKAROS_DSH_WEB_PORT
if (-not $port) { $port = 3080 }
$l = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($l) { Log "OK: $port listening" } else { Log "WARN: $port not listening — see data/logs/" }

$mcp = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
       Where-Object { $_.CommandLine -match 'mcp_server\.py' }
if ($mcp) { Log "OK: mcp_server running (PID $($mcp.ProcessId -join ','))" }
else      { Log "WARN: mcp_server not found — 可能要等首个会话才会 spawn" }

& $python $ctl dsh status 2>&1 | ForEach-Object { Log "  status: $_" }

Log "=== restart script done ==="
