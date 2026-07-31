# bin/ikaros-memory-watchdog.py — 记忆服务看门狗

## 用途（原模块 docstring）
管理记忆服务（统一架构），由 `ikaros-start.bat` 第 2 步以 `--detach` 拉起：
1. **Embedding (:8587)** — nomic-embed-text，供 v4 记忆库语义搜索（看门狗主动拉起 + 巡检重启）
2. **LLM (:8080)** — 本地常规 llama 服务，**懒加载 / 按需**：看门狗只检测端口在不，**不主动拉起模型、不在启动/巡检时加载模型**；模型在 agent 首次调用本地 LLM 时由 `ensure_local_llm()` 热载入，或手动 `llama-help --hotload` 触发。

启动后：拉起 embedding；每 10 秒巡检端口，embed 死则重启、LLM 仅监测（down 不重启）；写 PID 文件，支持 `--stop` 安全停止。

## 用法
```
python bin/ikaros-memory-watchdog.py          # 启动（后台: start /B）
python bin/ikaros-memory-watchdog.py --stop   # 停止
python bin/ikaros-memory-watchdog.py --status # 状态查询
```

## 端点播报
写入 `core/memory_v5/data/endpoints.json`，供其他组件读取。

## 关键设计点（内联要点）
- **`_health_ok`**：用 `http.client` 直连 `/health`，不走 urllib（避免 launch-hidden/--detach 下 urllib 误判超时或被代理拦截）。404 = 该 build 无 `/health` → 退化为仅查端口，不误杀；503/500 = 未就绪/已坏 → 触发重启。
- **`_check_and_restart`**：用 `_service_ok`（端口 + /health）而非裸 `_port_alive`，避免僵尸监听器（端口绑了但服务崩）被误报 OK。**LLM 分支为监测专用**：只看端口/health，down 不重启、不拉模型（懒加载）。
- **`_maybe_reflect`**：每 `REFLECT_INTERVAL`（30min）跑一次 V4 反思调度（consolidate/dedup/promote/distill/reflect/cleanup），`continue_on_error=True`；`force=True` 用于冷启动立即跑一次，避免空闲一个周期。
- **LLM 懒加载**：`ensure_local_llm(timeout)` 在 `:8080` 未就绪时 detached spawn llama-server 并等 `/health 200`；带 `data/logs/.llama-hotload.lock` 防并发热载入。由 `llm_client.call_llm(provider="local")` 或 `llama-help --hotload` 调用，看门狗本身不调用。
- **PID 文件**：`data/logs/ikaros-memory-watchdog.pid`；`--stop` 发 SIGTERM + 清扫 llama-server。
- Windows 子进程用 `DETACHED_PROCESS` 脱离父控制台（避免父死子随）。
