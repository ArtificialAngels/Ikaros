@echo off
rem start-omp.bat — thin wrapper (2026-08-20 收敛为 ikaros 启动器, see docs/ikaros-launcher-design.md)
set "IKAROS_ROOT=%~dp0.."
for %%i in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fi"
call "%IKAROS_ROOT%\bin\ikaros-env.bat"
if errorlevel 1 exit /b 1
"%IKAROS_ROOT%\bin\ikaros.bat" omp %*
exit /b %ERRORLEVEL%
