# start-all.bat — Ikaros Memory 统一启动所有服务

> 源文件：`Ikaros-memory/services/start-all.bat`
> 作用：拉起记忆相关服务（Embedding + LLM）。

## 端口

- **Embedding**：`:8587`（`nomic-embed-text`，自动启动）
- **LLM**：`:8080`（与 Hermes Agent 的 llama-server 共享）

## 行为

1. `call Ikaros-environment\ikaros-env.bat` 加载环境（失败则 `pause` + `exit /b 1`）。
2. 启动 embedding 服务：`start "Ikaros-Embedding" /MIN start-embedding.bat`，等 3 秒。
3. 打印访问地址；提示停止方式 `taskkill /F /IM llama-server.exe`。
4. `:keepalive` 循环 `timeout /t 3600` 保活，防止窗口退出导致子进程被回收。

> 注：`.bat` 约束 — 用 `timeout`（非 `ping`）做等待；子进程用 `start /MIN` 拉起。
