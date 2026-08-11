@echo off
REM ============================================================
REM  Retrieval regression baseline (P2, CortexFS adaptation, 2026-08-10)
REM  One-click LongMemEval 20-instance retrieval baseline.
REM  Output: data/eval/longmemeval_baseline.json
REM
REM  Usage:
REM    bin\retrieval-baseline.bat
REM    set LONGMEMEVAL_DATA=D:\path\to\longmemeval_s_cleaned.json
REM    bin\retrieval-baseline.bat
REM
REM  Sanity: nDCG between 0.3 and 0.9 is the expected range.
REM  Run this before and after retrieval changes (performance gate).
REM ============================================================
setlocal
cd /d "%~dp0.."

REM Data file: default reference copy, override with env LONGMEMEVAL_DATA
if "%LONGMEMEVAL_DATA%"=="" set "LONGMEMEVAL_DATA=E:\Ikaros-something\reference project\longmemeval_s_cleaned.json"
if not exist "%LONGMEMEVAL_DATA%" (
  echo [ERROR] LongMemEval data not found: %LONGMEMEVAL_DATA%
  echo         set LONGMEMEVAL_DATA to the correct path and retry.
  exit /b 1
)

if not exist "data\eval" mkdir "data\eval"

REM Python: prefer portable runtime, fall back to PATH python
set "PY="
if exist "runtime\portable-python\python.exe" set "PY=runtime\portable-python\python.exe"
if "%PY%"=="" set "PY=python"

echo [baseline] data : %LONGMEMEVAL_DATA%
echo [baseline] limit: 20  top_k: 10  seed: 42
"%PY%" bin\eval-longmemeval.py --data "%LONGMEMEVAL_DATA%" --limit 20
if errorlevel 1 (
  echo [ERROR] eval-longmemeval.py failed ^(exit %errorlevel%^)
  exit /b 1
)

move /y longmemeval_v5_result.json data\eval\longmemeval_baseline.json >nul
if errorlevel 1 (
  echo [ERROR] could not move result to data\eval\longmemeval_baseline.json
  exit /b 1
)
echo [OK] baseline saved -^> data\eval\longmemeval_baseline.json
endlocal
