@echo off
REM Convenience: webui-new-stop.bat -> forwards to webui-new.bat stop
call "%~dp0webui-new.bat" stop %*
exit /b %ERRORLEVEL%
