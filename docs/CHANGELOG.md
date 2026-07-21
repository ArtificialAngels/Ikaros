# Changelog

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
| **控制面板 / 整栈编排** | `tools/ikaros-dashboard` + `bin/ikaros-control.bat` | think 作为独立可重启组件；dashboard server 重写 |
| **本地 LLM :8080 + 嵌入 :8587** | memory watchdog | 运行时依赖（think 跑 `metacog.cycle()` 依赖它） |
| **Hermes 桌面端** | `hermes-agent` / `hermes-studio` | 下游消费 `latest_thought.json`（Ikaros 人格/状态来源） |
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
