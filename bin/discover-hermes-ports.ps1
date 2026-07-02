# discover-hermes-ports.ps1
# Helper: enumerate listening ports owned by hermes_cli processes.
# Output: one line per port: <port>  <pid>  <process>  <state>
# Used by discover-hermes-ports.bat to avoid nested PowerShell quoting hell.
#
# Note: We filter to the LAST (highest-PID) serve process only. The
# first port in numeric order can be a stale entry from a crashed
# child (e.g. Python debug port 1117) that hasn't been GC'd yet by
# the kernel. Sorting by PID and taking the highest matches the
# "most recently spawned = still alive" assumption, since the kill
# cascade that takes down a child typically frees the parent's port
# *after* the child's.

$serveProcesses = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'hermes_cli\.main serve' }
if (-not $serveProcesses) { exit 1 }

$servePids = $serveProcesses | ForEach-Object { $_.ProcessId }

$rows = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $servePids -contains $_.OwningProcess } |
    Sort-Object OwningProcess, LocalPort

if (-not $rows) { exit 1 }

foreach ($r in $rows) {
    $proc = Get-Process -Id $r.OwningProcess -ErrorAction SilentlyContinue
    $name = if ($proc) { $proc.ProcessName } else { '?' }
    '{0,6}  {1,6}  {2,12}  {3}' -f $r.LocalPort, $r.OwningProcess, $name, $r.State
}
