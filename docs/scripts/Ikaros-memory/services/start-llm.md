# start-llm.bat — Ikaros Memory LLM 服务（记忆抽取用）

> 源文件：`Ikaros-memory/services/start-llm.bat`
> 作用：启动本地 LLM 服务（llama.cpp server），供记忆抽取 / V5 后台任务使用。

## 配置来源（resolver 模式）

模型、端口、alias 等**不再写死在脚本里**，改由
`Ikaros-memory/models/model_config.py` 统一管理：

- 首次运行若无 `Ikaros-memory/models/model_config.json`，resolver 扫描
  `Ikaros-memory/models/*.gguf`（排除 embedding 模型），自动选出默认 chat 模型
  （偏好名称含 `1.7b` 的，否则取体积最小者），并写出 `model_config.json`。
- 启动命令由 resolver 的 `--emit-bat` 动态生成，脚本只 `call` 其产物，不含任何
  指定模型名的硬编码。
- 默认 `alias=local-llm`：所有客户端（`cloud_chat` / `ikaros-repl` / `v5` 各模块）
  统一以 `model: "local-llm"` 请求，与 server 的 `--alias` 对齐。

## 行为

1. `call Ikaros-environment\ikaros-env.bat` 加载环境。
2. `python model_config.py --emit-bat > %TEMP%\ikaros_llm_launch.tmp.bat`
   生成实际启动命令行（含 `-m <模型> --host 127.0.0.1 --port 8080 -c 8192
   -ngl auto --flash-attn auto --alias local-llm --cont-batching --jinja`）。
3. `call` 临时 bat 拉起 `llama-server`，捕获返回码后删除临时文件。

## 端口与模型兜底（已于 2026-07-16 重构）

- `IKAROS_PORT_LLM` 已在 `ikaros-env.bat` 中设为 `8080`，与 watchdog / 各客户端一致。
- `IKAROS_MODEL_LLM` 故意不设：看门狗与 `start-llm.bat` 都改走 resolver，
  由 `model_config.json` 决定初始加载模型；要换模型只需改 `model_config.json`
  （或重命名模型文件触发重新扫描），无需改任何脚本。
- 模型加载信息集中在 `Ikaros-memory/models/`，首次运行自动创建 `model_config.json`。

## 关联文档

- `Ikaros-memory/models/model_config.py`（resolver 实现，含 llama-server 官方 flag 用法）
- `bin/ikaros-memory-watchdog.md`（看门狗同样走 resolver 加载本地 LLM）
