@echo off
REM ============================================================
REM Hermes Console - Persistent model management shell
REM
REM The .ps1 does its own dot-source of deps\hermes-env.ps1.
REM ============================================================
chcp 65001 >nul
powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0hermes-console.ps1"
