@echo off
REM ikaros-llama-restart.bat — convenience wrapper for the human.
REM
REM Forces llama-server to re-scan --models-dir, which
REM refreshes the available model list for bridge-rs.
call "%~dp0hermes-root.bat" init >NUL
"%HERMES_PYTHON%" "%~dp0icarus-llama-restart.py" %*
