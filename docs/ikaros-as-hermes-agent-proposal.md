# Ikaros 作为「套在 Hermes 之上的小型智能体」解耦方案

> 状态：探究 / 方案（未实施）
> 日期：2026-08-04
> 背景：对标参考项目 `E:\Ikaros-something\reference project\hermes-studio-main`
> 关联文档：`hermes-update-integrity.md`、`hermes-ikaros-patches.md`、`hermes-agent-full-survey.md`、`ref-hermes-studio-chat.md`

---

## 0. 一句话结论

Ikaros **已经是一个"套在 Hermes 之上的智能体"的雏形**——它的核心价值（V5 记忆、对话树、MCP、skills）全部建立在 Hermes 的**标准扩展机制**之上。唯一不干净的地方是：这些扩展的代码实体目前被物理塞进了 Hermes 子仓库内部（`core/hermes/plugins/`），并以"侵入式补丁重放"的方式维护。要把它变成 `hermes-studio` 那种**非侵入、可随上游自由更新**的形态，只需把插件外置到个人配置 + 剥离无关补丁 + 让 `core/hermes` 回归纯净上游。**可行性已用代码实证确认，能力代码一行不用改。**

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
| Hermes Agent | `core/hermes`（git 子仓库，HEAD=上游 `f5be9236e`） | 被 Ikaros 改过 7 处代码 |
| ikaros_v5 memory provider | `core/hermes/plugins/memory/ikaros_v5` | 塞在 Hermes 内部的 B 类目录 |
| ikaros_v5 context engine | `core/hermes/plugins/context_engine/ikaros_v5` | 同上 |
| ikaros_v5 MCP server | `core/memory_v5/mcp_server.py`（经 `data/hermes-agent/config.yaml` 的 `mcp_servers.ikaros-v5-memory` 挂载） | 已是标准 MCP |
| V5 记忆引擎 | `core/memory_v5`（Ikaros 自仓，独立 `v5.db`） | Ikaros 自身资产 |
| 对话树 / N.E.K.O / 9100 面板 | `core/conversation-tree`、`core/neko`、`bin/ikaros-control.bat` | 已是独立上层服务 |
| 个人配置 | `data/hermes-agent`（config.yaml / .env / skills / state.db） | 独立，git 碰不到 |

### 2.2 侵入点清单（7 处 Ikaros 补丁）

- `hermes_cli/web_server.py`（上游已重构 `_context_engine_options` → `_discover_context_engines`，功能等价，Dashboard 入口未断）
- `scripts/run_tests.sh`、`scripts/run_tests_parallel.py`（Windows venv 探测，与核心无关）
- `cron/scheduler.py`、`tests/cron/test_scheduler.py`（调度，与核心无关）
- `plugins/context_engine/ikaros_v5`、`plugins/memory/ikaros_v5`（**核心，但位置在 Hermes 内部**）
- `skills/creative/tldraw-skill`（可选）

### 2.3 维护机制

B 类目录（`plugins/{context_engine,memory}/ikaros_v5`）在主仓 `patches/hermes/` 有完整源；更新脚本 `bin/hermes-update-and-patch.py` 经 `git clean -fd -- B_CLASS_DIRS` 后由 `ensure_b_class()` 重放 + `verify_ikaros_v5_runtime()` 子进程硬验证（`discover_memory_providers` + `available=True` + 真 `initialize()`）兜底。

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

### 3.1 标准插件发现机制（为什么能外置）

Hermes 的 `plugins/memory/__init__.py` 的 `_iter_provider_dirs()` 扫描规则：

1. 先扫 **bundled** 目录 `core/hermes/plugins/memory/`；
2. 再扫 **user** 目录 `$HERMES_HOME/plugins/`；
3. 同名碰撞时 bundled 胜出（`if child.name in seen: continue`）。

因此外置必须满足两个条件：
- **路径正确**：放在 `$HERMES_HOME/plugins/memory/ikaros_v5`（带 `memory/` 子目录），否则扫描器根本不会去那里找；
- **无同名 bundled 竞争**：必须从 `core/hermes/plugins/memory/` 移除 ikaros_v5，否则 user 份被覆盖、重新变成死副本（这正是 2026-08-04 清理的那个 `data/hermes-agent/plugins/ikaros_v5` 影子副本踩过的坑）。

---

## 4. 解耦方案：四步达成 hermes-studio 式干净形态

### 步骤 1 — 外置 ikaros_v5 插件到个人配置

```
# 从 Hermes 内部移除
git -C core/hermes rm -r --cached plugins/memory/ikaros_v5 plugins/context_engine/ikaros_v5
rm -rf core/hermes/plugins/memory/ikaros_v5 core/hermes/plugins/context_engine/ikaros_v5

# 落到 Ikaros 个人配置（注意 memory/ 子目录）
mkdir -p data/hermes-agent/plugins/memory data/hermes-agent/plugins/context_engine
cp -r <源> data/hermes-agent/plugins/memory/ikaros_v5
cp -r <源> data/hermes-agent/plugins/context_engine/ikaros_v5
```

> 源的"干净版"优先取主仓 `patches/hermes/plugins/{memory,context_engine}/ikaros_v5`（已跟踪、可重放），而非当前 `core/hermes` 里被重放出来的那份。

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

### 步骤 4 — `core/hermes` 回归纯净上游

- 停止在更新流程里跑 `ensure_b_class()` / `verify_ikaros_v5_runtime()`（它们是 B 类目录的保险，外置后不再需要）；
- `bin/hermes-update-and-patch.py` 简化为纯"拉上游 + 应用 C 盘 HOME 修复（server.py:1032 的 `env=` 注入）"；
- 保留一个**可选**的 `verify_ikaros_v5_runtime()` 调用作为健康自检（检测 `data/hermes-agent/plugins/.../ikaros_v5` 是否 `available=True`），但不阻塞更新。

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

## 5. 对标验证清单

外置后逐项确认（参照 `verify_ikaros_v5_runtime` 思路，改为针对 `data/hermes-agent/plugins/`）：

- [ ] `discover_memory_providers()` 返回 `ikaros_v5` 且 `available=True`
- [ ] `discover_context_engines()` 返回 `ikaros_v5` 且 `available=True`
- [ ] `IkarosV5MemoryProvider.initialize()` 真载入 V5（`v5.db` 可读写）
- [ ] `config.yaml` 的 `mcp_servers.ikaros-v5-memory` 连得上 `core/memory_v5/mcp_server.py`（`v5_*` 工具可用）
- [ ] 9100 面板 `hermes` 组件 restart 后，Dashboard（9119）的 Context Engine / Memory Provider 下拉仍含 `ikaros_v5` 且为当前选中
- [ ] 对话树（48920）经 8642 gateway 调用 ikaros_v5 上下文正常
- [ ] `git -C core/hermes diff` 相对上游为空

---

## 6. 风险与边界

| 风险 | 缓解 |
|---|---|
| bundled 优先导致 data 份被覆盖成死副本 | 步骤 1 **必须先删 core 内 ikaros_v5**；路径必须带 `memory/`/`context_engine/` 子目录 |
| 外置后 `memory_v5` import 失败 | 步骤 2 的 `sys.path` 自举；用 `IKAROS_MEMORY` 而非硬编码 |
| 剥离 7 补丁误伤核心 | 仅 `web_server/run_tests/cron/scheduler/tldraw` 与 ikaros_v5 逻辑无关；`plugins/{memory,context_engine}/ikaros_v5` 走步骤 1 外置而非删除 |
| 更新脚本去掉重放后失保 | 步骤 4 保留可选 `verify_ikaros_v5_runtime()` 健康自检 |
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
| 插件发现机制源码 | `core/hermes/plugins/memory/__init__.py`（`_iter_provider_dirs`） |
| 更新脚本（待简化） | `bin/hermes-update-and-patch.py`（`ensure_b_class` / `verify_ikaros_v5_runtime`） |
| 面板 env 注入 | `core/dashboard/server.py:145-165`（`build_env`）、`:1032`（`run_hermes_update_and_patch` 的 `env=` 修复） |
| 参考项目（只读） | `E:\Ikaros-something\reference project\hermes-studio-main` |
