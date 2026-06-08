@echo off
REM ============================================================
REM Hermes - Quick server verification (mock LLM)
REM
REM Boots Hermes FastAPI against a mock LLM provider, so the
REM REST surface can be smoke-tested without a real GPU.
REM Uses %HERMES_ROOT% from script location, so it works on any
REM drive letter / install path.
REM ============================================================
setlocal
set "HERMES_ROOT=%~dp0.."
set "HERMES_LLM_MOCK=1"
cd /d "%HERMES_ROOT%"
"%HERMES_ROOT%\portable-python\python.exe" -m hermes serve --host 127.0.0.1 --port 7860
endlocal
