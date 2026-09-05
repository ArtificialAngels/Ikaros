# Ikaros — Handoff Card

> **目标读者**：所有接入本项目的 AI Agent。
> **总入口**：本文档只含 agent onboarding 必须知道的硬契约 + 指针。
> **完整架构**：`docs/ARCHITECTURE.md`（~600 行，8 章）。
> **dsh 时代 5 分钟总览**：`docs/architecture-post-dsh.md`。
> **专题深度**：`docs/archive/decision-history/<name>.md`（按需下钻）。
> **时间线真相源**：`git log --oneline -30` + `docs/CHANGELOG.md`。
> **漂移守门**：`python docs/lint.py`（doc 改动后跑）。
> **Agent onboarding 路径**：`docs/README.md` §Agent onboarding 5 步。

---

## Ports (3 active)

| Port | Service | Component |
|------|---------|-----------|
| :8587 | Embedding (bge-m3-q8_0, 1024 dim, cls pooling) | `core/memory_v5/models/` + watchdog 自管 |
| :3080 | **dsh (DeepSeek Harness)** 工作引擎 web | `runtime/dsh/` + `core/ikaros-dsh/cordis.patch.yml` |
| :48920 | Conversation Tree 面板 (动态端口, 见 `tmp/ct-port.json`) | `core/conversation-tree/server.py` |

> 详细端口表 + 退役端口（:8080 Phi-4 / :9100/9119 dashboard / :7870/7871 voice bridge）见 `docs/ARCHITECTURE.md` §1.2。

## Startup

- **dsh web（Web GUI）**：`ikaros web` → http://127.0.0.1:3080
- **dsh headless（one-shot）**：`ikaros dsh headless "<task>"`（**非一级子命令**——`headless` 是 dsh 组件 `web|headless` 分支）
- **对话树**：`ikaros tree`
- **全部启动**：`ikaros all`（拓扑序 embedding → tree → dsh）
- **dsh 重启**：`ikaros dsh restart`（**会中断 :3080 Web 会话**）
- **统一入口**：`bin/ikaros.bat` 双击出 13 项菜单（components / dsh shortcuts / diagnostics / control），CLI 直接 `ikaros <subcommand>` 透传给 ikarosctl.py

## dsh 底座 overlay

- **底座 = dsh 0.1.1-rc.2**；Ikaros 定制经 `core/ikaros-dsh/cordis.patch.yml` 注入
  （memory_v5 MCP + 持久 PTY 终端 + LSP + 工作引擎 persona），0 源码侵入。
- **插件目录**：`core/ikaros-dsh/plugins/{ikaros-memory, ikaros-conversation-tree, ikaros-memory-settings}/`
  每个插件是 npm 包，源码改后**必须**：
  1. `cd <plugin> && node scripts/build.mjs`（tsc → dist/，**dist 被 .gitignore**）
  2. `cd ~/.dsh/profiles/web && pnpm remove <name> && pnpm add file:E:/Ikaros/core/ikaros-dsh/plugins/<plugin>`
     （pnpm file: 是复制非符号链接，必须 remove/add 才带新 dist）
  3. **重启 dsh**（`ikaros dsh restart`）才会加载新代码
- **改完插件必跑** `python core/ikaros-dsh/tools/plugin_sync_check.py`（sha256 比对源码 vs 已装副本；exit 1 = 不同步，会打印 `--fix-cmd`）

> 插件细节 + ikaros-memory-settings v0.2 HTTP server 模式：`docs/archive/decision-history/ikaros-dsh-plugin-architecture.md`。
> ikaros-memory plugin loop 三阶段（pre/post/maintenance）：`core/memory_v5/loop.py`。

## V5 记忆核心（最小契约）

- **Python 包**：`memory_v5`（`import memory_v5`，`sys.path` 含 `E:/Ikaros/core`）
- **唯一对外检索**：`memory_retrieval.unified_retrieve(query, scope=auto|semantic|lexical|graph|tree|temporal)`
- **MCP 工具**：58 in `legacy` / 17 in `slim`（模式 = `V5_MCP_TOOL_MODE`）
  - 切 slim 前**必跑闸门**：`python core/memory_v5/tools/slim_check.py`（exit 0 才能切）
  - 工具清单真相源 = `core/memory_v5/tools/registry.py`，**漏登记启动即 RuntimeError**
- **DB**：`core/memory_v5/data/v5/v5.db`（**文件名冻结**，外部契约）
- **Chroma**：`core/memory_v5/data/v5/chroma/ikaros_v5`（可删重建，不影响 v5.db）
- **`[dsh-only]` 隔离**：内容含此标记 = 仅 dsh 可见；外部检索须传 `include_dsh_only=false`
- **数据真相源链路**：`v5.db` (SQLite+FTS5) = 唯一真相源 / `chroma/` = 派生可重建 / JSON 状态 = 灵魂状态

> V5 架构深度 + unified_retrieve 设计取舍：见 `core/memory_v5/memory_retrieval.py` `unified_retrieve` 头注释。
> MCP 合并历史 + slim 闸门细节：`docs/archive/decision-history/v5-mcp-consolidation.md`。

## Doc-drift rule

- 改架构 / 端口 / 组件的 commit **必须同步** `docs/ARCHITECTURE.md` 与本文件，否则 commit message 带 `docs:` 前缀
- 改完跑 `python docs/lint.py` 检查残留旧路径 / 旧端口 / 旧组件名
- 专题深度评审先放 `docs/archive/decision-history/`，**不放** `docs/` 根目录
- 详见 `docs/README.md` §维护规则

## DO NOT

- ❌ Never auto-commit / auto-push without an explicit user instruction
- ❌ Never run `llama-server.exe` bare — missing CUDA env → SIGSEGV. Always via component start script
- ❌ After editing `core/conversation-tree/server.py`, restart the corresponding service
- ❌ Don't edit `runtime/` 下的上游/工具链（dsh npm 包 / portable-python / llama 二进制）——它们是拉取的依赖，定制走 `core/ikaros-dsh/` overlay 或 `bin/` 包装层
- ❌ Don't rename `v5.db` 或 `v5_*` MCP 工具前缀（外部契约）
- ❌ Don't restore 已退役组件：hermes / N.E.K.O / 9100/9119/8642/8650 控制面板 / voice bridge / herdr / omp / pi / 本地 LLM Phi-4
  （历史见 `docs/archive/decision-history/hermes-retirement-inventory.md` 等）

## Resources

- **架构骨架**：`docs/ARCHITECTURE.md`（8 章，必读 §1-§6）
- **dsh 5 分钟总览**：`docs/architecture-post-dsh.md`
- **专题深度**：`docs/archive/decision-history/`（45 份按需下钻）
- **历史报告**：`docs/archive/`（25 份 8-14 之前的）
- **4 线方案历史**：`docs/hermes-plans/`
- **4 计划 SLM skill + WorkBuddy skill**：`docs/examples/skills/`
- **环境权威源**：`bin/ikaros-env.{sh,bat,ps1}`（自锚定 IKAROS_ROOT）

## CLI

```bash
# 统一入口 (双击出菜单 / CLI 透传)
ikaros                       # 双击或无参 -> 13 项交互菜单
ikaros {web,tree,embed,all}  # 起单个组件
ikaros dsh {status,open,sync,restart,stop}
ikaros {status,ps,logs,stop,restart}
ikaros doctor                 # 诊断 (components.yaml + runtime 缺失检查)

# 兼容调用 (薄壳, 等价于 ikaros <subcommand>)
bin/start-dsh-ikaros.bat {web,headless}  # 历史入口, 已合并到 ikaros.bat
bin/restart-dsh-ikaros.ps1                # 历史入口, 已合并到 ikaros dsh restart
```
