# Ikaros 作为「套在 Hermes 之上的小型智能体」解耦方案

> 状态：**已实施**（2026-08-05 完成 ikaros_v5 外置解耦，代码已 commit）
> 日期：2026-08-04
> 背景：对标参考项目 `E:\Ikaros-something\reference project\hermes-studio-main`
> 关联文档：`hermes-update-integrity.md`、`hermes-ikaros-patches.md`、`hermes-agent-full-survey.md`、`ref-hermes-studio-chat.md`

---

## 0. 一句话结论

Ikaros **已经是一个"套在 Hermes 之上的智能体"的雏形**——它的核心价值（V5 记忆、对话树、MCP、skills）全部建立在 Hermes 的**标准扩展机制**之上。唯一不干净的地方是：这些扩展的代码实体目前被物理塞进了 Hermes 子仓库内部（`core/hermes/plugins/`），并以"侵入式补丁重放"的方式维护。要把它变成 `hermes-studio` 那种**非侵入、可随上游自由更新**的形态，只需把插件外置到个人配置 + 剥离无关补丁 + 让 `core/hermes` 回归纯净上游。**可行性已用代码实证确认，能力代码一行不用改。** **→ 该解耦已于 2026-08-05 实施完成：** ikaros_v5 已外置为 Hermes 通用插件（`data/hermes-agent/plugins/ikaros_v5/`，`plugins.enabled: [ikaros_v5]`），`core/hermes` 还原纯净上游，代码已 commit；硬验证（§5）PASS。**

---

## 1. 参照物：hermes-studio 到底是什么（代码实证）

### 1.1 项目身份

`hermes-studio-main`（仓库名 `hermes-web-ui`，license BSL-1.1）是一个 **TypeScript monorepo**：

- 客户端：`packages/client`（Vue 3 + Pinia + Naive UI，Vite 构建）
- 服务端：`packages/server`（Koa + Socket.IO + SQLite，HTTP API / 鉴权 / 文件 / **Hermes runtime 集成**）
- 桌面端：`packages/desktop`（Electron 壳，bundled Python/Hermes 运行时）
- `package.json` 的 `dependencies` 中**没有任何 hermes npm 包**，也没有 hermes 源码。

### 1.2 它如何"套在 Hermes 之上"——非侵入外壳

经探查 `packages/server/src/services/hermes/` + `package.json`，集成机制是**混合模式**：

1. **拉起受管子进程**：`gateway-runner.ts` 通过 `spawn('hermes', ['gateway', 'run', '--replace'], { detached: true, env: { ...process.env, HERMES_HOME: profileDir } })` 启动 Hermes 的 gateway。
2. **HTTP 反向代理**：Web UI 把 chat/代理路由转发到 gateway 的 REST API。`config.ts` 定义 `HERMES_GATEWAY_URL` / `GATEWAY_HOST`(127.0.0.1) / `GATEWAY_PORT`(8642) / `HERMES_WEB_UI_MANAGED_GATEWAY`。
3. **运行时来源外部化**：桌面端 `packages/desktop/scripts/install-hermes.mjs` 执行 `uv/pip install hermes-agent[extras]==<version>` 把 Hermes 装进捆绑的 Python venv；Docker 以 `FROM nousresearch/hermes-agent:latest` 为基础镜像。
4. **数据目录指向**：`hermes-path.ts:detectHermesHome()` 优先级 `HERMES_HOME` → 原生安装目录；`hermes-profile.ts` 用 `active_profile` 文件管理 profile。

### 1.3 关键判定

- **无 fork、无 vendored**：仓库根无 `.gitmodules`，全仓唯一 `.py` 是 Web UI 自有的 `agent-bridge/python/*.py`（明确文档说明是"从已安装的 hermes-agent 包 import `run_agent`"）。
- Hermes 是**纯净外部依赖**，studio 通过"spawn 子进程 + HTTP 代理 + 环境变量"三件套与之交互。

> 这就是我们要对齐的目标形态：**上层壳不碰下层引擎的内部代码，只通过标准运行/配置接口对接。**

---

## 2. Ikaros 现状：侵入式包含

### 2.1 物理布局

| 组件 | 当前位置 | 性质 |
|---|---|---|
| Hermes Agent | `core/hermes`（git 子仓库，**已还原纯净上游**，无本地 diff） | 纯净上游，更新即升级 |
| ikaros_v5 插件（provider + engine） | `data/hermes-agent/plugins/ikaros_v5`（通用插件系统，`kind: standalone`，`plugins.enabled: [ikaros_v5]`） | **已外置**，经 `register()` 双注册 |
| ikaros_v5 MCP server | `core/memory_v5/mcp_server.py`（经 `data/hermes-agent/config.yaml` 的 `mcp_servers.ikaros-v5-memory` 挂载） | 已是标准 MCP |
| V5 记忆引擎 | `core/memory_v5`（Ikaros 自仓，独立 `v5.db`） | Ikaros 自身资产 |
| 对话树 / N.E.K.O / 9100 面板 | `core/conversation-tree`、`core/neko`、`bin/ikaros-control.bat` | 已是独立上层服务 |
| 个人配置 | `data/hermes-agent`（config.yaml / .env / skills / state.db / plugins） | 独立，git 碰不到 |

### 2.2 侵入点清单（均已处置）

> 解耦前 Ikaros 对 Hermes 有 7 处补丁 + 2 个内部插件目录。解耦后：

- `plugins/{context_engine,memory}/ikaros_v5`：**已外置**到 `data/hermes-agent/plugins/ikaros_v5`（通用插件系统），`core/hermes` 内已无此目录。
- `hermes_cli/web_server.py` 的 `_context_engine_options`：上游已重构为 `_discover_context_engines`（通用功能），非 Ikaros 侵入；当前 `core/hermes` 无 Ikaros 关键字残留。
- `scripts/run_tests.sh` / `run_tests_parallel.py` / `cron/scheduler.py` / `tests/cron/test_scheduler.py` / `skills/creative/tldraw-skill`：与 ikaros_v5 核心无关，**保留为 `patches/hermes/` 的 B 类重放源**（`core/hermes` 已还原为这些文件的纯净上游版本，更新时按需重放）。

### 2.3 维护机制（外置后）

- **ikaros_v5 插件**：源在 `patches/hermes/plugins/ikaros_v5/`（主仓 git 跟踪），更新脚本经 `ensure_external_plugins()` **幂等部署**到 `data/hermes-agent/plugins/ikaros_v5/`（`$HERMES_HOME`，gitignore 数据区，hermes 更新不受影响）。
- **其余 7 个补丁**：仍作为 `patches/hermes/` 的 B 类重放源（`ensure_b_class()`），因它们与 ikaros_v5 逻辑解耦、重放成本低。
- **硬验证**：`verify_ikaros_v5_runtime()` 子进程验证外置插件在 hermes 内可用（`load_memory_provider` + 通用插件系统两条原生发现链路 + 真 `initialize()` 载 V5）。2026-08-05 实测 PASS。

---

## 3. 可行性证据：ikaros_v5 插件天然可外置

直接 grep `core/hermes/plugins/memory/ikaros_v5/__init__.py` 与 `context_engine/ikaros_v5/__init__.py` 的 import：

```
# memory/ikaros_v5/__init__.py
from agent.memory_provider import MemoryProvider          # ← Hermes 公共扩展基类
from memory_v5.store import stats, search                 # ← Ikaros 自己的 V5
from memory_v5.affect import AffectState
from memory_v5.reflect.registry import make_default_scheduler, make_consolidate_op
from memory_v5.extensions.token_compressor import (...)

# context_engine/ikaros_v5/__init__.py
from agent.context_compressor import ContextCompressor   # ← Hermes 公共扩展基类
from plugins.memory.ikaros_v5 import IkarosV5MemoryProvider
```

**结论**：ikaros_v5 仅依赖 Hermes 的两个**公共扩展基类**（`MemoryProvider` / `ContextCompressor`）+ Ikaros 自己的 `memory_v5`。**零 Hermes 内部私有模块依赖** → 它满足 Hermes 插件协议，可以放在任何插件扫描目录下加载，不必须住在 `core/hermes` 里。

### 3.1 插件发现机制（实际采用的通用插件系统路线）

> Ikaros 最终**没有**走 `plugins/memory/` 子目录扫描方案，而是采用 Hermes 的**通用插件系统**（`kind: standalone` + `plugins.enabled` allow-list），更干净且规避 bundled 优先陷阱。

机制：

1. `data/hermes-agent/plugins/ikaros_v5/plugin.yaml` 声明 `kind: standalone` + `name: ikaros_v5`；
2. `data/hermes-agent/config.yaml` 的 `plugins.enabled: [ikaros_v5]` 显式 opt-in（**必须**，否则 `register()` 不被调用，context engine 无法注册）；
3. 插件 `__init__.register(ctx)` 经 `ctx.register_context_engine()` / `ctx.register_memory_provider()` **双注册**（兼容两种 PluginContext）；
4. memory provider 另由 Hermes memory 系统的 **user 目录扫描**发现（`$HERMES_HOME/plugins/ikaros_v5` → `_hermes_user_memory.ikaros_v5`）。

仍需注意的约束：
- 必须从 `core/hermes/plugins/{memory,context_engine}/` **移除** ikaros_v5（已做），否则 bundled 同名会干扰注册；
- 路径自举：插件 `__init__`/`memory_provider.py` 顶部 `sys.path.insert(0, IKAROS_MEMORY or 推导路径)`，使 `memory_v5.*` 可导入（不再依赖 hermes 启动注入 path）。

---

## 4. 解耦方案：四步达成 hermes-studio 式干净形态

### 步骤 1 — 外置 ikaros_v5 插件到个人配置（通用插件系统）

1. 从 Hermes 内部移除：`git -C core/hermes rm -r --cached plugins/memory/ikaros_v5 plugins/context_engine/ikaros_v5`（已执行，`core/hermes` 现无 ikaros_v5 目录）。
2. 落到 Ikaros 个人配置**单目录**：`data/hermes-agent/plugins/ikaros_v5/`，结构：
   - `plugin.yaml`（`kind: standalone`, `name: ikaros_v5`）
   - `context_engine.py`（`IkarosV5ContextEngine(ContextCompressor)` + `register_context_engine`）
   - `memory_provider.py`（`IkarosV5MemoryProvider(MemoryProvider)` + path 自举 `sys.path.insert`）
   - `__init__.py`（`register(ctx)` 双注册 + `hasattr` 兼容）
3. `config.yaml`：`plugins.enabled: [ikaros_v5]` + `memory.provider: ikaros_v5` + `context.engine: ikaros_v5`。

> 源取主仓 `patches/hermes/plugins/ikaros_v5`（已跟踪、可重放），更新脚本 `ensure_external_plugins()` 幂等部署，不污染 `core/hermes`。

### 步骤 2 — 插件内部 path 自举（替代启动注入）

当前 `memory_v5` 能 import，靠的是 Hermes 启动流程把 `core/memory_v5` 塞进了 `sys.path`。外置后插件独立加载，必须在插件 `__init__.py` 顶部自行注入：

```python
import os, sys
from pathlib import Path
_IKAROS_MEMORY = os.environ.get("IKAROS_MEMORY") or str(Path(__file__).resolve().parents[4] / "core" / "memory_v5")
if _IKAROS_MEMORY not in sys.path:
    sys.path.insert(0, _IKAROS_MEMORY)
```

`IKAROS_MEMORY` 已由 9100 面板 `build_env()` 注入，无需在 `.env` 写死。

### 步骤 3 — 剥离 7 个无关补丁

| 补丁 | 处理方式 |
|---|---|
| `web_server.py` 的 `_context_engine_options` | 上游已重构为 `_discover_context_engines`，**直接丢弃**，不影响 Dashboard |
| `scripts/run_tests.sh` / `run_tests_parallel.py` | 与 ikaros_v5 核心无关；如有 Windows 运行需求，在 Ikaros 侧另存脚本维护 |
| `cron/scheduler.py` / `tests/cron/test_scheduler.py` | 同上，Ikaros 侧维护或丢弃 |
| `skills/creative/tldraw-skill` | 可选，按需在 Ikaros `skills/` 维护 |

目标：`core/hermes` 的 diff vs 上游 `f5be9236e` **归零**，不再需要 `patches/hermes` 重放。

### 步骤 4 — `core/hermes` 回归纯净上游 + 更新脚本改造

- `core/hermes` 已还原为纯净上游（`git status` 空，7 个补丁文件无 Ikaros 关键字残留）；ikaros_v5 不再是 B 类目录。
- 更新脚本 `bin/hermes-update-and-patch.py` 改造：
  - 新增 `EXTERNAL_PLUGIN_SRC`（`patches/hermes/plugins/ikaros_v5`）→ `EXTERNAL_PLUGIN_DST`（`data/hermes-agent/plugins/ikaros_v5`）+ `ensure_external_plugins()` 幂等部署（注释"hermes 仓库外，更新不受影响"）；
  - `verify_ikaros_v5_runtime()` 改为走 `load_memory_provider` + 通用插件系统两条原生发现链路 + 真 `initialize()` 载 V5，作为健康自检；
  - 其余 7 个补丁仍走 `ensure_b_class()` B 类重放（与 ikaros_v5 解耦，保留 fallback）。

### 解耦后的形态

```
Ikaros 壳（9100 面板）
 ├─ data/hermes-agent/            ← 个人配置（HERMES_HOME）
 │   ├─ config.yaml              （memory.provider / context.engine = ikaros_v5 + mcp_servers.ikaros-v5-memory）
 │   ├─ plugins/memory/ikaros_v5      ← 外置的核心插件（步骤1）
 │   ├─ plugins/context_engine/ikaros_v5
 │   └─ skills/ikaros-*          ← 标准 skill
 ├─ core/memory_v5/              ← Ikaros 自仓（V5 引擎 + mcp_server.py）
 ├─ core/conversation-tree/      ← 独立上层服务（48920）
 ├─ core/neko/                   ← 独立上层服务
 └─ core/hermes/                 ← 纯净上游（git 直接跟踪 upstream，无本地 diff）
```

---

## 5. 对标验证清单（2026-08-05 实测）

外置后逐项确认（参照 `verify_ikaros_v5_runtime` 思路，针对 `data/hermes-agent/plugins/`）：

- [x] `load_memory_provider("ikaros_v5")` 返回非 None 且 `available=True`（2026-08-05 实测 PASS）
- [x] `discover_plugins()` + `get_plugin_context_engine()` 返回 `ikaros_v5` 且 `available=True`（2026-08-05 实测 PASS）
- [x] `IkarosV5MemoryProvider.initialize()` 真载入 V5（`v5.db` 可读写，2026-08-05 实测 PASS）
- [x] `config.yaml` 的 `mcp_servers.ikaros-v5-memory` 连得上 `core/memory_v5/mcp_server.py`（`v5_*` 工具可用）
- [ ] 9100 面板 `hermes` 组件 restart 后，Dashboard（9119）的 Context Engine / Memory Provider 下拉仍含 `ikaros_v5` 且为当前选中（待运行时确认）
- [ ] 对话树（48920）经 8642 gateway 调用 ikaros_v5 上下文正常（待运行时确认）
- [x] `git -C core/hermes diff` 相对上游为空（2026-08-05 实测：工作树干净）

---

## 6. 风险与边界

| 风险 | 缓解 |
|---|---|
| bundled 同名干扰注册 | 步骤 1 **必须先删 core 内 ikaros_v5**（已做）；实际走通用插件系统 `plugins.enabled` 路线，路径为 `$HERMES_HOME/plugins/ikaros_v5`（单目录，非 `memory/` 子目录） |
| 外置后 `memory_v5` import 失败 | 步骤 2 的 `sys.path` 自举；用 `IKAROS_MEMORY` 而非硬编码 |
| 剥离 7 补丁误伤核心 | 仅 `web_server/run_tests/cron/scheduler/tldraw` 与 ikaros_v5 逻辑无关；`plugins/{memory,context_engine}/ikaros_v5` 走步骤 1 外置而非删除 |
| 更新后外置插件丢失 | `ensure_external_plugins()` 幂等部署 + `verify_ikaros_v5_runtime()` 健康自检（2026-08-05 实测 PASS） |
| 9100 面板 `build_env()` 未注入 `IKAROS_MEMORY` | 已在 `server.py:145-165` 固化，无需改动 |

**不解决的问题（明确边界）**：本方案只做"解耦/外置"，不改变 V5 以 SQLite（`v5.db`）为事实源的决策，不引入图数据库迁移，不动对话树/neko 架构。

---

## 7. 工作量评估

| 阶段 | 内容 | 量级 |
|---|---|---|
| 外置 | 移动 ikaros_v5 两份 + 改 import path | 小（~半天） |
| 剥离 | 移除 7 补丁 + 清理 `patches/hermes` 重放逻辑 | 小 |
| 验证 | 跑 §5 清单 + 本地冒烟 | 中 |
| 回归 | 对话树/记忆/压缩/Dashboard 全链路 | 中 |

**核心能力代码（V5 引擎、ikaros_v5 provider/engine 逻辑、MCP、skills）一行不用改**，只是换了个被加载的物理位置。总体 **中等工作量，低风险**。

---

## 8. 索引

| 主题 | 位置 |
|---|---|
| Hermes 集成补丁全貌 | `docs/hermes-ikaros-patches.md` |
| 更新不冲掉配置/插件的两层安全 | `docs/hermes-update-integrity.md` |
| Hermes Agent 整体调研 | `docs/hermes-agent-full-survey.md` |
| hermes-studio 聊天集成参考 | `docs/ref-hermes-studio-chat.md` |
| 插件发现机制源码（通用插件系统） | `core/hermes/hermes_cli/plugins.py`（`discover_plugins` / `get_plugin_context_engine`）、`plugins_cmd.py`（`_discover_context_engines`） |
| 外置插件实体 | `data/hermes-agent/plugins/ikaros_v5/`（`plugin.yaml` / `context_engine.py` / `memory_provider.py` / `__init__.py`） |
| 外置插件源（重放） | `patches/hermes/plugins/ikaros_v5/` |
| 更新脚本（已改造） | `bin/hermes-update-and-patch.py`（`ensure_external_plugins` / `verify_ikaros_v5_runtime`） |
| 面板 env 注入 | `core/dashboard/server.py:145-165`（`build_env`）、`:1032`（`run_hermes_update_and_patch` 的 `env=` 修复） |
| 参考项目（只读） | `E:\Ikaros-something\reference project\hermes-studio-main` |
