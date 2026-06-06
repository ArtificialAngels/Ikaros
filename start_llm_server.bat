@echo off
REM Start the local LLM server in background
chcp 65001 >nul
cd /d "E:\Hermes Agent"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python local_llm_server.py --model "E:\Hermes Agent\data\models\Qwen2.5-3B-Instruct-Q4_K_M.gguf" --port 8080 --host 127.0.0.1 --name qwen2.5-3b --ctx 2048 --threads 2
