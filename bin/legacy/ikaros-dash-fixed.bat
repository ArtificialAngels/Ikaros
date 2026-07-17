@echo off
set HERMES_DASHBOARD_SESSION_TOKEN=ikaros-fixed-token
"%~dp0..\hermes-agent\venv\Scripts\hermes.exe" dashboard --skip-build --port 9119 %*
