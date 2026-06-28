@echo off
REM icarus-llama-restart.bat — convenience wrapper for the human.
REM
REM Forces llama-server to re-scan --models-dir + router-preset.ini, which
REM is the only way to make WebUI's model dropdown reflect a change to
REM data/models/*.gguf (WebUI's own "refresh cache" button only refreshes
REM cloud provider catalogs).
call "%~dp0hermes-root.bat" init >NUL
"%HERMES_PYTHON%" "%~dp0icarus-llama-restart.py" %*
