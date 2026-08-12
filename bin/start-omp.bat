@echo off
rem start-omp.bat — 拉起搭载 V5 记忆的 omp TUI (独立窗口, 根目录自锚定)
rem 2026-08-12: omp 启动检查 bun 在 PATH, 必须显式注入
set "IKAROS_ROOT=%~dp0.."
set "PATH=%IKAROS_ROOT%\runtime\bun\bin;%IKAROS_ROOT%\runtime\node\node_modules\bun\bin;%PATH%"
cd /d "%IKAROS_ROOT%"
"%IKAROS_ROOT%\runtime\bun\bin\omp.exe" --model go-deepseek/deepseek-v4-flash
