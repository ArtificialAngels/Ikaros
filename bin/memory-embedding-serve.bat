@echo off
REM ============================================================
REM memory-embedding-serve.bat
REM
REM 启动专用 embedding llama-server (nomic-embed-text-v1.5 Q4_K_M)
REM
REM 用途: bridge-rs memory.rs 默认调用 :8080/embeddings,
REM       但主 chat llama-server :8080 没 --embeddings flag.
REM       这个 server 跑在独立端口 8587, 专门给向量检索用.
REM
REM 配置: 1.5GB embedding model, 768 维输出
REM
REM 端口: 8587 (HTTP) + 8588 (gRPC)
REM
REM 启动: 双击 或 bin/memory-embedding-serve.bat
REM 停止: Ctrl+C 或 taskkill /F /IM llama-server.exe
REM ============================================================
chcp 65001 >nul

set "MODEL=E:\Ikaros\data\models\nomic-embed-text-v1.5-q4\nomic-embed-text-v1.5.Q4_K_M.gguf"
set "LLAMA=E:\Ikaros\runtime\llama-server.exe"
set "PORT=8587"
set "HOST=127.0.0.1"
set "CTX=2048"
set "GPU_LAYERS=99"
set "ALIAS=nomic-embed-text-v1.5-q4"

if not exist "%MODEL%" (
    echo [FATAL] 模型不存在: %MODEL%
    pause
    exit /b 1
)

echo ============================================================
echo   Embedding llama-server (nomic-embed-text-v1.5 Q4_K_M)
echo   Port: %PORT%  Model: %ALIAS%  Dims: 768
echo ============================================================

"%LLAMA%" ^
    -m "%MODEL%" ^
    --host %HOST% ^
    --port %PORT% ^
    -c %CTX% ^
    -ngl %GPU_LAYERS% ^
    --embeddings ^
    --pooling mean ^
    --model-dimensions 768 ^
    --alias %ALIAS% ^
    --cont-batching ^
    --flash-attn
