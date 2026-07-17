# bin/ikaros-memory-watchdog.py — 记忆服务看门狗

## 用途（原模块 docstring）
管理记忆服务（统一架构），由 `ikaros-start.bat` 第 2 步以 `--detach` 拉起：
1. **Embedding (:8587)** — nomic-embed-text，供 v4 记忆库语义搜索
2. **LLM (:8080)** — local LLM，供 V5 后台节 token 任务（reflect/compress/think）

启动后：启动 embedding + LLM 服务；每 10 秒巡检端口，死则重启；写 PID 文件，支持 `--stop` 安全停止。

## 用法
```
python bin/ikaros-memory-watchdog.py          # 启动（后台: start /B）
python bin/ikaros-memory-watchdog.py --stop   # 停止
python bin/ikaros-memory-watchdog.py --status # 状态查询
```

## 端点播报
写入 `data/Ikaros-memory/endpoints.json`，供其他组件读取。

## 关键设计点（内联要点）
- **`_health_ok`**：用 `http.client` 直连 `/health`，不走 urllib（避免 launch-hidden/--detach 下 urllib 误判超时或被代理拦截）。404 = 该 build 无 `/health` → 退化为仅查端口，不误杀；503/500 = 未就绪/已坏 → 触发重启。
- **`_check_and_restart`**：用 `_service_ok`（端口 + /health）而非裸 `_port_alive`，避免僵尸监听器（端口绑了但服务崩）被误报 OK。
- **`_maybe_reflect`**：每 `REFLECT_INTERVAL`（30min）跑一次 V4 反思调度（consolidate/dedup/promote/distill/reflect/cleanup），`continue_on_error=True`；`force=True` 用于冷启动立即跑一次，避免空闲一个周期。
- **SKIP_LLM**：`--no-llm` 时设 `IKAROS_SKIP_LLM`，不启动也不重启 :8080。
- **PID 文件**：`data/logs/ikaros-memory-watchdog.pid`；`--stop` 发 SIGTERM + 清扫 llama-server。
- Windows 子进程用 `DETACHED_PROCESS` 脱离父控制台（避免父死子随）。
