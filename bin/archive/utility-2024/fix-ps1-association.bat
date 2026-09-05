@echo off
rem fix-ps1-association.bat -- 把 .ps1 关联回 Windows PowerShell
rem
rem 问题: 你的 .ps1 文件关联被改成了 notepad (你可能装过第三方编辑器
rem 改过 HKEY_CLASSES_ROOT\\.ps1). cmd /c "x.ps1" 时不调 powershell 解释器
rem 而开 notepad -- 你看到"选任何数字都用记事本打开.ps1".
rem
rem 修法 (HKCU 写注册表, 不需管理员):
rem   1. HKCU\\Software\\Classes\\.ps1 = "Microsoft.PowerShellScript.1"
rem   2. HKCU\\Software\\Classes\\.ps1\\OpenWithProgids 加 PowerShell ProgId
rem   3. HKCU\\Software\\Classes\\Applications\\powershell.exe\\shell\\open\\command
rem      = "\"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe\" \"%1\" %*"
rem   4. 发 SHCNE_ASSOCCHANGED 让 explorer 刷新缓存
rem
rem 注意: 本 bat 改的只是 HKCU (用户层), 不会影响其他用户.
rem 若想改机器层 (HKLM) 需管理员, 用本 bat 时加 "as Administrator" 右键.

setlocal EnableExtensions

echo ============================================================
echo   Fix .ps1 association on user level (HKCU)
echo ============================================================

:: 1. HKCU\\.ps1 -> Microsoft.PowerShellScript.1
reg add "HKCU\Software\Classes\.ps1" /ve /d "Microsoft.PowerShellScript.1" /f >nul
if errorlevel 1 (
    echo [FAIL] reg add HKCU\\.ps1
    exit /b 1
)
echo [OK] HKCU\\.ps1 = Microsoft.PowerShellScript.1

:: 2. OpenWithProgids
reg add "HKCU\Software\Classes\.ps1\OpenWithProgids\Microsoft.PowerShellScript.1" /ve /d "" /f >nul
if errorlevel 1 (
    echo [FAIL] reg add OpenWithProgids
    exit /b 1
)
echo [OK] HKCU\\.ps1\\OpenWithProgids = Microsoft.PowerShellScript.1

:: 3. applications\\powershell.exe\\shell\\open\\command
set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
reg add "HKCU\Software\Classes\Applications\powershell.exe\shell\open\command" /ve /d "\"%PS_EXE%\" \"%%1\" %%*" /f >nul
if errorlevel 1 (
    echo [FAIL] reg add powershell.exe shell\\open\\command
    exit /b 1
)
echo [OK] HKCU\\Applications\\powershell.exe\\shell\\open\\command = "%PS_EXE%" "%%1" %%*

:: 4. 发 SHCNE_ASSOCCHANGED 让 explorer 刷新关联缓存
powershell -NoProfile -Command "Add-Type -Namespace Win32 -Name Shell -MemberDefinition '[DllImport(\"shell32.dll\", CharSet=CharSet.Auto, SetLastError=true)] public static extern void SHChangeNotify(uint wEventId, uint uFlags, IntPtr dwItem1, IntPtr dwItem2);'; [Win32.Shell]::SHChangeNotify(0x08000000, 0x0000FFFF, [IntPtr]::Zero, [IntPtr]::Zero); 'OK SHCNE_ASSOCCHANGED fired'"
if errorlevel 1 (
    echo [WARN] SHChangeNotify failed (关联可能需重启 explorer)
)

echo.
echo ============================================================
echo   Verify (HKCU):
reg query "HKCU\Software\Classes\.ps1" /ve
echo.
reg query "HKCU\Software\Classes\Applications\powershell.exe\shell\open\command" /ve
echo ============================================================
echo.
echo [NOTE] 关联已写入 HKCU. explorer 缓存可能未立即刷新.
echo       如仍弹记事本, 重启 explorer.exe 或注销重登:
echo         powershell -Command "Stop-Process -Name explorer -Force; explorer &"
echo.
echo [NOTE] 本 bat 不需管理员权限 (写 HKCU 即可).
echo.
pause
exit /b 0
