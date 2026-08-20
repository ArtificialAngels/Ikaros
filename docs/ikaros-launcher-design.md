# Ikaros 启动器设计文档

> 状态：设计文档（未动代码，仅规范）。与 `config/components.yaml`、`core/components/registry.py::ComponentSpec` 配套使用。
> 适用范围：`E:\Ikaros-line3`（line3 工作树，dsh 底座，2026-08-18 之后）。
> 阅读对象：任何准备启动 / 重启 Ikaros 的开发者与 AI agent。
> 关联文档：`AGENTS.md`（端口表 / 启动段）、`docs/ikaros-dsh-plugin-architecture.md`（dsh 插件化方向）、`docs/COMPONENT-PLUGIN-SPEC.md`（本批次的组件字段规范）。

---

## 0. 一句话目标

**统一 Ikaros 启动入口（命令行 + Windows GUI 双击），收敛 4 个组件（dsh / conversation-tree / embedding / herdr）的生命周期命令，让"启动 Ikaros"这件事不再依赖散落在 `bin/` 里的 6 个互不感知的 bat/ps1 脚本。**

---

## 1. 入口选择：`bin/ikaros` 统一 CLI vs `bin/start-ikaros.bat` Windows GUI 双击

### 1.1 候选 A —— `bin/ikaros` 统一 CLI（推荐）

形态：三平台薄壳脚本（`bin/ikaros` bash / `bin/ikaros.ps1` / `bin/ikaros.bat`），每条只把参数透传给 Python 调度器 `bin/ikarosctl.py`（stdib-only，零第三方依赖），由 Python 读 `config/components.yaml` 拓扑、决定组件启动顺序与失败回退。

| 维度 | 评估 |
|------|------|
| 跨平台一致 | ✅ bash / PowerShell / cmd 三 shell 入口共享同一 Python 调度逻辑，只壳不同 |
| 子命令可扩展 | ✅ `ikaros <sub>` 子命令（web/tree/embed/all/doctor/update/status/ps/logs）自然映射 argv |
| 可观测 | ✅ Python 端可以打印 PID、端口、健康状态、依赖图 |
| 自测友好 | ✅ CI 可直接 `ikaros doctor` 断言（无需启动 web） |
| AI agent 友好 | ✅ `ikaros doctor` 单行可解析输出，便于 agent 做 smoke check |
| 学习成本 | ⚠️ 用户需要记一个入口 + 子命令（vs 现在直接 `start-dsh-ikaros.bat`） |

### 1.2 候选 B —— `bin/start-ikaros.bat` Windows GUI 双击

形态：薄壳 bat，双击后弹出 cmd 窗口，菜单选择「web / tree / embed / all / 重启 / 停止」。

| 维度 | 评估 |
|------|------|
| 双击体验 | ✅ 桌面用户友好（无命令行知识） |
| 跨平台 | ❌ 仅 Windows；Linux/macOS 用户必须再搞入口 |
| 可扩展 | ⚠️ 菜单结构硬编码，每加一个子命令要改 bat + 测试 |
| 与现有生态融合 | ⚠️ `start-dsh-ikaros.bat` 等 6 个启动脚本是「单组件」模型，与「统一入口」是两条路线 |
| AI agent 友好 | ❌ 双击 bat 无人值守不可用 |

### 1.3 决定 —— 候选 A 为主，候选 B 作 thin wrapper

**最终选择：候选 A 优先（统一 CLI），候选 B 在其之上提供 GUI 入口（菜单本质是 `ikaros` CLI 的图形前端）。**

理由：
1. line3 工作流以 AI agent + 远程 CLI 为主，GUI 双击不是核心需求；
2. 单组件脚本（`start-dsh-ikaros.bat`、`start-omp.bat`、`memory_v5/services/start-embedding.bat`、`restart-dsh-ikaros.ps1`）仍是事实标准，新启动器必须**共存**而非替换；
3. 候选 A 是"调度层"，候选 B 是"展示层"——二者职责清晰、不冲突。

GUI 入口若要做，定义为薄壳：
```bat
@echo off
rem bin/start-ikaros.bat —— 双击菜单（透传到 ikarosctl）
:menu
echo 1) web  2) tree  3) embed  4) all  5) doctor  6) update  7) status
set /p c=choose:
call "%~dp0ikaros.bat" %c%
pause
goto menu
```

---

## 2. 启动器覆盖场景：6 个子命令

> 子命令集合由 plan 指定：`web / tree / embed / all / doctor / update`。下表是每个子命令的"预期行为契约"。

### 2.1 `ikaros web`

启动 dsh 工作引擎（web 模式，:3080）。

- **依赖检查**：先确保 `:8587 embedding` 健康（dsh 的 MCP memory-v5 启动可能调用嵌入）；
- **执行**：调 `bin/start-dsh-ikaros.bat web`（等价现有 `bin/start-dsh-ikaros.bat`，默认走 web 分支）；
- **端口**：`IKAROS_DSH_WEB_PORT=3080`（与官方 dsh desktop 不冲突；bat 内部已有 3080/3085 双端口兜底）；
- **健康检查**：轮询 `:3080` 监听 + `/healthz`（若 dsh 提供），否则只验端口；
- **日志**：`%USERPROFILE%\.dsh\ikaros-dsh-web.out.log` / `.err.log`（沿用 `restart-dsh-ikaros.ps1` 的目录约定，避免再发明）；
- **失败回退**：启动失败 → 打印 `ikaros-dsh-web.err.log` 最后 30 行 + `exit 1`。

### 2.2 `ikaros tree`

启动 Conversation Tree 面板（:48920，Python ThreadingHTTPServer）。

- **依赖检查**：先确保 `memory_v5` 数据可用（`core/memory_v5/data/v5/v5.db` 存在；不存在 → 警告但仍启动，第一次连接会自动建表）；
- **执行**：`python core/conversation-tree/server.py --port 48920`（前台进程，Python stdout 直接打到终端；或后台用 `start /MIN`）；
- **健康检查**：轮询 `:48920/healthz`（server.py 应在 Phase 2 提供，否则只验端口）；
- **日志**：`IKAROS_LOGS/conversation-tree.out.log`；
- **失败回退**：端口被占 → 提示用户 `python bin/proc.py kill 48920`；
- **注意**：当前 `core/conversation-tree/server.py` 自身不带 watchdog（registry 标 `watchdog: self` 但未实现），启动器需在外部跑一个简单的端口巡检（详见 §5）。

### 2.3 `ikaros embed`

启动 Embedding 服务（bge-m3-q8_0，:8587，llama.cpp）。

- **依赖检查**：`core/memory_v5/models/bge-m3-q8_0.gguf` 存在；`runtime/llama/b10000-cuda/llama-server.exe` 存在；
- **执行**：调 `core/memory_v5/services/start-embedding.bat`（已存在，前台运行；或后台化）；参数必须是 `--pooling cls`（AGENTS.md §embed 教训：cls 才对 bge-m3，否则语义向量全零 / 卡死）；
- **健康检查**：轮询 `:8587/health`（llama-server 自带），**必须二次校验嵌入值非零**（教训：`nomic-embed-text-v2-moe.f32.gguf` 端口 + health 都活，但语义向量全 0，沉默死了一周；见 `AGENTS.md` 2026-08-14 段）；
- **日志**：`IKAROS_LOGS/embedding.out.log`；
- **失败回退**：CUDA 不可用 → 提示"加 `nvidia-smi` 检查 + 重启 GPU 驱动"。

### 2.4 `ikaros all`

按拓扑顺序启动全部组件，等价 `ikaros embed && ikaros tree && ikaros web` + `herdr` 按需。

- **拓扑**（来自 `components.yaml` 的 `dependencies`）：
  - `embedding` (无依赖) → 最先
  - `conversation-tree` 依赖 `memory_v5`（隐式 = embedding）→ 第二
  - `dsh` 依赖 `embedding` → 第三
  - `herdr` 无 TCP 端口、无依赖 → **默认不拉起**（按需 `ikaros herdr`，避免占终端）
- **失败策略（fail-fast vs best-effort）**：
  - **fail-fast 模式（默认 `--strict`）**：任一组件启动失败 → 立即停止后续组件 + 保留已起进程 + exit 非 0；
  - **best-effort 模式（默认行为）**：失败组件跳过 + 警告，后续组件继续；最终汇总报告。
  - **选择**：默认 best-effort（让 dsh 仍可起，方便调试 embedding 故障时不影响工作），加 `--strict` 给 CI。

### 2.5 `ikaros doctor`

只读体检，不拉起任何进程。

- **检查项**：
  - IKAROS_ROOT 是否锚定到合法目录（路径含 `bin/ikaros-env.bat`）；
  - `bin/ikaros-env.sh|bat|ps1` 三份文件存在且 mtime 一致；
  - `runtime/node/node.exe` / `runtime/dsh/.../bin.js` / `runtime/portable-python/python.exe` / `runtime/llama/.../llama-server.exe` / `runtime/herdr/herdr.exe` 全部存在；
  - 模型文件 `bge-m3-q8_0.gguf` 存在（体积 ≥ 500MB）；
  - patch overlay `core/ikaros-dsh/cordis.patch.yml` 存在；
  - 端口扫描（`:3080 / :48920 / :8587`）当前是否被占用、被谁占（`python bin/proc.py ps`）；
  - `data/logs/` 目录可写；
  - `config/components.yaml` 通过 `core.components.registry.load_components()` 校验。
- **输出**：单行 OK/FAIL + 详情块（`name | status | detail`）；
- **退出码**：全 OK → 0；任一 FAIL → 1；
- **AI agent 友好**：JSON 输出模式 `--json`（便于脚本消费）。

### 2.6 `ikaros update`

**本子命令在 line3 工作流下语义有限**，因为 `runtime/` 是 git-ignored 上游二进制（dsh npm 包 / portable-python / llama.cpp / herdr），不是 git pull 能解决的。
**实际语义**：

- 拉取 line3 自身（git fetch + git status）→ 若有未提交改动，**拒绝 update 并提示**（教训：AGENTS.md 2026-08-18 真实 FS 不一致大修就是 harness 编辑落虚拟视图 + 游离 patch 进程覆盖源码）；
- 重新跑 `scripts/setup-native.py`（link `bin/` ↔ `runtime/`、复制运行时到本地）；
- 不重新下载 model（`bge-m3-q8_0.gguf` 605MB，不在 update 流里）；
- **不**触碰 `core/memory_v5/data/v5/`（用户数据，update 不应清）。

### 2.7 隐含子命令（不在 plan 列表内，但补完整）

| 子命令 | 用途 |
|--------|------|
| `ikaros status` | 列每个组件：状态（running/stopped/unhealthy）+ PID + 端口监听 + 上次日志时间戳 |
| `ikaros ps` | 等价 `python bin/proc.py ps`（已存在；启动器可直接代理） |
| `ikaros logs <component>` | tail `$IKAROS_LOGS/<component>.out.log` |
| `ikaros stop <component>` | 杀单个组件进程（`bin/proc.py kill <port>`） |
| `ikaros herdr` | 按需拉起 herdr（默认不随 all 启动） |

---

## 3. 跨平台：三 shell 实现约束

> 现状：`bin/ikaros-env.sh|bat|ps1` 三套并立（自锚定 IKAROS_ROOT 单一权威源）。新启动器必须复用这套机制，不另立山头。

### 3.1 bash (`bin/ikaros`)

- 入口：`#!/usr/bin/env bash`；
- 第一行：`source "$(dirname "$0")/ikaros-env.sh"` 拉环境；
- 子命令分发：`exec python "$(dirname "$0")/ikarosctl.py" "$@"`（避免 fork 进程解析 YAML）；
- 注释：**UTF-8 自由**（bash 默认 UTF-8 locale，中文 OK）；
- 错误：`set -euo pipefail` + trap ERR 打印回溯。

### 3.2 PowerShell (`bin/ikaros.ps1`)

- 入口：`#requires -Version 5.1`；
- 第一行：`. (Join-Path $PSScriptRoot "ikaros-env.ps1")` 拉环境；
- 子命令分发：`& python (Join-Path $PSScriptRoot "ikarosctl.py") @args`；
- 注释：**UTF-8（BOM 不必）**，PowerShell 5.1 在没有 `$OutputEncoding = [System.Text.Encoding]::UTF8` 时中文会乱码——bat/ps1 双入口互调时必须先设；
- 路径：Join-Path + Resolve-Path，不写死盘符。

### 3.3 cmd (`bin/ikaros.bat`)

- 入口：`@echo off` + `setlocal EnableExtensions`；
- 第一行：`call "%~dp0ikaros-env.bat"` 拉环境；
- 子命令分发：`call "%~dp0ikaros.bat-shim.bat" %*`（注：cmd 不能直接 exec Python 的 argparse 透传，需要 shim 解析第一参数后调 `python bin\ikarosctl.py %*`）；
- **硬约束**：
  - **文件必须是 ANSI/GBK 编码**——cmd.exe 默认代码页是 936（中文 Windows），UTF-8 BOM 或纯 UTF-8 中文注释会被 cmd 解析器吃成乱码甚至 parse error（教训：现存 `bin/ikaros-env.bat` 第 2-7 行的中文注释读出来就是 mojibake，文件本身仍能跑，但写入新中文注释必须用 GBK 或纯 ASCII）；
  - **注释限 ASCII**（或事先用 chcp 65001 + 显式声明 UTF-8，但 936 ↔ 65001 切换在嵌套批处理里不稳定）；
  - **`%IKAROS_DSH_WEB_PORT%` 等变量必须 setlocal 前声明或带默认值**（教训：`ikaros-env.bat` 的 setlocal 会让变量不外传，`start-dsh-ikaros.bat` 显式兜底 `if not defined ... set "..."=3085`）；
  - **不要用 `for /f "delims="`** 处理带 `!` 的字符串（默认 EnableDelayedExpansion 会吃 `!`，必须 `setlocal DisableDelayedExpansion` 或临时 `setlocal`）；
  - **错误处理**：cmd 无 try/catch，靠 `if errorlevel 1` 链式判断（不要把 `errorlevel` 写在一行 else 里，会被解析错）。

### 3.4 调度核心（跨 shell 共用）`bin/ikarosctl.py`

- stdlib only（`argparse / subprocess / json / socket / pathlib / dataclasses`）；
- 第一步：`from core.components.registry import list_components`（共享 components.yaml）；
- 第二步：拓扑排序（Kahn）→ 按依赖顺序启动；
- 第三步：每个组件 `subprocess.Popen(start_script, ...)` + 端口轮询 + PID 记录；
- 第四步：失败汇总（best-effort / strict）；
- **跨平台**：Windows 用 `subprocess.Popen(..., creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)` 让子进程独立；POSIX 用 `start_new_session=True`。

---

## 4. 与现有 `start-dsh-ikaros.bat` 等单组件脚本的共存策略

### 4.1 现状清单（line3，2026-08-20）

| 脚本 | 类型 | 关系 |
|------|------|------|
| `bin/start-dsh-ikaros.bat` | 单组件 bat | dsh 启动器（事实标准） |
| `bin/restart-dsh-ikaros.ps1` | 单组件 ps1 | dsh 重启（杀旧 + --patch 重启） |
| `bin/start-omp.bat` | 单组件 bat | omp/pi TUI（herdr 链路前端） |
| `core/memory_v5/services/start-embedding.bat` | 单组件 bat | embedding llama-server 启动 |
| `core/memory_v5/services/start-llm.bat` | 单组件 bat | **已退役**（本地 LLM 2026-08-18 退役） |
| `core/memory_v5/services/start-all.bat` | 单组件 bat | 历史"全部"启动入口（已陈旧，不引用） |

### 4.2 共存策略 —— 启动器是调度层，组件脚本是 worker 层

| 层 | 谁负责 | 职责 |
|----|--------|------|
| **调度层**（新） | `bin/ikaros` / `ikaros.ps1` / `ikaros.bat` + `ikarosctl.py` | 拓扑排序 / 失败回退 / 端口巡检 / 日志汇总 / doctor 体检 |
| **Worker 层**（保留） | 现有 4 个单组件 bat/ps1 | 单组件的具体启动命令（`node bin.js web --patch ...` / `llama-server -m ...`） |

**新启动器不重写单组件脚本**，而是 subprocess 调它们。例如：

```python
# bin/ikarosctl.py 伪码
spec = get_component("dsh")
subprocess.Popen(
    spec.lifecycle["start_script"],  # "bin/start-dsh-ikaros.bat web"
    cwd=os.environ["IKAROS_ROOT"],
    env={**os.environ, "IKAROS_DSH_WEB_PORT": str(spec.port)},
)
```

### 4.3 兼容性保证

- `ikaros web` 启动 dsh 时，最终调用的命令**完全等价**当前 `bin/start-dsh-ikaros.bat web`（含 `--patch core/ikaros-dsh/cordis.patch.yml`）；
- 现存 AI agent / 桌面用户的 `.bat` 双击习惯**不破坏**——老脚本保留；
- `components.yaml` 的 `lifecycle.start_script` 字段是**字符串**，未来若用 `subprocess` 直起（不依赖 bat wrapper），把字段值换成 `"node runtime/dsh/.../bin.js web --patch ..."` 即可，不影响调度层。

### 4.4 何时退役单组件脚本

**现在不退役**。理由：
1. 单组件脚本仍是大多数调试场景的最短路径（直接 bat 双击，比 `ikaros web --verbose` 更直接）；
2. 启动器是叠加层而非替换层；
3. 退役触发条件（任一）：
   - line3 进入 production 阶段、单一入口成为规范；
   - 或：现有 6 个脚本被证明无人直接调用（git blame + `grep` 排查）。

---

## 5. watchdog 设计：内置 vs 各组件脚本自管

### 5.1 历史教训

> AGENTS.md 2026-08-19 段：**集中 watchdog 已退役**。"Embedding (:8587): 由各组件启动脚本自带 watchdog 拉起（不再由集中看门狗管理）"。

根因（AGENTS.md 2026-08-14）：集中 watchdog 只查端口 + health，**不校验嵌入值**，结果 `nomic-embed-text-v2-moe.f32.gguf` 输出全零向量但 watchdog 报"健康"，语义检索**沉默死了一周**。

### 5.2 设计原则

| 选项 | 利 | 弊 |
|------|----|----|
| **A. 启动器内置 watchdog** | 单点管理、UI 一致 | **容易复活旧集中 watchdog 的盲区**（只看端口不看语义）；与"各组件自管"哲学冲突 |
| **B. 各组件脚本自带 watchdog** ✅ | 健康检查贴近组件语义（如 embedding 必须校验非零向量）；故障域隔离；与已退役的集中 watchdog 教训一致 | 启动器看不到健康详情，需要组件主动上报（status 子命令聚合） |
| **C. 混合：调度层做端口 + 进程存活，组件脚本做语义健康** ✅ | 各取所长 | 需要约定上报协议（status 文件 / 共享 SQLite / HTTP endpoint） |

### 5.3 选择 —— C 混合方案

- **启动器（`ikarosctl.py`）负责**：
  - 端口监听巡检（每 5s 一次，连续 3 次失败 → unhealthy）；
  - 进程 PID 存在性（`os.kill(pid, 0)` / Windows `OpenProcess`）；
  - **不**做语义检查（不归它管）。
- **各组件脚本负责**：
  - embedding：必须校验一个 probe 向量非零（教训固化）；
  - dsh：校验 `/healthz`（若 dsh 提供）+ mcp_server 子进程已 spawn；
  - conversation-tree：校验 `/healthz`（待 server.py 添加）；
  - herdr：校验命名管道可达（`test -p` / Windows `WaitNamedPipe`）。
- **上报通道**：
  - 简单方案：每组件写 `data/logs/<id>.status.json`（`{"status":"healthy","checked_at":...,"detail":{...}}`），启动器读这个聚合到 `ikaros status`；
  - 进阶方案（Phase 3）：共享 SQLite `data/runtime/health.db`（多组件并写）。

### 5.4 与 AGENTS.md 的一致性

- 新启动器**不复活集中 watchdog**——只在调度层做"端口 + 进程存活"的最浅检查；
- 真正的健康（语义 / 业务）是各组件脚本的责任，与 AGENTS.md 既有纪律一致；
- 教训二次复用：任何 watchdog 都必须**校验语义**，不能只看端口。

---

## 6. IKAROS_ROOT 自锚定模式

### 6.1 现状（已实现，2026-08-11）

- `bin/ikaros-env.sh`：`export IKAROS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"`（bash 自锚定）；
- `bin/ikaros-env.bat`：`for %%I in ("%~dp0..") do set "IKAROS_ROOT=%%~fI"`（cmd 自锚定）；
- `bin/ikaros-env.ps1`：`$env:IKAROS_ROOT = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path.TrimEnd('\\')`（PS 自锚定）；
- **单一权威源**：三 shell 各自实现同一锚定逻辑，互不引用（避免 cross-shell 引用混乱）。

### 6.2 新启动器复用

启动器三个入口（`bin/ikaros` / `ikaros.ps1` / `ikaros.bat`）**第一行都是 source/call 现有 env 文件**，不再重复实现锚定：

| 入口 | 第一行 |
|------|--------|
| `bin/ikaros` | `source "$(dirname "$0")/ikaros-env.sh"` |
| `bin/ikaros.ps1` | `. (Join-Path $PSScriptRoot "ikaros-env.ps1")` |
| `bin/ikaros.bat` | `call "%~dp0ikaros-env.bat"` |

### 6.3 自锚定必须保证的不变量

1. **不写死盘符**：`E:\`、`C:\` 一律不出现在 `bin/` 下任何脚本（教训：现存 `bin/start-omp.bat` 没有盘符，但 `restart-dsh-ikaros.ps1` 第 6 行有 `$env:USERPROFILE`，这是 %USERPROFILE% 派生，不是硬编码，可接受）；
2. **支持整个项目文件夹移动**：把 `E:\Ikaros-line3` 整目录拷到 `D:\whatever\line3` 仍然能起——靠的是 `%~dp0` / `${BASH_SOURCE[0]}` / `$PSScriptRoot` 的相对推导；
3. **路径分隔符在跨 shell 边界要显式转换**：bash → Python 用 `/`，cmd → Python 用 `\`，Python `pathlib.Path` 自动适配（不要在脚本里手写 `os.path.join`）；
4. **`components.yaml` 里所有路径都是相对 IKAROS_ROOT**——绝对禁止在 YAML 里写 `E:\\` / `/home/x/Ikaros`（教训：`core/env/ikaros-paths.json` 头注释明示）。

### 6.4 错误处理

- 锚定失败（如 `bin/ikaros-env.bat` 不存在）→ 打印明确错误 "IKAROS_ROOT anchor failed: bin/ikaros-env.bat not found at <path>" + exit 1；
- 检测到路径含空格或中文（已知会触发某些 npm 子进程 bug）→ 警告但不阻断（用户责任）。

---

## 7. 失败模式处理（某个子组件拉不起怎么办）

### 7.1 失败分级

| 级别 | 触发 | 处理 |
|------|------|------|
| **F1 软依赖失败** | `embedding` 启动失败 | `dsh` 仍尝试启动（dsh 不强依赖 embedding 启动，但 MCP memory-v5 工具可能不可用）；打印警告；`ikaros status` 标 embedding=down |
| **F2 强依赖失败** | `embedding` 启动失败 + dsh 配置 `failOnStartupError=true` | dsh 直接拒启动（MCP server 起不来）；启动器把 dsh 标 failed，不影响 tree |
| **F3 端口冲突** | `:3080` 被占（最常见：旧 dsh 残留 / 用户装了 dsh desktop） | `ikarosctl` 调用 `python bin/proc.py kill 3080 --yes` 自动杀；或加 `--no-kill` 仅警告 |
| **F4 路径不存在** | `core/ikaros-dsh/cordis.patch.yml` 缺失 | 启动器启动前预检 + 拒绝启动 + 提示 `git checkout core/ikaros-dsh/` |
| **F5 配置错误** | `components.yaml` 校验失败（缺字段 / 端口重复） | 启动器读 YAML 时 `ValueError` → 直接退出 + 打印哪一行错了 |
| **F6 嵌入全零** | embedding 端口活 + health 活，但嵌入值校验全 0 | **embedding 自带 watchdog 检测到 → 自动重启 llama-server 一次**，仍失败则标 unhealthy + 打印错误（教训固化，见 §5.1） |
| **F7 dsh 二进制升级破坏** | `runtime/dsh` 被覆盖、`@deepseek-ai/dsh-mcp-client` 等包名变化 | 启动 dsh 时 patch overlay 解析失败 → 启动器捕获 → 提示 `git status runtime/`（一般会显示 untracked 改动）+ exit 非 0 |

### 7.2 失败时的进程清理

- **best-effort 模式**：已起的进程**保留**（用户决定是否手动 `ikaros stop <id>`），最终打印汇总："started: A B C / failed: D (see logs)"；
- **strict 模式**：任一失败 → 先 `taskkill /F /T` 杀掉所有已起子进程 → exit 1；
- **超时**：单个组件启动超时默认 60s（可 `--timeout` 覆盖）；超时 = 失败，按上表处理。

### 7.3 日志策略

- 每组件日志独立：`$IKAROS_LOGS/<id>.{out,err}.log`（沿用现有约定）；
- 启动器本身日志：`$IKAROS_LOGS/ikarosctl.log`（每次启动一行 JSON：started_at / components / results）；
- 日志轮转：暂不做（line3 数据量小），但保留 `data/logs/` 在 `.gitignore` 里。

---

## 8. 风险点

### 8.1 dsh 升级（高）

**风险**：`runtime/dsh` 是 npm 本地安装的包（pnpm），上游 DeepSeek Harness 升级可能：
- 改 `--patch` 语法（cordis API 变更）→ `core/ikaros-dsh/cordis.patch.yml` 解析失败；
- 改包名（如 `@deepseek-ai/dsh-mcp-client` → `@deepseek-ai/dsh-mcp-v2`）→ patch 里裸包名解析不到；
- 改 web 端口默认值（3080 → 3090）→ 与本地约定冲突；
- 改 memory-v5 MCP 工具名（`v5_memory_search` → `memory_search`）→ 召回/写回插件失配。

**缓解**：
- `components.yaml` 的 `lifecycle.start_script` 集中登记启动命令，升级时**只需改一处**；
- `cordis.patch.yml` 是 line3 自己维护的 overlay，不在 npm 包内——升级 npm 包不会破坏 patch 本身；
- 升级后**必跑** `ikaros doctor` + `ikaros web` smoke（启动后用 `v5_memory_search` 实测一次）；
- **不自动升级**（`ikaros update` 子命令禁触及 `runtime/`）。

### 8.2 端口冲突（中）

**风险**：`:3080`（dsh）/ `:48920`（tree）/ `:8587`（embed）被其他进程占：
- `:3080`：用户装了官方 dsh desktop / 旧 line3 残留 / 系统其它服务；
- `:48920`：低概率（非常用端口）；
- `:8587`：用户跑了别的 llama-server。

**缓解**：
- 启动前 `ikaros doctor` 端口扫描（`bin/proc.py ps` 看谁占 + `netstat`）；
- 自动 kill 模式 `--kill-conflicts`（默认关，避免误杀）；
- 端口可配置（`IKAROS_DSH_WEB_PORT` 环境变量已支持）。

### 8.3 平台差异（中）

**风险**：
- **bash ↔ PowerShell 切换**：WLS 用户 / Git-Bash 用户 / 系统 PowerShell 用户三套环境差异（路径分隔符、命令内建）；
- **Windows 长路径**：260 字符限制（dsh node_modules 深嵌套常踩）；
- **macOS / Linux**：line3 当前是 Windows-only（herdr 是 Windows 命名管道二进制、llama.cpp b10000-cuda 是 Windows），跨平台属于 Phase 5+；
- **cmd GBK**：见 §3.3，新 bat 不能写中文注释。

**缓解**：
- 跨平台文档明示"line3 当前 Windows-only"；
- 长路径：Git for Windows `core.longpaths=true` + Node `--preserve-symlinks`；
- cmd 注释：所有新 bat 文件**注释限 ASCII** + 文件保存为 ANSI/GBK 编码（不保存为 UTF-8）。

### 8.4 真实 FS vs harness 视图不一致（教训 AGENTS.md 2026-08-18 高）

**风险**：AI agent 通过 harness 文件工具看到的视图 ≠ 真实磁盘（虚拟视图 / junction 漏改 / 游离 patch 进程）。新启动器如果被 AI agent 写文件 + 立即跑，会读到旧版。

**缓解**：
- 启动器**第一件事**是 `git status`（line3 是 git 仓库，git 是真实 FS 权威源）；
- 若 git status 有 uncommitted 改动 → 警告但不阻断；
- 启动 dsh 前再次 `git diff --stat core/ikaros-dsh/cordis.patch.yml`（patch 是否最新）；
- 文档明示："**启动器不验证虚拟视图**——只在真实 FS 起作用"。

### 8.5 watchdog 沉默死亡（教训 AGENTS.md 2026-08-14 高）

**风险**：端口活 ≠ 业务活（如 embedding 全零向量 / dsh patch 未生效）。

**缓解**：见 §5.3 C 混合方案——每个组件脚本**必须做语义校验**，启动器只做端口 + 进程存活。

### 8.6 IKAROS_ROOT 错位（低-中）

**风险**：`%~dp0` 在某些 bat 调用方式（如被 `cmd /c` 调用）解析出带尾随 `\bin` 的非预期路径。

**缓解**：
- bat 锚定后立即 `for %%I in ("%IKAROS_ROOT%") do set "IKAROS_ROOT=%%~fI"`（强制绝对路径规范化，已实现）；
- 启动器启动时 sanity check（`IKAROS_ROOT/bin/ikaros-env.bat` 必须存在）。

### 8.7 日志爆炸（低）

**风险**：dsh web 日志、`ikaros-dsh-restart.log`、`mcp_server.out` 等可能增长很快。

**缓解**：
- `data/logs/` 在 `.gitignore`（已实现）；
- 启动器**不**做日志轮转（line3 数据量小）；
- 提供 `ikaros logs --rotate <id>` 子命令预留（Phase 2）。

---

## 9. 实现路线图（建议）

| 阶段 | 内容 | 依赖 |
|------|------|------|
| **Phase 1** | `bin/ikarosctl.py`（Python stdlib）+ `bin/ikaros` / `ikaros.bat` / `ikaros.ps1` 薄壳；只实现 `doctor` / `status` / `ps` 三个只读子命令 | `config/components.yaml` 已就绪 ✅ |
| **Phase 2** | 实现 `web` / `tree` / `embed` 三个启动子命令；混合 watchdog（端口 + 进程存活） | Phase 1 |
| **Phase 3** | 实现 `all` + `stop` + `logs`；语义校验（embedding probe / dsh mcp spawn）；`data/runtime/health.db` 上报 | Phase 2 |
| **Phase 4** | 实现 `update`（限定 git fetch + scripts/setup-native.py 重跑，**不**触碰 runtime/） | Phase 3 |
| **Phase 5** | 跨平台（macOS / Linux）；herdr 拉起 + 命名管道健康检查 | 评估 line3 是否真有跨平台需求 |

---

## 10. 核心结论（≤ 10 行）

1. **入口选 CLI 优先**（`bin/ikaros` 三 shell 壳 + Python `ikarosctl.py` 调度核心），GUI 双击 bat 作为 thin wrapper（菜单 → CLI）。
2. **6 子命令契约**：web/tree/embed/all/doctor/update；外加隐含 status/ps/logs/stop/herdr；详细行为见 §2。
3. **三 shell 共用**：`bin/ikaros-env.sh|bat|ps1` 是单一权威源，启动器只 source/call，不再各自实现 IKAROS_ROOT 锚定。
4. **cmd 硬约束**：注释限 ASCII + 文件 GBK + setlocal 兜底 + 不在嵌套批处理里切 chcp。
5. **共存不替换**：现有 4 个单组件脚本（start-dsh-ikaros.bat / start-omp.bat / start-embedding.bat / restart-dsh-ikaros.ps1）是 worker 层，新启动器是调度层，subprocess 调它们。
6. **watchdog = C 混合**：启动器只管端口 + 进程存活；语义校验（embedding probe / dsh mcp spawn）归各组件脚本，避免复活集中 watchdog 的盲区。
7. **IKAROS_ROOT 自锚定复用**：`%~dp0` / `${BASH_SOURCE[0]}` / `$PSScriptRoot` 三 shell 各自实现，已就绪，启动器不再造轮子。
8. **失败处理 best-effort 默认 + --strict**：失败组件警告但不阻断；进程清理只在 strict 模式做。
9. **核心风险**：dsh 升级 / 端口冲突 / 平台差异 / 真实 FS vs 视图不一致 / watchdog 沉默死亡——均有缓解策略固化到实现约束。
10. **5 阶段路线**：Phase 1 (doctor/status/ps) → Phase 2 (web/tree/embed) → Phase 3 (all + 语义校验) → Phase 4 (update) → Phase 5 (跨平台)。

---

## 附录 A：相关文件清单

| 文件 | 角色 |
|------|------|
| `bin/ikaros-env.sh\|bat\|ps1` | IKAROS_ROOT 单一权威源（自锚定） |
| `bin/start-dsh-ikaros.bat` | dsh 启动器（worker 层） |
| `bin/restart-dsh-ikaros.ps1` | dsh 重启器（worker 层） |
| `bin/start-omp.bat` | omp/pi TUI 启动（herdr 链路） |
| `core/memory_v5/services/start-embedding.bat` | embedding llama-server 启动（worker 层） |
| `core/memory_v5/mcp_server.py` | memory_v5 MCP stdio server（dsh 调它） |
| `core/ikaros-dsh/cordis.patch.yml` | dsh overlay（dsh 用 `!!js process.env.IKAROS_ROOT` 推导路径，0 硬编码） |
| `core/ikaros-dsh/plugins/ikaros-memory/` | npm 包（turn-stopping 写回 + pre-step 召回） |
| `config/components.yaml` | 组件元数据规范源（启动器读它） |
| `core/components/registry.py::ComponentSpec` | 组件 spec 数据类（启动器引用） |
| `core/env/ikaros-paths.json` | 路径配置（与 ikaros-env.* 同步） |
| `AGENTS.md` | handoff card（端口表 / 启动段 / 教训段） |

## 附录 B：open questions（待 line3 实施时确认）

- [ ] `ikaros web` 是否需要 `--patch` 参数透传（用户自定义 overlay）？
- [ ] `ikaros update` 是否包含 `npm` 升级（更新 `runtime/dsh/node_modules`）？目前倾向**不**包含（教训：手动升级 npm 包易引入 AGENTS.md 2026-08-18 那类破坏）。
- [ ] GUI 双击 bat（候选 B）是否值得实现？line3 AI agent 流是否真有桌面双击需求？
- [ ] `data/runtime/health.db` 上报通道是否值得做（vs 简单 JSON 状态文件）？