<#
.SYNOPSIS
Close all top-level windows whose title starts with "Hermes-" or equals
"Hermes Model Running" by sending WM_CLOSE. Used by hermes-stop.bat
step 6.

WHY: Send WM_CLOSE (not Stop-Process on the parent PID) so we only close
the Hermes tab/window without killing the entire WindowsTerminal process
(which may host other unrelated tabs).

Previous Get-Process | Where MainWindowTitle approach failed because the
cmd.exe subshells inside WindowsTerminal have empty MainWindowTitle —
the terminal owns the actual window. EnumWindows finds the real
HWNDs and the owning PIDs.
#>

$ErrorActionPreference = "Continue"

# WM_CLOSE message: politely ask a window to close
$WM_CLOSE = 0x0010

# Pinvoke: enumerate windows + post WM_CLOSE
$src = @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public class HermesWin {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
}
"@

if (-not ("HermesWin" -as [type])) {
    Add-Type -TypeDefinition $src
}

# Collect (HWND, title, ownerPid) for every matching window. We need a
# container that survives across the EnumWindows callback and that the
# callback scriptblock can mutate without PowerShell's method-overload
# resolution getting confused. Use a ScriptBlock that pushes to a
# module-level array via += (concat), which works in all PS versions.
$global:HermesMatches = @()

$callback = [HermesWin+EnumWindowsProc] {
    param($hWnd, $lParam)
    $len = [HermesWin]::GetWindowTextLength($hWnd)
    if ($len -gt 0) {
        $sb = New-Object System.Text.StringBuilder ($len + 1)
        [HermesWin]::GetWindowText($hWnd, $sb, $sb.Capacity) | Out-Null
        $title = $sb.ToString()
        if ($title -match '^Hermes-' -or $title -eq 'Hermes Model Running') {
            $ownerPid = 0
            [HermesWin]::GetWindowThreadProcessId($hWnd, [ref]$ownerPid) | Out-Null
            $script:HermesMatches += ,@{ HWnd = $hWnd; Title = $title; OwnerPid = $ownerPid }
        }
    }
    return $true
}

[HermesWin]::EnumWindows($callback, [IntPtr]::Zero) | Out-Null

if ($HermesMatches.Count -eq 0) {
    Write-Host "   (no Hermes windows found)"
} else {
    foreach ($m in $HermesMatches) {
        Write-Host ("   HWnd " + $m.HWnd + "  PID " + $m.OwnerPid + "  (" + $m.Title + ")")
        # PostMessage is non-blocking — returns immediately, OS dispatches WM_CLOSE
        $ok = [HermesWin]::PostMessage($m.HWnd, $WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero)
        if (-not $ok) {
            # Fallback: try SendMessage
            [HermesWin]::SendMessage($m.HWnd, $WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
        }
    }
    # Give windows a moment to close
    Start-Sleep -Milliseconds 500
}

# Clean up the global
Remove-Variable -Name HermesMatches -Scope Global -ErrorAction SilentlyContinue
