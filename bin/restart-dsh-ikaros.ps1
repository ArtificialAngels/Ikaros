# Ikaros DSH work-engine base -- restart helper
# Kill the existing dsh web process and re-launch it (loads
# ~/.dsh/profiles/web/cordis.patch.yml automatically).
#
# 2026-08-30: fixed two fatal bugs (see docs/v5-mcp-consolidation.md §9):
#   1) Old version passed --patch to web mode too. But dsh's load order is
#      "package.json bundles -> profile cordis.patch.yml -> --patch overlay"
#      (see ~/.dsh/profiles/web/cordis.yml top comment), the profile already
#      contains memory-ikaros-v5, and stacking --patch collides on id:
#        duplicate loader entry id: memory-ikaros-v5
#      -> dsh fails to start. web mode should never pass --patch; only headless does.
#   2) Old version didn't set IKAROS_ROOT. Patch paths are !!js expressions
#      (process.env.IKAROS_ROOT + "..."); missing env var silently resolves
#      the relative path to "undefined\..." and spawn ENOENTs, so MCP /
#      conversation-tree / plugins all fail to start.
#
# Fix: delegate to `ikarosctl.py dsh sync|restart` -- shares the same code
#      path as bin/ikaros.bat (single source of truth); no duplicated launch
#      arguments in the .ps1.
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path.TrimEnd('\')
$logDir = Join-Path $env:USERPROFILE ".dsh"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "ikaros-dsh-restart.log"
function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Out-File -FilePath $log -Append -Encoding utf8 }

# IKAROS_ROOT is not a registry-persistent variable (injected by bin/ikaros-env.bat);
# it must be set explicitly here, otherwise child processes' !!js path expressions
# in the patch resolve to "undefined\...".
$env:IKAROS_ROOT = $root

$python = Join-Path $root "runtime\portable-python\python.exe"
$ctl    = Join-Path $root "core\ikarosctl.py"
if (-not (Test-Path $python)) { Log "ERROR: python not found: $python"; exit 1 }
if (-not (Test-Path $ctl))    { Log "ERROR: ikarosctl not found: $ctl"; exit 1 }

Log "=== restart started (PID $PID, IKAROS_ROOT=$env:IKAROS_ROOT) ==="

# 0. Pre-buffer: give the caller time to report before we kill the host
Start-Sleep -Seconds 5

# 1. Sync canonical patch into profile first (web mode loads from profile, not --patch)
Log "syncing cordis.patch.yml -> profile"
& $python $ctl dsh sync 2>&1 | ForEach-Object { Log "  sync: $_" }

# 2. Restart (stop + start web; start uses start_component('dsh', ('web',)), no --patch)
Log "restarting dsh web"
& $python $ctl dsh restart 2>&1 | ForEach-Object { Log "  restart: $_" }

# 3. Verify: 3080 listen + mcp_server spawned + conversation-tree port
Start-Sleep -Seconds 20
$port = $env:IKAROS_DSH_WEB_PORT
if (-not $port) { $port = 3080 }
$l = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($l) { Log "OK: $port listening" } else { Log "WARN: $port not listening -- see data/logs/" }

$mcp = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
       Where-Object { $_.CommandLine -match 'mcp_server\.py' }
if ($mcp) { Log "OK: mcp_server running (PID $($mcp.ProcessId -join ','))" }
else      { Log "WARN: mcp_server not found -- first session spawn may be pending" }

& $python $ctl dsh status 2>&1 | ForEach-Object { Log "  status: $_" }

Log "=== restart script done ==="
