@echo off
REM ============================================================
REM memory-writer-llm-serve.bat
REM
REM 启动专用归约 llama-server (DeepSeek-R1-Distill-Qwen-1.5B Q4_K_M)
REM
REM 用途: bridge-rs/memory_writer.rs 的 reduce_to_fact 调 :8588
REM       把用户对话归约为结构化事实 JSON (写入 mem0)
REM
REM 端口: 8588 (跟 :8080 chat / :8587 embedding 分离)
REM
REM 模型: ~1.04 GB DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf
REM       (架构 qwen2, 上下文 131K, 训练用 R1 distillation)
REM
REM 启动: 双击 或 bin/memory-writer-llm-serve.bat
REM 停止: Ctrl+C 或 taskkill /F /IM llama-server.exe
REM ============================================================
chcp 65001 >nul

set "MODEL=E:\Ikaros\data\models\deepseek-r1-distill-qwen-1.5b-q4\DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf"
set "LLAMA=E:\Ikaros\runtime\llama-server.exe"
set "PORT=8589"
set "HOST=127.0.0.1"
set "CTX=8192"
set "GPU_LAYERS=99"
set "ALIAS=DeepSeek-R1-Distill-Qwen-1.5B-q4"

if not exist "%MODEL%" (
    echo [FATAL] 模型不存在: %MODEL%
    pause
    exit /b 1
)

echo ============================================================
echo   Memory Writer llama-server (DeepSeek-R1-Distill-Qwen-1.5B Q4_K_M)
echo   Port: %PORT%  Model: %ALIAS%  Context: %CTX%
echo   用途: bridge-rs/memory_writer.rs 调归约
echo ============================================================

"%LLAMA%" ^
    -m "%MODEL%" ^
    --host %HOST% ^
    --port %PORT% ^
    -c %CTX% ^
    -ngl %GPU_LAYERS% ^
    --jinja ^
    --alias %ALIAS% ^
    --cont-batching ^
    --flash-attn auto
