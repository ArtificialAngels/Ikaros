@echo off
REM ============================================================
REM Hermes Console - Persistent model management shell
REM ============================================================
chcp 65001 >nul
set "HERMES_ROOT=%~dp0.."
powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0hermes-console.ps1"
