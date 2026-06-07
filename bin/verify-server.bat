@echo off
set HERMES_LLM_MOCK=1
cd /d "E:\Hermes Agent"
"E:\Hermes Agent\portable-python\python.exe" -m hermes serve --host 127.0.0.1 --port 7860
