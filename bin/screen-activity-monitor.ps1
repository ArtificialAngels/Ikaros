# See docs/scripts/bin/screen-activity-monitor.md

param(
    [ValidateSet('start','stop','status','report','log','clear')]
    [string]$Command = 'status',
    [int]$Interval = 2,
    [string]$LogPath = ""
)

# ── 配置 ──────────────────────────────────────────────────────────
$BaseDir = "$env:USERPROFILE\.screen-activity-monitor"
if (-not (Test-Path $BaseDir)) { [void](New-Item -ItemType Directory -Path $BaseDir -Force) }
if (-not $LogPath) { $LogPath = "$BaseDir\log.csv" }
$PidFile = "$BaseDir\monitor.pid"
$LockFile = "$BaseDir\monitor.lock"

# ── 一次性加载 Win32 API ─────────────────────────────────────────
$typeLoaded = [System.Management.Automation.PSTypeName]'ScreenActivityMonitor.Native'
if (-not $typeLoaded.Type) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Diagnostics;

namespace ScreenActivityMonitor {
    public static class Native {
        [DllImport("user32.dll", SetLastError=false)]
        public static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
        public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

        [DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
        public static extern int GetClassName(IntPtr hWnd, StringBuilder cls, int maxCount);

        [DllImport("user32.dll", SetLastError=true)]
        public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);

        public static string GetTitle(IntPtr h) {
            if (h == IntPtr.Zero) return "";
            var sb = new StringBuilder(1024);
            GetWindowText(h, sb, 1024);
            return sb.ToString();
        }
        public static string GetClass(IntPtr h) {
            if (h == IntPtr.Zero) return "";
            var sb = new StringBuilder(256);
            GetClassName(h, sb, 256);
            return sb.ToString();
        }
        public static uint GetPid(IntPtr h) {
            if (h == IntPtr.Zero) return 0;
            GetWindowThreadProcessId(h, out uint pid);
            return pid;
        }
    }
}
"@
}

# ── 工具函数 ──────────────────────────────────────────────────────
function Get-ActiveWindowInfo {
    $h = [ScreenActivityMonitor.Native]::GetForegroundWindow()
    $title  = [ScreenActivityMonitor.Native]::GetTitle($h)
    $cls    = [ScreenActivityMonitor.Native]::GetClass($h)
    $pid    = [ScreenActivityMonitor.Native]::GetPid($h)
    $pName  = ""
    $pPath  = ""
    if ($pid -gt 0) {
        try {
            $p = Get-Process -Id $pid -ErrorAction Stop
            $pName = $p.ProcessName
            $pPath = $p.MainModule.FileName
        } catch { $pName = "?" }
    }
    return [PSCustomObject]@{
        Timestamp   = (Get-Date -Format "HH:mm:ss.fff")
        WindowTitle = $title
        WindowClass = $cls
        ProcessId   = $pid
        ProcessName = $pName
        ProcessPath = $pPath
        Handle      = $h.ToString("X8")
    }
}

function Write-CsvLine {
    param($Info)
    $now = (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff")
    $esc = { $args[0] -replace '"','""' }
    $line = "`"$now`",`"$($esc.Invoke($Info.WindowTitle))`",`"$($esc.Invoke($Info.WindowClass))`",$($Info.ProcessId),`"$($esc.Invoke($Info.ProcessName))`",`"$($esc.Invoke($Info.ProcessPath))`",$($Info.Handle)"
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
}

function Write-Event {
    param([string]$Level, [string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts [$Level] $Message" | Add-Content -Path "$BaseDir\events.log" -Encoding UTF8
}

function Is-Running {
    if (-not (Test-Path $PidFile)) { return $false }
    $saved = (Get-Content $PidFile -Raw).Trim()
    if (-not $saved) { return $false }
    try {
        $p = Get-Process -Id ([int]$saved) -ErrorAction Stop
        # 检查进程名—防止 PID 被回收
        return $p.ProcessName -eq 'powershell' -or $p.ProcessName -eq 'pwsh'
    } catch { return $false }
}

# ── 命令: start ──────────────────────────────────────────────────
function Start-Monitor {
    if (Is-Running) {
        Write-Host "[!] 监控已在运行 (PID $(Get-Content $PidFile -Raw).Trim())" -ForegroundColor Yellow
        return
    }
    # 创建 CSV 表头（如果新文件）
    if (-not (Test-Path $LogPath)) {
        Add-Content -Path $LogPath -Value 'Timestamp,WindowTitle,WindowClass,ProcessId,ProcessName,ProcessPath,Handle' -Encoding UTF8
    }
    # 启动后台监控
    $startArgs = @"
-NoProfile -WindowStyle Hidden -File "$PSCommandPath" _daemon -Interval $Interval
"@
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'powershell.exe'
    $psi.Arguments = $startArgs
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    # 等一会儿确认启动
    Start-Sleep -Milliseconds 500
    if ($proc -and (-not $proc.HasExited)) {
        $proc.Id | Out-File -FilePath $PidFile -Encoding ASCII
        Write-Host "[OK] 后台监控已启动 (PID $($proc.Id)，间隔 ${Interval}s)" -ForegroundColor Green
        Write-Event -Level INFO -Message "监控已启动 (PID $($proc.Id), 间隔 ${Interval}s)"
    } else {
        Write-Host "[X] 启动失败" -ForegroundColor Red
    }
}

# ── 守护循环（仅由 _daemon 子进程调用） ──────────────────────────
function Daemon-Loop {
    param([int]$IntervalSec)
    # 写 PID
    $pid | Out-File -FilePath $PidFile -Encoding ASCII
    # 创建互斥锁文件
    $fs = [System.IO.File]::Open($LockFile, [System.IO.FileMode]::OpenOrCreate,
          [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::Read)
    $fs.Lock(0, [Int64]::MaxValue)
    # 编码
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    Write-Event -Level INFO "守护进程启动 (PID $pid)"
    $lastInfo = $null
    $lastHwnd = [IntPtr]::Zero
    $cooldown  = 0
    while ($true) {
        try {
            $info = Get-ActiveWindowInfo
            $h = [IntPtr]::Parse($info.Handle, [System.Globalization.NumberStyles]::HexNumber)
            # 只在窗口切换或标题变化时才记录
            if ($h -ne $lastHwnd -or $info.WindowTitle -ne $lastInfo.WindowTitle) {
                Write-CsvLine $info
                $lastInfo = $info
                $lastHwnd = $h
                $cooldown = 0
            }
            Start-Sleep -Seconds $IntervalSec
        } catch {
            Write-Event -Level ERROR "监控循环异常: $_"
            Start-Sleep -Seconds 5
        }
    }
    $fs.Unlock(0, [Int64]::MaxValue)
    $fs.Close()
}

# ── 命令: stop ───────────────────────────────────────────────────
function Stop-Monitor {
    if (-not (Is-Running)) {
        Write-Host "[i] 监控未运行" -ForegroundColor Gray
        return
    }
    $pid = (Get-Content $PidFile -Raw).Trim()
    try {
        $p = Get-Process -Id ([int]$pid) -ErrorAction Stop
        $p.Kill()
        Write-Host "[OK] 已停止监控 (PID $pid)" -ForegroundColor Green
        Write-Event -Level INFO "监控已停止 (PID $pid)"
    } catch {
        Write-Host "[!] 停止失败: $_" -ForegroundColor Yellow
    }
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
    if (Test-Path $LockFile) { Remove-Item $LockFile -Force }
}

# ── 命令: status ────────────────────────────────────────────────
function Show-Status {
    if (Is-Running) {
        $pid = (Get-Content $PidFile -Raw).Trim()
        Write-Host "● 运行中 (PID $pid)" -ForegroundColor Green
        if (Test-Path $LogPath) {
            $size = (Get-Item $LogPath).Length
            $lines = (Get-Content $LogPath | Measure-Object).Count
            $since = (Get-Date) - (Get-Item $LogPath).LastWriteTime
            Write-Host "  日志: $LogPath"
            Write-Host "  大小: {0:N1} KB, {1} 条记录" -f ($size/1KB), ($lines-1)
            Write-Host "  最新更新: {0:HH:mm:ss} ({1:N0} 秒前)" -f (Get-Item $LogPath).LastWriteTime, $since.TotalSeconds
        }
    } else {
        Write-Host "○ 未运行" -ForegroundColor Gray
    }
    # 显示最后一条记录
    if (Test-Path $LogPath) {
        $last = Get-Content $LogPath -Tail 2 | Select-Object -First 1
        if ($last -and $last -ne 'Timestamp,WindowTitle,WindowClass,ProcessId,ProcessName,ProcessPath,Handle') {
            Write-Host "最后活动: $last"
        }
    }
}

# ── 命令: report ────────────────────────────────────────────────
function Show-Report {
    if (-not (Test-Path $LogPath)) { Write-Host "[i] 暂无日志"; return }
    $today = (Get-Date -Format "yyyy-MM-dd")
    $lines = Get-Content $LogPath | Where-Object { $_ -ne 'Timestamp,WindowTitle,WindowClass,ProcessId,ProcessName,ProcessPath,Handle' -and $_ -match $today }
    if ((-not $lines) -or $lines.Count -eq 0) { Write-Host "[i] 今天暂无活动记录"; return }
    Write-Host "══════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host " 屏幕活动日报 — $today" -ForegroundColor Cyan
    Write-Host "══════════════════════════════════════════" -ForegroundColor Cyan
    # 解析
    $records = $lines | ForEach-Object {
        $parts = $_ -split ',(?=(?:[^"]*"[^"]*")*[^"]*$)'
        [PSCustomObject]@{
            Time   = if ($parts[0]) { $parts[0].Trim('"') } else { "" }
            Title  = if ($parts[1]) { $parts[1].Trim('"') } else { "" }
            PName  = if ($parts[4]) { $parts[4].Trim('"') } else { "" }
            PPid   = if ($parts[3]) { $parts[3] } else { "" }
        }
    }
    # 1. 活跃应用排行
    Write-Host "`n活跃应用排行:" -ForegroundColor Yellow
    $records | Group-Object PName | Sort-Object Count -Descending |
        Select-Object -First 10 | ForEach-Object {
            $pct = [math]::Round($_.Count / $records.Count * 100, 1)
            Write-Host "  {0,-30} {1,4} 次 ({2,5}%)" -f $_.Name, $_.Count, $pct
        }
    # 2. 窗口标题切换次数
    Write-Host "`n窗口切换次数:" -ForegroundColor Yellow
    $switchCount = ($records | Measure-Object).Count
    $firstTime  = $records | Select-Object -First 1 -ExpandProperty Time
    $lastTime   = $records | Select-Object -Last 1 -ExpandProperty Time
    Write-Host "  $switchCount 次切换"
    Write-Host "  时间段: $firstTime — $lastTime"
    # 3. 详细时间线（最近 20 条）
    Write-Host "`n最近活动时间线:" -ForegroundColor Yellow
    $records | Select-Object -Last 20 | ForEach-Object {
        $t = $_.Time
        Write-Host "  [$t] $($_.PName)`t$($_.Title)"
    }
    Write-Host "`n日志文件: $LogPath" -ForegroundColor DarkGray
}

# ── 命令: log ────────────────────────────────────────────────────
function Show-Log {
    if (-not (Test-Path $LogPath)) { Write-Host "[i] 日志文件不存在"; return }
    $content = Get-Content $LogPath -Tail 201
    $content | ForEach-Object { Write-Host $_ }
    $total = (Get-Content $LogPath | Measure-Object).Count
    Write-Host "`n--- 共 $total 条记录 ---" -ForegroundColor DarkGray
}

# ── 命令: clear ─────────────────────────────────────────────────
function Clear-Log {
    if (Is-Running) {
        Write-Host "[!] 监控正在运行，请先 stop" -ForegroundColor Red
        return
    }
    if (Test-Path $LogPath) {
        Remove-Item $LogPath -Force
        Write-Host "[OK] 日志已清除" -ForegroundColor Green
    } else {
        Write-Host "[i] 无日志可清" -ForegroundColor Gray
    }
}

# ── 入口 ──────────────────────────────────────────────────────────
switch ($Command) {
    'start'  { Start-Monitor }
    'stop'   { Stop-Monitor }
    'status' { Show-Status }
    'report' { Show-Report }
    'log'    { Show-Log }
    'clear'  { Clear-Log }
    '_daemon' { Daemon-Loop -IntervalSec $Interval }
    default  { Show-Status }
}
