# Changelog

> **注意**：下文中的 `Ikaros-memory/` 等旧目录名反映条目撰写时的项目结构。
> 2026-07-24 统一规范化后映射为 `core/v5/`，2026-07-26 进一步**重命名为 `core/memory_v5/`**（包名 `memory_v5`；`v5.db` 文件名与 `v5_*` 工具名作为契约保留）。
> Hermes Agent 已从仓库根的 `hermes-agent/` **搬迁至 `core/hermes/`**（venv 同步迁移）。 (2026-07-27 校正)
> 已移除组件：`:7870`/`:7871` 语音桥、`think.py` 自循环、Persona Sync、Studio 桌面端。**`:8642` Hermes API 网关已重新启用**（由 `hermes_cli.main gateway run` 提供，dashboard + chat-tree 复用；2026-07-31 校正）。
> 详见 [ARCHITECTURE.md](ARCHITECTURE.md#35-目录映射旧--新) 与 [naming.md](naming.md)。

---

## [2026-08-01] — 9100 面板：自我思考卡片对齐真实数据 + 整页自由画布

- **自我思考卡片（替代"内心独白"）**：9100 面板原「内心独白」读 `pending_thought.json`（磁盘不存在、无写入者）已废弃；现改读 metacog 真实产出的 `latest_thought.json`，经 `core/dashboard/server.py` 的 `/api/state` 暴露 `state.thought`；`index.html` 新增「自我思考」卡片（text + kind + 好奇度 + 时间，3s 轮询）。metacog 模块本体保留（事件驱动，详见 ARCHITECTURE §1.3.1）。
- **整页统一自由画布**：`index.html` 把原 `.main` 左右分栏（grid-col / side / dash-strip）重构为单个 `#canvas` 绝对定位画布；新增 `PanelManager`（Pointer Events 统一鼠标+触摸）实现：标题拖拽移动、八向 `.rz` 手柄缩放、拖拽/缩放时吸附对齐容器边与其他面板（8px 阈值 + 高亮参考线）、`localStorage['ikaros-control-panels']` 记忆布局、刷新自动恢复；「重置布局」清记忆恢复默认。`dashboard.css` 同步新增画布/手柄/吸附线样式与 ≤720px 移动端降级（堆叠流、隐藏手柄）。纯前端改动，`server.py` 无需改动。
- **相关文档同步**：ARCHITECTURE §1.3.1 新增面板布局与 self-thinking 说明；hermes-agent-full-survey 第 70 行"每 45min 内心独白循环"更正为事件驱动（无定时循环）。
- 改动文件：`core/dashboard/index.html`、`core/dashboard/assets/dashboard.css`（均未提交，待主仓 commit；hermes/neko 照例不碰）。

## [2026-07-31] — 修订 :8642 约定冲突（文档对齐现实）

- **Hermes API 网关（:8642）重新启用确认**：`:8642` 实际在跑，由 `python -m hermes_cli.main gateway run` 提供（**非**旧的 `bin/hermes-api-server.py` 脚本），被 dashboard（:9119）与对话树面板（:48920，chat 的 hermes 模式走其 agent runtime）复用。此前文档 / lint 误标为"已移除 / 勿加回"。
- `docs/lint.py`：将 `:8642` 从 `DELETED_PORTS` 移除（:8642 确为在用端口，由 `hermes_cli.main gateway run` 提供）。`bin/hermes-api-server.py` 仍是未启用的遗留脚本，保持不在 `DELETED_FILES`（避免 lint 自检误报），但文档不再称其为"在用"。
- 修订 `AGENTS.md` / `docs/naming.md` / `docs/ARCHITECTURE.md` / `docs/CHANGELOG.md` / `UPSTREAM.md` 与架构图生成器（`tools/gen_architecture_html.py` + 两份生成 HTML），将 `:8642` 由"已删除"改为"在用（请勿删除）"，`:7870`/`:7871` 语音桥维持"已删除"。

## [2026-07-27] — 架构重构收口 + 文档纠偏

### Architecture / 重命名
- `core/v5/` → **`core/memory_v5/`**（包名 `memory_v5`）；`hermes-agent/` → **`core/hermes/`**，venv 同步迁移并修复符号链接深度。 (2026-07-27 校正)
- 边界决策：**apps/neko 保持独立完整**，取消「neko 记忆合并进 V5」计划（删除 `bin/migrate-neko-to-v5.py` + `docs/memory-replacement-plan.md` + `docs/memory-server-proxy-plan.md`）；neko 不引入 `memory_v5` 依赖、数据不回写 V5。

### Docs
- 新增三份交互式架构图（`docs/`）：架构全景图、文件夹层级图、`module-dependency-map.html`（模块依赖）。
- `docs/README.md` / `docs/scripts/README.md` 导航链接修正（`core/v5` → `core-v5`，`Ikaros-memory`/`Ikaros-environment` → `core/memory_v5`/`core/env`）。 (2026-07-27 校正)
- 修复散布在会话流/规格文档中的 `core/v5`、`:8642`/`:7870`/`:7871`、已删除 `think.py` 等过时引用（历史性报告保留原路径以追溯，但加注当前映射）。
- `neko-deep-analysis.md` 加 2026-07-27 状态横幅：「迁移到 V5 建议已作废」。

### 维护约定
- 文档防漂移：`python docs/lint.py` 检查 `core/v5` / 已删文件 `think.py` / 已删端口 `:7870` `:7871`。（`:8642` Hermes 网关已重新启用，2026-07-31 从删除端口移除。） (2026-07-31 校正)

Ikaros 项目升级日志。格式参考 [Keep a Changelog](https://keepachangelog.com/)。

---

## [2026-07-21] — V5 意识工厂持久化治理 + 意图驱动思考 + 控制面板整栈重写

> 主体 commit：`61e009e`（V5 治理层 + think 改造 + 控制面板重写 + hermes-studio 升级）
> 本次补交：三个工具目录（`.cad_mcp/`、`.codebase-memory/`、`.ikaros-patches/`）+ 本文件

### Added
- **V5 持久化治理层** `Ikaros-memory/v5/supervisor_persist.py`（纯标准库，无循环依赖）
  - 状态机 `RUNNING / IDLE / PAUSED / TRIPPED / STOPPED`，原子写 `data/supervisor/state.json`
  - `mission.md` 机器可验证完成契约（OpenHarness 风格：完成条件 / 边界 / 执行周期）
  - `heartbeat.md` 跨会话断点 + `latest-status.txt` 心跳广播（strict-agent-loop 风格）
  - 连续失败 ≥3 次触发熔断器，停写 LLM 直到外部 reset
- **运行锁** `_SKIP` 哨兵：防止单轮超时后后台 `metacog.cycle` 仍在跑时，下一轮重复提交造成重叠
- 本 `docs/CHANGELOG.md`

### Changed
- **think 循环：固定 15min → 意图驱动**（借鉴 Reverie 潜意识意图 + strict-agent-loop 可靠性内核）
  - `_should_deep_think()`：潜意识意图分（新记忆 +0.4 / 情感显著变化 +0.3 / 好奇心高 +0.2 / 待办到期 +0.3，≥0.5 才深度思考；超 30min 软上限强制触发防饿死）
  - `_deep_think_once()`：单轮 **120s 硬超时**（`concurrent.futures`）
  - `SIGTERM / SIGINT` 优雅退出写 `STOPPED` 断点续跑信号
- **hermes-studio** `0.6.30 → 0.6.31`
- **控制面板整栈重写**：启动器由 Rust `ikaros.exe` 切换为 `bin/ikaros-control.bat` + dashboard server；`think` 成为**可独立重启的组件**（早期未提交部分一并纳入）
- V5 向量闭环（data/v4 → data/v5）、ThirdSpace 桥接等前期累积改动一并提交

### Fixed
- **think.schedule() 致命 `NameError`**：`sp` / `poll_sec` 等闭包作用域变量原本定义在 `_unified_loop` 内部，却在该函数外被 `logger.info(...)` 引用，导致整个自主思考循环一启动就崩溃 → 提到 `schedule()` 作用域
- **signal 注册位置错误**：原写在 daemon 线程内（Python 仅主线程可注册信号），优雅停止实际从未生效 → 移到 `schedule()` 主线程
- 加固 `get_scheduler()._items` 私有属性访问 → `getattr(..., [])` 兜底，避免 proactive 重构时静默失效

### 涉及子系统（"部门"）
| 子系统 | 路径 | 本次影响 |
|---|---|---|
| **V5 意识工厂** | `Ikaros-memory/v5` | 核心改动区（think.py + 新增 supervisor_persist.py + metacog/store 配套） |
| **控制面板 / 整栈编排** | `tools/core/dashboard` + `bin/ikaros-control.bat` | think 作为独立可重启组件；dashboard server 重写 |
| **本地 LLM :8080 + 嵌入 :8587** | memory watchdog | 运行时依赖（think 跑 `metacog.cycle()` 依赖它） |
| **Hermes 桌面端** | `core/hermes` / `hermes-studio` | 下游消费 `latest_thought.json`（Ikaros 人格/状态来源） |
| **ThirdSpace Vault** | `data/thirdspace-vault` | 经 `bin/sync-thirdspace-v5.py` 同步 `latest_thought` |
| **bin/cloud_chat.py** | `bin` | 调 `v5.think` 的 `inner_monologue` / `_intensity` 等 API（符号须稳定） |

### 激活方式
仅重启 `think` 组件即可，**无需重启整栈**：
- 控制面板 `:9100` → 找到「自思考循环」→ 先 **Stop** 再 **Start**
- 前提：`:8080` 本地 LLM 必须在线
- 首次运行 `supervisor_persist.ensure_mission()` 自动创建 `data/supervisor/` 四件套（mission.md / heartbeat.md / state.json / latest-status.txt）

---

## 历史提交（摘要）
- 2026-07-20：启动器切到控制面板 `bin/ikaros-control.bat`；b10000-cuda 经面板 start 可用
- 2026-07-07：PyQt6 桌面宠移除，架构转为 Tauri v2 + Live2D + Hermes Desktop + Dashboard + memory watchdog
- 2026-07-04：V4 项目 ship；清理 265 个旧文件一次性 push 到 `ArtificialAngels/Ikaros` (commit `11d682f`)
