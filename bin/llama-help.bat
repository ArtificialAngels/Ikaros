@echo off
set IKAROS_PY=%~dp0..\runtime\portable-python\python.exe
if not exist "%IKAROS_PY%" set IKAROS_PY=python
"%IKAROS_PY%" "%~dp0llama-help.py" %*
