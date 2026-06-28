# Hermes Agent — 项目全景(开发者视角)

> 精简版项目地图。完整记忆库见 [AGENTS.md](../AGENTS.md);用户视角见 [README.md](../README.md);速查表见 [00-速览.md](00-速览.md)。

---

## 1. 一句话定位

**Hermes Agent v3** — 便携 U 盘即插即用的 AI Agent 运行环境。便携 Python 3.12 + llama.cpp router 模式 + 多版本 CUDA(11.8/12.4/13.0)+ Node.js 23.11.1,**零系统依赖**。带完整 Web UI,可本地或云端 LLM 路由,已装 14 个技能。

---

## 2. 三端口职责(防混淆)

| Port | 进程 | 角色 |
|------|------|------|
| **8080** | llama-server | LLM 推理引擎,OpenAI 兼容 HTTP。**内部端口**,不直接对浏览器。 |
| **7860** | FastAPI bridge | RAG / embeddings / sessions / kanban / cron / workspace。**不直接打开浏览器**。 |
| **8648** | Hermes Web UI | **主入口**。Vue 3 + Koa + Socket.IO,浏览器打开 `http://localhost:8648/`。 |

---

## 3. 启动链路

```
用户双击
  └─ bin/hermes-all.bat
       └─ bin/hermes-supervisor.py          ← Python 编排器,拓扑排序
            ├─ deps/hermes-env.bat          ← 环境变量 SST 装载
            ├─ 派生 bin/hermes-watchdog.py  ← Detached 守护进程(10s 探活,30s 冷却)
            └─ 启动 modules/<name>/start.ps1
                 ├─ env_bootstrap  (tool, GPU 检测)
                 ├─ llm_engine    → :8080
                 ├─ bridge        → :7860
                 └─ webui         → :8648
```

浏览器由 **npm 包 `hermes-web-ui.mjs` 的 poll-loop 自动打开**(不是 all.bat 打开,详见 [14-维护与升级.md](14-维护与升级.md))。

---

## 4. 模块化自描述服务

每个模块 = `module.json` + `start.ps1` + `stop.ps1` + `health.ps1` 四件套。supervisor 读取 `module.json` 的 `lifecycle.start` 字段调对应 ps1。

| 模块 | 端口 | 关键代码 |
|------|------|---------|
| `env_bootstrap` | (无) | `modules/env_bootstrap/gpu_detect.py`(559 行,多版本 CUDA 自适应) |
| `llm_engine` | 8080 | `runtime/llama-server.exe` 或 `runtime/cuda/<v>/llama-server-cuda-<v>.exe` |
| `bridge-rs` | 7860 | `bridge-rs/src/main.rs`(1761 行, Rust + axum + tokio) |
| `webui` | 8648 | `runtime/node23/node.exe hermes-web-ui.mjs`(npm global 包) |
| `model_manager` | (无) | `modules/model_manager/{gguf,mirror}.py`(工具包,被 env_bootstrap 引用) |

**添加新模块的最小模板**: 复制 `modules/env_bootstrap/` → 改 `module.json` 的 `name` / `runtime.kind` → 写 `start.ps1` 用 `deps/hermes-env.ps1` 拿 env。

---

## 5. 六条核心不变式(改之前必读)

1. **路径解析单一源真理(SST)**:`bin/hermes-root.py` 四级优先级: `HERMES_ROOT env` → `.hermes-root` cache → script-location → drive scan D:..Z:。**任何新代码都应走这条路径**,不要硬编码 `E:\Hermes Agent`。
2. **CRLF 行尾强制**:`cmd.exe` 不解析 LF-only `.bat`(含空格路径会被截断)。改完后跑 `portable-python\python.exe bin\fix-eol.py --all`。Pre-commit hook 已强制。
3. **NTFS Junction 禁用**(§0.5 修复):不要用 `mklink /J` 做目录软链(绝对 reparse-point 跨盘符断链)。一律走 `runtime/*`。`deps/hermes-env.{bat,ps1}` 会自愈残留 junction。
4. **CUDA 多版本自动适配**:`runtime/cuda/{11.8,12.4,13.0}/`,driver→CUDA 映射写在 `modules/env_bootstrap/gpu_detect.py:220 driver_to_cuda_version()`。不要在 start.ps1 硬选二进制,统一调 `gpu_detect recommend`。
5. **Workspace 白名单**:`hermes/workspace.py` 信任边界 = `HERMES_ROOT`。白名单 dirs = `{data/models, data/logs, docs, tests}`,根文件 = `{README.md, AGENTS.md}`。Windows 用 `os.path.normcase` 防大小写绕过。
6. **HERMES_AGENT_CLI_PYTHON 强制 pin**:三处都要 pin(`deps/hermes-env.{bat,ps1}` + `modules/webui/start.ps1`),防止 webui 把 `HERMES_BIN` 目录当 exe spawn 出 ENOENT。

完整 gotchas 见 AGENTS.md §7。

---

## 6. 文件索引(角色 → 去哪找)

| 我想找... | 看哪个文件 |
|----------|-----------|
| **架构全景 + 修改历史** | `AGENTS.md`(1774 行,§0-§16) |
| **用户视角** | `README.md`(313 行) |
| **三端口角色速查** | `docs/00-速览.md` |
| **故障排查 / 救命命令** | `docs/15-故障排查.md`(184 行) |
| **升级 / 备份 / 迁移** | `docs/14-维护与升级.md`(174 行) |
| **镜像 / 代理配置** | `docs/附录-镜像与代理.md` |
| **主配置** | `config/hermes.yaml`(245 行) |
| **模型 profiles** | `config/models.yaml`(123 行) |
| **路径解析 SST** | `bin/hermes-root.py`(345 行) |
| **启动编排** | `bin/hermes-supervisor.py`(802 行) |
| **守护进程** | `bin/hermes-watchdog.py`(277 行) |
| **环境变量装载** | `deps/hermes-env.bat` / `.ps1` |
| **依赖清单** | `deps/manifest.json` |
| **All endpoints** | `bridge-rs/src/main.rs`(1761 行, Rust axum+tokio) |
| **路由决策引擎** | `hermes/routing.py`(7-tier) |
| **Workspace 白名单** | `hermes/workspace.py`(524 行) |
| **GPU 检测 + CUDA 安装** | `modules/env_bootstrap/gpu_detect.py`(559 行) |
| **llama.cpp 启动** | `modules/llm_engine/start.ps1`(259 行) |
| **GGUF header 解析** | `modules/model_manager/gguf.py`(126 行) |
| **镜像 / 代理** | `modules/model_manager/mirror.py`(155 行) |
| **遥测总线** | `bridge/telemetry.py`(249 行) |
| **健康注册** | `bridge/health.py`(157 行) |
| **云端 LLM 客户端** | `bridge/cloud_client.py`(410 行) |
| **E2E 测试** | `tests/test_hermes.py`(204 行) |
| **env 烟雾测试** | `tests/smoke_hermes_env.py`(146 行) |

---

## 7. 修订时间线(简版)

| 日期 | 关键修订 |
|------|---------|
| 2026-06-09 | Router 模式 refactor(替换 kill+restart 切换模型) |
| 2026-06-10 | Phase 1-13 模块化重构(全部代码迁入 `modules/<name>/`) |
| 2026-06-13 | Privacy scrub + ENOENT 修复 + 仓库改名 `hermes-agent` → `hermes-agent-portable` |
| 2026-06-13 | **Junction de-coupling (§0.5)** — 移除 4 个 NTFS junction,改走 `runtime/*` |
| 2026-06-14 | Junction sweep module manifests(§0.6)— `module.json` 路径全部对齐 |
| 2026-06-15 | Node.js 下载步骤(§0.7)+ 移除 dev source(§0.7a)+ 移除重复浏览器打开(§0.7b)+ 清理 dead data dirs(§0.7e) |
| 2026-06-15f | **提取 watchdog(§0.7f)** — `bin/hermes-watchdog.py` 独立 detached,supervisor `cmd_start` 不再 hang |

完整修订日志见 AGENTS.md §0 + §8。

---

## 8. 常见任务快速入口

| 我要做... | 看哪里 |
|----------|--------|
| **修 bug** | AGENTS.md §7(常见 gotchas)+ §10(历史调试案例) |
| **加新云端 LLM provider** | `bridge/cloud_client.py` `_PROVIDER_BASE_URLS` / `_PROVIDER_KEY_ENV` + `config/hermes.yaml` `llm.cloud` 段 + `.env` 加 key |
| **加新模块**(第 6 个自描述服务)| 复制 `modules/env_bootstrap/` 模板,改 `module.json` + `start.ps1` |
| **强制路由走本地/云端** | `bridge/cloud_client.py` + `bridge-rs/src/main.rs` `X-Hermes-Routing` header / `HERMES_ROUTING_MODE` env |
| **加新 skill** | `data/hermes-agent/skills/<category>/`(gitignored,运行时数据) |
| **改 CUDA 自适应规则** | `modules/env_bootstrap/gpu_detect.py:220 driver_to_cuda_version()` |
| **迁移到新 U 盘 / 新盘符** | 删 `.hermes-root` cache,`bin/hermes-root.py` 会重扫 D:..Z: |
| **跑测试** | `portable-python\python.exe tests\test_hermes.py` |
| **完整重置**(保留模型)| `bin/hermes-stop.bat` + `rd /S /Q data\{memory,knowledge,logs,hermes-agent,webui,...}` |
| **离线模式** | `config/hermes.yaml` `behavior.offline_mode.disable_tools: [web-search, http-fetch, browser]` |

---

## 9. 上游集成模式(零分叉)

- `hermes-agent/`(116 MB,只读副本,NousResearch v0.16.0)提供 `AIAgent`、`hermes_cli`、cron、kanban、plugins、gateway、ACP adapter、tui_gateway
- `runtime/node23/node_modules/hermes-web-ui/`(npm global,EKKOLearnAI v0.6.x)提供 Web UI
- **我们只做**:`bridge-rs/src/main.rs` Rust reverse proxy + `bridge/cloud_client.py` cloud fallback + `bridge/sitecustomize.py` Windows-only monkey-patches + 编排层 + 配置注入(`HERMES_HOME=data/hermes-agent`)
- 真功能通过上游 import 复用:`from run_agent import AIAgent` / `from hermes_cli.main import main` / `from hermes_state import SessionDB`
- 改上游 bug → PR 上游,本地写 sitecustomize.py monkey-patch 兜底