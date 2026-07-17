# start-embedding.bat — Ikaros Memory Embedding 服务

> 源文件：`Ikaros-memory/services/start-embedding.bat`
> 作用：启动本地 embedding 服务（llama.cpp server）。

## 配置

- **Model**：`nomic-embed-text-v2-moe`（768 维，MoE）
- **Port**：`:8587`

## 行为

1. `call Ikaros-environment\ikaros-env.bat` 加载环境。
2. 校验 `llama-server` 与模型文件存在，缺失则 `pause` + `exit /b 1`。
3. 启动：`"%LLAMA%" -m "%MODEL%" --host 127.0.0.1 --port 8587 -ngl auto --embedding --pooling mean`

> 注：与 `start-all.bat` 配套使用；`--pooling mean` 适配 embedding 池化。
