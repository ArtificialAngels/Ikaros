@echo off
REM ============================================================
REM Hermes Model Running - Live LLM backend log viewer
REM Watch llama-server's real-time reasoning: model load,
REM prompt eval, token generation, request/response.
REM ============================================================
chcp 65001 >nul
powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0hermes-model-run.ps1"
