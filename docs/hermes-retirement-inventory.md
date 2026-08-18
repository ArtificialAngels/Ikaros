# Hermes 底座退役/切换 · 引用点全景盘点

> 目的：Ikaros 的 agent 底座从 **hermes-agent** 切换到 **deepseek-harness (dsh)** 前，摸清所有引用点，按"服务 / 启动 / 环境 / 配置 / 前端 / 记忆 / N.E.K.O / 测试 / 文档 / 死引用"分层归档，给出处置建议。
>
> 口径：全仓库文本扫描（忽略 `runtime/`、`node_modules`、`.venv`、`venv`、`tmp/`、`logs/`、二进制与 3MB+ 文件）。
> 结果：**223 个文件、2703 处 hermes 引用**（不含 hermes 子仓库自身）。
> 扫描数据：`tmp/hermes-ref-scan.json`。相关阅读：`docs/architecture-cleanup-20260817.md`、`docs/harness-engineering-notes.md`、`core/ikaros-dsh/README.md`。

---

## 0. 现状速览（hermes 底座在 Ikaros 里到底承担什么）

| 端口 | 组件 | 代码位置 | 职责 |
|---|---|---|---|
| :8642 | Hermes gateway | `bin/hermes-gateway.py` + `runtime/hermes-agent` (`hermes_cli.main gateway run`) | 纯净 Agent 运行时，完整 tools/skills 循环 |
| :8650 | Hermes Bridge | `core/hermes-bridge/` + `bin/hermes-bridge.py` | 0 侵入包装层，对话树默认通道 |
| :9119 | Hermes Dashboard | `runtime/hermes-agent/.../web_server.py` | 管理面板（已非 LLM 网关） |
| :8088 | QwenPaw (Hermes-Paw) | `bin/hermes_paw_bridge.py` | 伪装 QwenPaw 的猫爪服务端，Hermes Agent 驱动 |
| :48920 | 对话树 hermes 模式 | `core/conversation-tree/server.py` | ikaros/hermes 双模式，走 :8650 → :8642 |
| :9100 | 控制面板 Hermes 组件卡 | `core/dashboard/server.py` | 启停/更新 Hermes（`/api/hermes/update`） |
| — | Hermes CLI | `hermes.cmd` | 便携环境包装的 hermes 命令入口 |

dsh 侧现状：`runtime/deepseek-harness-master/`（源码树）+ `runtime/dsh/`（npm 包）+ `data/dsh/profiles/`（profile）+ `core/ikaros-dsh/`（overlay：memory_v5 MCP + terminal + lsp + persona）+ `bin/start-dsh-ikaros.bat` / `restart-dsh-ikaros.ps1`。

---

## 1. 服务 / 进程层（切换时最先动的）

| 文件 | 引用数 | 角色 | 切换建议 |
|---|---|---|---|
| `core/dashboard/server.py` | 196 | 控制面板 :9100：Hermes 组件启停、`:8080/:8587` 监视、`/api/hermes/update` 更新入口、`HERMES_ROOT` 推导 | **改造**：面板组件卡从 hermes → dsh 服务组（:3080 web / headless），update 端点改 dsh 版本管理 |
| `core/conversation-tree/server.py` | 126 | 对话树 :48920：`HERMES_AGENT_URL`（默认 :8650）、`HERMES_GATEWAY_URL`（:8642）、`_stream_hermes_gateway`、ikaros/hermes 双模式 | **改造**：hermes 模式改走 dsh（或先保留 dsh 为第三模式） |
| `core/hermes-bridge/server.py` | 31 | :8650 包装层本体 | **退役**（整体删除，dsh 不需要 bridge） |
| `core/hermes-bridge/translate.py` | 18 | 原生 SSE → 对话树方言 | **退役**（随 bridge） |
| `core/hermes-bridge/inject_ikaros_paths.py` | 15 | 路径注入（搬自 hermes mcp_tool 补丁） | **退役**；其职责由 `core/env/ikaros-env.*` 承接 |
| `core/hermes-bridge/tests/*` | 22 | bridge 单测 | **退役** |
| `core/conversation-tree/rescue_tools.py` | 66 | 自救工具集：:8642/:8650/:9119 重启、hermes venv python | **改造**：改调 dsh 服务重启 |
| `bin/hermes-gateway.py` | 16 | :8642 启动器（start/status/stop，读 `data/hermes-agent/.env` 的 API_SERVER_KEY） | **退役**（由 `start-dsh-ikaros.bat` 取代） |
| `bin/hermes-bridge.py` | 13 | :8650 启动器 | **退役** |
| `bin/hermes_paw_bridge.py` | 52 | :8088 猫爪（Hermes Agent 驱动 QwenPaw 角色） | **待定**：猫爪是否改由 dsh 驱动，还是保留 hermes 一段时期 |
| `hermes.cmd` | 7 | 顶层 hermes CLI 包装 | **退役/改指向 dsh** |
| `core/dashboard/studio_update.py` | 10 | studio 更新（`HERMES_ROOT` 推导 + `bin/studio-local-update.bat`） | **改造**（若 studio 保留） |

## 2. 上游 / venv / 更新管理

| 文件 | 引用数 | 角色 | 切换建议 |
|---|---|---|---|
| `bin/hermes-update-and-patch.py` | 99 | 更新 `runtime/hermes-agent` + 重打 Ikaros 补丁（两步法，状态 `tmp/hermes-patch-state.json`） | **退役**（dsh 无源码补丁；若上游更新需求 → dsh 走 npm 版本管理） |
| `bin/rebuild-hermes-venv.bat` | 9 | 重建 hermes venv（editable install） | **退役** |
| `bin/path_resolve.py` | 9 | 定位 Ikaros 根 + 修 hermes venv editable 残留 | **改造**：保留根定位，去掉 hermes venv 分支 |
| `bin/bootstrap-venvs.py` | 28 | 便携 venv 引导（含 hermes venv 候选/回退） | **改造**：去掉 hermes venv，保留 portable-python 引导 |
| `scripts/fetch-upstreams.py` | 7 | 上游清单（hermes-agent、hermes-web-ui 条目） | **改造**：新增 dsh 条目 / 标注 hermes 已弃用 |
| `scripts/setup-native.py` | 11 | `write_hermes_config()` 生成 hermes config；paths 含 hermes | **改造**：生成 dsh profile config |
| `UPSTREAM.md` | 11 | hermes-agent / hermes-web-ui 上游表格 | **改造**：标记退役，补 dsh 上游 |
| `.gitignore` | 29 | `runtime/hermes-agent/`、`data/hermes-agent/`、`hermes/data/` 忽略规则 | **保留**（数据目录仍可能保留一段时间），新增 `data/dsh/` 若需忽略 |
| `.gitattributes` | 4 | hermes-agent / hermes-web-ui 标 vendored | **改造**：补 dsh vendor 标记 |
| `.githooks/pre-commit` | 6 | 阻止提交 Hermes-owned 脚本被改 | **改造**：改检查 dsh 相关文件 |
| `requirements.txt` | 5 | Mavis+Hermes Agent 依赖注释 | **改造**：dsh 是 npm/pnpm 侧，Python 依赖收敛 |
| `Dockerfile` / `deploy/Dockerfile` | 20+20 | Portable Hermes Agent 镜像（HERMES_HOME/CONFIG/LLM 变量） | **待定**：容器化形态是否跟随切换 |

## 3. 环境 / 路径层（`HERMES_*` 兼容变量）

| 文件 | 引用数 | 角色 | 切换建议 |
|---|---|---|---|
| `core/env/ikaros-env.bat` | 15 | 便携环境注入：`IKAROS_HERMES_AGENT/HOME`、`HERMES_*` 兼容变量、PYTHONPATH | **改造**：保留 IKAROS_*，清理/重定向 HERMES_*，新增 DSH_* |
| `core/env/ikaros-env.ps1` | 19 | 同上（PowerShell 版） | 同上 |
| `core/env/ikaros_paths.py` | 17 | 路径注册表（`hermes.*` 段 + `HERMES_*` 兼容段） | **改造**：加 `dsh.*` 段，标记 hermes 段 deprecated |
| `core/env/PATH-LAYER.md` | 30 | 30 个 IKAROS_* + 12 个 HERMES_* 兼容变量文档 | **改造** |
| `core/env/README.md` | 11 | 同上简述 | **改造** |
| `core/env/scripts/validate-paths.py` | 10 | 路径校验（HERMES_ROOT 兼容 + hermes 组件段） | **改造** |
| `core/env/scripts/detect-root.ps1` | 5 | 根探测（HERMES_ROOT + `runtime/hermes-agent` marker） | **改造**：marker 改为 dsh |
| `core/env/detect-root/src/main.rs` | 6 | Rust 版根探测（同上） | **改造** |
| `.env` | 12 | `HERMES_*` 配置（ROUTING/LOCAL_LLM/CLOUD/DASHBOARD_FILES_ROOT 等） | **改造**：dsh 用自己 profile，HERMES_* 仅留兼容 |
| `.env.example` | 1 | 示例 | 同上 |
| `data/hermes-agent/.env` | — | 32 个 IKAROS_* 同源变量 + API_SERVER_KEY + DEEPSEEK_API_KEY | **迁移**：key 迁到 dsh profile，API_SERVER_KEY 不再需要 |
| `core/memory_v5/reflect/llm_client.py` | 7 | 按 `HERMES_HOME/.env` 顺序加载 dotenv | **改造**：dsh 下改读 dsh profile 或 IKAROS 根 .env |

## 4. 配置层

| 文件 | 引用数 | 角色 | 切换建议 |
|---|---|---|---|
| `config/hermes.yaml` | 18 | Hermes 智能路由配置（name/hermes、cloud/local 路由、人格） | **退役**（dsh 用 profile + cordis overlay） |
| `config/models.yaml` | 20 | 本地模型路径（`${HERMES_ROOT}\data\models\...`） | **保留改造**：模型继续给 :8080 本地 LLM 用，占位符改 IKAROS_ROOT |
| `config/ikaros-backend.json` | — | 后端 provider（local :8080 / deepseek） | **保留**（dsh 也可消费） |
| `data/config/panel_models.json` | — | 面板模型表（8080/8587/8088） | **保留**，可加 dsh 项 |
| `data/hermes-agent/config.yaml` | — | ikaros_v5 插件激活（context.engine/memory.provider/plugins） | **退役**（插件职责由 `core/ikaros-dsh/plugins/ikaros-memory` 承接） |

## 5. 对话树前端（:48920）

| 文件 | 引用数 | 角色 | 切换建议 |
|---|---|---|---|
| `core/conversation-tree/index.html` | 29 | 前端：`agent==='hermes'` 分支归属徽标、`代理→Hermes（任务）` 菜单、CT_MODELS hermes 模式、上下文窗口元数据 | **改造**：分支归属改 dsh；或 hermes 模式改名为任务代理模式 |
| `core/conversation-tree/index.html.bak-*`（15+ 个） | ~200 | 历史备份 | **归档/删除**（不属于引用，切完一起清） |
| `data/ct-check-app.js` / `data/dc-check.js` | 34 | 生成的检查页（面板/对话树 HTML 快照） | **重新生成**或归档 |
| `bin/import-hermes-to-convtree.py` | 9 | 导入 `data/hermes-agent/.hermes_history` → 对话树 | **保留**（历史数据导入工具，一次性） |

## 6. 记忆 / 插件层

| 文件 | 引用数 | 角色 | 切换建议 |
|---|---|---|---|
| `data/hermes-agent/plugins/ikaros_v5/`（规范源 `patches/hermes/plugins/ikaros_v5/`） | — | V5 上下文引擎 + 记忆提供方（hermes 用户插件） | **退役**：职责迁 `core/ikaros-dsh/plugins/ikaros-memory`（recallMemory/writeMemory） |
| `patches/hermes/` | — | hermes 补丁规范源 | **退役** |
| `core/memory_v5/extensions/EXTENSIONS.md` | 7 | 记录 on_pre_compress 等 hermes 插件接入点 | **改造**：改述 dsh compaction seam |
| `bin/sync-wb-skills-to-hermes.py` | 12 | WorkBuddy 技能 → `data/hermes-agent/skills/` | **改造**：指向 dsh skills 目录 |
| `bin/cross-session-e2e-check.py` | 6 | 模拟 Hermes 每轮写回路径 E2E | **改造**：改为 dsh agent loop E2E |
| `core/memory_v5/tests/test_ontology_align.py` | 9 | 实体对齐（"Hermes" 作为示例实体） | **保留**（测试数据，无需改） |

## 7. N.E.K.O 侧（外部记忆导入，非底座依赖）

| 文件 | 引用数 | 角色 | 切换建议 |
|---|---|---|---|
| `apps/neko/memory/external_markdown_import.py` | 17 | 导入 OpenClaw/Hermes Markdown 记忆（`§` 分隔符、`.hermes/SOUL.md`、`memories/`） | **保留**（历史格式导入能力；dsh 侧若输出相似格式可复用） |
| `apps/neko/templates/memory_browser.html` 等 10 个文件 | 4~38 | hermes 格式导入相关 UI/路由/测试 | **保留**（功能独立于底座） |
| `apps/neko/brain/openfang_adapter.py` | — | openfang 适配（hermes 相关，2 处 harness 命中） | **待查**：确认是否依赖 hermes runtime |

## 8. 测试层

| 文件 | 引用数 | 处置 |
|---|---|---|
| `tests/test_hermes.py`、`tests/smoke_hermes_env.py` | 19+24 | **退役/重写**为 dsh smoke |
| `tests/test_tree_agent_mode.py`、`test_tree_agent_inherit.py` | 26+16 | 对话树双模式测试 → 改为 dsh 模式 |
| `tests/test_dashboard_server.py`、`smoke_webui_proxy.py`、`smoke_node_path.ps1` | 19+10+9 | 面板/代理 smoke → 改 dsh 组件 |
| `tests/__acceptance_07f.py`、`test_phase1_cleanup.py`、`test_token_compressor_integration.py` 等 | 9+9+8+… | 逐一定级 |
| `core/conversation-tree/tests/test_chat_sse*.py` | 40 | gateway SSE 解析测试 → 改 dsh SSE/wire 协议 |

## 9. 文档层（54 个 docs 文件 + 根文档，2703 处中的大头）

- 架构类：`docs/ARCHITECTURE.md`(53)、`README.md`(41)、`AGENTS.md`(23)、`docs/CHANGELOG.md`、`docs/README.md`、`docs/architecture-cleanup-20260817.md` → **全部改述 dsh 底座**
- 设计/调查类：`docs/hermes-ikaros-patches.md`(81)、`docs/ikaros-as-hermes-agent-proposal.md`(67)、`docs/hermes-agent-full-survey.md`(26)、`docs/hermes-bridge-design.md`(26)、`docs/hermes-tools-scoping.md`(20)、`docs/hermes-v5-memory-efficiency-analysis.md`(8)、`docs/hermes-update-integrity.md`(33) → **归档为历史**（标"已退役"）
- 参考对比类：`docs/ref-hermes-studio-chat.md`(37)、`docs/omnipanel-inspiration.md`(60)、`docs/pi-ikaros-evaluation.md`(24) → **保留**（参考价值，标历史）
- 历史 handoff：`data/ikaros-coordination/*`（handoff./handshake. 2026-07-*，17+14+13 处） → **归档，不动**
- 生成物：`docs/module-dependency-map.html`、`docs/architecture-overview.html`、`docs/folder-tree.html`（由 `tools/gen_architecture_html.py`(26) 生成） → **重新生成**

## 10. 死引用清单（引用了已不存在的路径——切底座时顺手清）

| 引用处 | 指向 | 状态 |
|---|---|---|
| `core/env/*` 的 `IKAROS_HERMES` / `hermes.core` | 顶层 `hermes/` Python 包目录 | ❌ 目录不存在（v3 前遗留） |
| `.env` 注释 `deps/hermes-env.bat` | `deps/` | ❌ 不存在 |
| 历史文档提及 `oldcode/` | `oldcode/` | ❌ 不存在 |
| `tools/gen_architecture_html.py` 注释 | `bin/hermes-api-server.py`（未启用） | ❌ 不存在 |
| `UPSTREAM.md` | `bin/ikaros-soul-sync.py` | ❌ 不存在（V5 每轮自同步已取代） |
| `config/models.yaml` 注释 | `bin/hermes-models.py` | ❌ 不存在 |
| `docs/scripts/bin/hermes_tts.md` | hermes_tts | 历史文档 |

---

## 11. 退役批次建议（优先级）

1. **P0 · 服务切换**：`start-dsh-ikaros.bat` 成为对话树默认通道 → 对话树 `HERMES_AGENT_URL` 指向 dsh；控制面板组件卡改 dsh 服务组。
2. **P1 · 记忆迁移**：`core/ikaros-dsh/plugins/ikaros-memory` 实现 recall/write（agent/pre-step + turn-stopping）→ 停 `ikaros_v5` hermes 插件 → 删除 `patches/hermes/`。
3. **P2 · 拆除 bridge/gateway**：停 :8650/:8642/:9119 启动器（`bin/hermes-gateway.py`、`bin/hermes-bridge.py`、`core/hermes-bridge/`）。
4. **P3 · 环境收敛**：`HERMES_*` 兼容变量标记 deprecated，env 脚本加 `DSH_*`；`config/hermes.yaml` 退役。
5. **P4 · 上游/构建**：`hermes-update-and-patch.py`、`rebuild-hermes-venv.bat`、`bootstrap-venvs.py` hermes 分支退役；`fetch-upstreams.py`/`setup-native.py`/`UPSTREAM.md`/Dockerfile 更新。
6. **P5 · 收尾**：测试重写（test_hermes/smoke_hermes_env/tree_agent_*）、文档改述、死引用清理、`.bak` 备份归档。
7. **待定项**：:8088 猫爪（QwenPaw）是否保留 hermes 驱动；N.E.K.O 外部导入格式兼容；`data/hermes-agent/` 历史数据保留期限（`.hermes_history` 导入工具保留）。

---

## 附录 A · 完整文件清单（运行相关，按引用数降序）

```
196  core/dashboard/server.py
126  core/conversation-tree/server.py
 99  bin/hermes-update-and-patch.py
 66  core/conversation-tree/rescue_tools.py
 52  bin/hermes_paw_bridge.py
 38  apps/neko/tests/unit/test_daily_external_import.py
 31  core/hermes-bridge/server.py
 30  core/conversation-tree/tests/test_chat_sse.py
 30  core/env/PATH-LAYER.md
 29  apps/neko/tests/unit/test_external_markdown_import.py
 29  core/conversation-tree/index.html
 28  bin/bootstrap-venvs.py
 26  core/dashboard/index.html
 26  data/dc-check.js
 26  tests/test_tree_agent_mode.py
 26  tools/gen_architecture_html.py
 24  tests/smoke_hermes_env.py
 20  config/models.yaml
 20  Dockerfile
 20  deploy/Dockerfile
 19  core/dashboard/index.html (另有 .bak-0804)
 19  core/env/ikaros-env.ps1
 19  tests/test_dashboard_server.py
 19  tests/test_hermes.py
 18  config/hermes.yaml
 18  core/hermes-bridge/translate.py
 17  apps/neko/memory/external_markdown_import.py
 17  core/env/ikaros_paths.py
 16  bin/hermes-gateway.py
 16  tests/test_tree_agent_inherit.py
 15  core/env/ikaros-env.bat
 15  core/hermes-bridge/inject_ikaros_paths.py
 13  bin/hermes-bridge.py
 13  core/hermes-bridge/tests/test_translate.py
 12  .env
 12  bin/sync-wb-skills-to-hermes.py
 12  docs/CHANGELOG.md
 12  docs/architecture-cleanup-20260817.md
 11  UPSTREAM.md
 11  core/env/README.md
 11  scripts/setup-native.py
 10  core/conversation-tree/tests/test_chat_sse_live.py
 10  core/dashboard/studio_update.py
 10  core/env/scripts/validate-paths.py
 10  tests/smoke_webui_proxy.py
  9  bin/import-hermes-to-convtree.py
  9  bin/path_resolve.py
  9  bin/rebuild-hermes-venv.bat
  9  core/hermes-bridge/tests/test_server.py
  9  core/memory_v5/tests/test_ontology_align.py
  9  tests/__acceptance_07f.py
  9  tests/smoke_node_path.ps1
  9  tests/test_phase1_cleanup.py
  8  data/ct-check-app.js
  8  tests/test_token_compressor_integration.py
  7  core/memory_v5/extensions/EXTENSIONS.md
  7  core/memory_v5/reflect/llm_client.py
  7  hermes.cmd
  7  scripts/fetch-upstreams.py
  6  .githooks/pre-commit
  6  bin/cross-session-e2e-check.py
  6  core/env/detect-root/src/main.rs
  6  docs/scripts/bin/hermes_tts.md
  5  core/env/scripts/detect-root.ps1
  5  requirements.txt
  (其余 <5 处引用文件见 tmp/hermes-ref-scan.json)
```

---

## 11. 面板组件改造（2026-08-18 已完成）

`core/dashboard/server.py` + `core/dashboard/index.html` 的 hermes 引用已全部清除（140 处 → 0 处代码级）：

- **组件卡**：`hermes_bridge`(:8650)、`hermes_gateway`(:8642)、`qwenpaw`(:8088) 三卡移除；新增 `dsh` 卡（:3080，markers=["dsh"]，经 `bin/start-dsh-ikaros.bat web` 拉起）。
- **全局**：`HERMES_ROOT` → `ROOT`（18 处改名）；删 `HERMES_AGENT_DIR/PATCH_SPEC/PATCH_SCRIPT` 常量。
- **build_env**：删 `IKAROS_HERMES_AGENT/HOME`、`HERMES_BIN/AGENT_CLI_PYTHON/AGENT_BRIDGE_PYTHON/AGENT_NODE/NODE/HOME/PYTHON/RUNTIME/AGENT_ROOT/TUI_DIR` 共 14 个变量；API_SERVER_KEY 改从根 `.env` 读；PYTHONPATH 去 hermes-agent 段；新增 `IKAROS_DSH_PATCH/IKAROS_DSH_PROFILE_DIR`。
- **删除**：`_sync_hermes_web_stamp`（死函数）、`_git_hermes/_hermes_git_healthy`、`_hermes_patch_present/hermes_patch_status`、`run_hermes_update_and_patch`、qwenpaw/bridge/gateway 启停函数、Cron/Kanban 管理（`_HERMES_CLI/_cron_*/_kanban_*`，改返回"已随 Hermes 底座退役"）、`/api/hermes/*` 路由、UPSTREAM_REPOS 的 hermes 条目、`_CONTENT_MARKERS` 的 hermes、`pull_repo` 的 hermes 分支、BOOT_PROFILE 的 qwenpaw。
- **前端**：FEATURED 换 dsh；删 hermes 渲染分支/事件绑定/hermesUpdate/repoNameOf 特判；herdr agent 下拉去 hermes 选项。
- **⚠️ 误删恢复**：`_ttl_cache`/`_CACHE_REGISTRY`/`_clear_status_caches` 与 `import functools as _functools` 原位于 hermes 函数块内被连带删除，已从 git 恢复（通用缓存装饰器，非 hermes 专属）。
- **保留**：`_usage_report`（读 state.db 优雅降级 ok:False）、CSS 类名 `hermes-patch/hermes-extra`（纯视觉）。
- **验证**：py_compile ✓、模块导入+组件卡校验 ✓、build_env 无 HERMES_* ✓、9100 重启后 11 组件卡 ✓、前端无 hermes_bridge/gateway/qwenpaw 残留 ✓、/api/cron|kanban 返回退役提示 ✓。

