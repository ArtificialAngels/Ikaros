# bin/llama-help.py — 本地 LLM (:8080) 配置查看 / 状态 / 热载入 / 停止

## 用途
`:8080` 本地 llama 服务自 2026-07-26 起改为**懒加载 / 按需**：看门狗只检测端口在不，
不主动拉起模型。本工具是查看其配置逻辑与手动控制的统一入口。

配置逻辑单一事实来源：`core/v5/models/model_config.py`（`resolve_model_config` / `server_args`），
经看门狗 `_load_model_cfg` 读取；本工具不重复定义任何启动参数，只做只读展示与控制。

## 用法
```
llama-help                 # 配置逻辑摘要 + 当前状态
llama-help --config        # 仅配置逻辑 (模型/二进制/端口/参数/环境变量覆盖)
llama-help --status        # 仅端口与 /health 状态
llama-help --hotload       # 触发热载入 (未起则 detached spawn llama-server 并等 /health 200)
llama-help --stop          # 停止本地 llama-server (:8080)
```

## 热载入链路
- `llama-help --hotload` → `ensure_local_llm()`（看门狗模块）
- agent 调用本地 LLM：`llm_client.call_llm(provider="local")` → `_call_local` → `_ensure_local_llm_loaded` → `ensure_local_llm()`
- 已起且 `/health 200` 则直接返回；否则 detached spawn（CREATE_NO_WINDOW，脱离父进程常驻）并等待就绪
- 并发保护：`data/logs/.llama-hotload.lock` 占位防重复 spawn

## 环境变量覆盖
- `IKAROS_LLAMA_SERVER`  — llama-server 二进制路径
- `IKAROS_MODEL_LLM`     — 模型 .gguf 路径
- `IKAROS_LOCAL_LLM_URL` — 服务地址（默认 http://127.0.0.1:8080）
- `IKAROS_LOCAL_LLM_ALIAS` — API 请求 model 字段（默认 local-llm）
