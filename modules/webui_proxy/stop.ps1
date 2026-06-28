# modules/webui_proxy/stop.ps1 — Stop webui_proxy (Python thin proxy)
. $PSScriptRoot\..\..\deps\hermes-env.ps1

$LogDir   = Join-Path $HERMES_ROOT 'data\logs'
$pidFile  = Join-Path $LogDir 'webui_proxy-last-launch.json'

# Method 1: kill by recorded PID
if (Test-Path $pidFile) {
    $info = Get-Content $pidFile -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($info -and $info.launcher_pid) {
        Write-Host "Stopping webui_proxy launcher (pid $($info.launcher_pid))..."
        Stop-Process -Id $info.launcher_pid -Force -ErrorAction SilentlyContinue
    }
    if ($info -and $info.server_pid) {
        Write-Host "Stopping webui_proxy server (pid $($info.server_pid))..."
        Stop-Process -Id $info.server_pid -Force -ErrorAction SilentlyContinue
    }
}

# Method 2: kill any stray python that is running THIS script
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.CommandLine -match 'webui_proxy\.py'
} | ForEach-Object {
    Write-Host "Stopping stray webui_proxy (pid $($_.ProcessId))..."
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

# Method 3: close browser windows showing the webui via Win32 EnumWindows.
# Uses pure C# P/Invoke (no PowerShell callbacks) because scriptblocks
# converted to delegates fail inside EnumWindows on the UI thread.
# The C# code enumerates all visible top-level windows, matches titles
# against a regex, and sends WM_CLOSE to each match.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
using System.Text.RegularExpressions;
namespace HM {
    public static class WindowCloseTool {
        [DllImport("user32.dll", CharSet=CharSet.Auto)]
        private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool EnumWindows(EnumWindowsDelegate lpEnumFunc, IntPtr lParam);
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool IsWindowVisible(IntPtr hWnd);
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
        private delegate bool EnumWindowsDelegate(IntPtr hWnd, IntPtr lParam);
        private const uint WM_CLOSE = 0x0010;
        private static List<string> _found = new List<string>();
        private static Regex _regex = null;
        private static int _closed = 0;
        private static bool Callback(IntPtr hWnd, IntPtr lParam) {
            try { if (!IsWindowVisible(hWnd)) return true; }
            catch { return true; }
            var sb = new StringBuilder(512);
            GetWindowText(hWnd, sb, 512);
            var title = sb.ToString();
            if (title.Length > 0 && _regex != null && _regex.IsMatch(title)) {
                _found.Add(title);
                PostMessage(hWnd, WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
                _closed++;
            }
            return true;
        }
        public static string[] FindAndClose(string pattern) {
            _found = new List<string>(); _closed = 0;
            _regex = new Regex(pattern, RegexOptions.IgnoreCase);
            EnumWindows(Callback, IntPtr.Zero);
            return _found.ToArray();
        }
        public static int GetClosedCount() { return _closed; }
    }
}
'@

$pattern = '(?:localhost|127\.0\.0\.1)[:.]?864[89]|Hermes'
$found = [HM.WindowCloseTool]::FindAndClose($pattern)
$count = [HM.WindowCloseTool]::GetClosedCount()
if ($count -gt 0) {
    Write-Host "  closed $count browser window(s)"
    foreach ($t in $found) {
        Write-Host "    -> $t"
    }
} else {
    Write-Host "  no matching browser windows found"
}

exit 0
