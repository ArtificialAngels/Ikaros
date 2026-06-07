@echo off
REM ============================================================
REM Hermes Trace - Real-time backend log viewer
REM ============================================================
chcp 65001 >nul
powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0hermes-trace.ps1"
