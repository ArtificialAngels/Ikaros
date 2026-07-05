# PATH-LAYER — Ikaros Environment 的 Windows PATH / sys.path 等效层

> 严格文档化: 把 Windows 系统的 `PATH` / `PYTHONPATH` / `sys.path` 等概念
> 1:1 映射到 Ikaros 项目自己的路径管理层。
>
> 这份文档是**让人接手的智能体能一眼看懂** —— Ikaros 项目用
> `Ikaros-environment/ikaros-env.{bat,ps1}` 在进程启动时建了一层
> **完全独立于系统的可执行/模块搜索路径**, 用 `ikaros-paths.json` 序列化
> 全部 60+ 路径项。

---

## 1. 核心思想 (30 秒看懂)

Ikaros 是**便携项目** —— U 盘 / 不同盘符都能跑。 这要求:

- **不能依赖系统 PATH 里碰巧装了什么 Python / Node / llama / npm**
  - 否则到一台没装对应依赖的机器上, `python` `node` `npm` 就飞了
- **不能依赖系统 PYTHONPATH 的写法**
  - 旧版 HERMES_* 变量在不同脚本里有不同假设
- **不能依赖用户的 Windows PATH 设置**
  - 哥哥可能同时装 dev 其他 Python/Node,污染

解法:

```
                       ┌──────── Ikaros 项目内部 ────────────┐
                       │                                      │
  Windows 系统层      │   Ikaros-environment/                 │
  ┌──────────┐        │   ┌─────────────────────────────┐    │
  │ %PATH%   │ ←────→ │   │ ikaros-env.bat (CMD)         │    │
  │          │   prepend │ ikaros-env.ps1 (PowerShell)  │    │
  │ PYTHONPATH│   ←──→ │   │   负责把项目内 portable    │    │
  │           │   inject│   │   python/node/llama/npm   │    │
  │ sys.path  │   ←──→ │   │   prepend 到 %PATH% /      │    │
  │ NODE_PATH │   set  │   │   PYTHONPATH / etc.         │    │
  └──────────┘        │   └─────────────────────────────┘    │
                       │                                      │
                       │   ikaros-paths.json  ←──Python 读    │
                       │   scripts/validate-paths.py  ── 校验 │
                       │   scripts/detect-root.ps1  ── 探测   │
                       └──────────────────────────────────────┘
```

**也就是说: `Ikaros-environment/ikaros-env.bat` (对 .ps1) 就是 Ikaros 项目的"PATH-bootstrap"层, 等效于 (并且强于) Windows 的 `PATH` + `PYTHONPATH` 设置。**

---

## 2. Windows 概念 → Ikaros 实现 映射表

| Windows 概念 | 默认位置 / 行为 | Ikaros 等效在哪 | 何时生效 |
|---|---|---|---|
| **`%PATH%`** — 可执行文件搜索路径 | 系统属性 → 环境变量 | `ikaros-env.bat` Step 9: prepend 7 个项目内 bin 目录 | `call ikaros-env.bat` 后 |
| **`PYTHONPATH`** — Python 模块导入路径 | 用户 env | `ikaros-env.bat` Step 8: `set PYTHONPATH=%IKAROS_ROOT%;%IKAROS_HERMES_AGENT%` | 同上 |
| **`sys.path`** (Python `import` 时的搜索 list) | `PYTHONPATH` + site-packages 自动 | `PYTHONPATH` 注: portable-python 用 `_pth` 文件机制,`PYTHONPATH` 可能被屏蔽 | portable-python 自己处理 |
| **`%HOME%` / `%USERPROFILE%`** — 用户目录 | 系统自带 | **`IKAROS_ROOT`** (项目根, 跨机器可变) | `ikaros-env.bat` Step 1 |
| **`PYTHONHOME`** — Python 安装根 | `C:\PythonXX\` 等 | **故意清空** `set PYTHONHOME=` (Step 10) 避免误用系统 Python | 同上 |
| **`NODE_PATH`** — Node 模块搜索 | 由 npm 自动 | `set NODE_PATH=%IKAROS_RUNTIME%\node23\node_modules` (Step 10) | 同上 |
| **`NPM_CONFIG_PREFIX`** — npm 全局包根 | 通常 `%AppData%\npm` | **故意清空** 强制项目级 npm (Step 10) | 同上 |

---

## 3. `ikaros-env.bat` 7 大步骤逐行对账

> **接手者重点**: 你只需要看 `ikaros-env.bat` (你 CPU 的 PS/CMD 跑哪份),Step 1-11 都做了 PATH 增强。

| Step | 内容 | 等效 Windows 概念 |
|------|------|---|
| 1 | Detect `IKAROS_ROOT` (5 级优先级) | "在任意机器找项目根" |
| 2-4 | 填 30 个 `IKAROS_*` 路径变量 (core / hermes / memory / modules / live2d / mcp) | 直接路径替换 `C:\...` 字面量 |
| 5-6 | 业务路径 (llama-server, models, ports) | 同上 |
| 7-8 | 端口 / Python 编码 (`PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`) | 等效 Windows 区域设置 |
| **9** | **`%PATH%` prepend** 7 个项目 bin 目录 — 这是核心 | **= Windows PATH 等效层** |
| **10** | `NODE_PATH` set, `PYTHONHOME` / `NPM_CONFIG_PREFIX` 清空 | **= 反污染** |
| **11** | 12 个 `HERMES_*` 兼容变量 (`HERMES_ROOT=IKAROS_ROOT` etc.) | 等效 Windows 兼容层 (旧脚本别 break) |

> **`.ps1` 版本 Step 几乎完全相同**,仅 step 9 用 `$env:PATH = ($pathParts -join ';') + ';' + $env:PATH` 拼字符串。

---

## 4. 30 个 `IKAROS_*` 变量清单 (从 `ikaros-paths.json` 来)

### 4.1 核心 (`core.*`) — 7 项

| Key | 值 | Windows 等效 |
|---|---|---|
| `core.python` | `%IKAROS_ROOT%\portable-python\python.exe` | `where python.exe` 应该命中这条 |
| `core.runtime` | `%IKAROS_ROOT%\runtime` | 工具根 |
| `core.node` | `%IKAROS_ROOT%\runtime\node23\node.exe` | `where node.exe` 应该命中这条 |
| `core.npm` | `%IKAROS_ROOT%\runtime\node23\npm.cmd` | npm 入口 |
| `core.node_modules` | `%IKAROS_ROOT%\runtime\node23\node_modules` | `NODE_PATH` 注入 |
| `core.bin` | `%IKAROS_ROOT%\bin` | `bin/` 脚本(已 prepend 到 PATH) |
| `core.runtime.node_mod_path` (ps1) | 同上 node_modules | = `NODE_PATH` |

### 4.2 Hermes (`hermes.*`) — 4 项

| Key | 作用 |
|---|---|
| `hermes.agent` | upstream 源码(只读,`hermes-agent/`) |
| `hermes.home` | 运行时数据(`data/hermes-agent/`,含 auth.json state.db 等) |
| `hermes.bridge` | 桥接层(`bridge/`) |
| `hermes.core` | ikaros 自有 Python 包(`hermes/`) |

### 4.3 Memory (`memory.*`) — 5 项

`root / data / models / services / script` + `db (v3.db) + chromadb`。

### 4.4 LLM (`llama.*` + `models.*`) — 5 项

`llama.dir / server / cli / version`(默认 `b9867`) + `models.embedding/llm`(gguf 路径)。

### 4.5 Live2D (`live2d.*`) — 3 项

`root / src / src_tauri` —— Ikaros 桌宠。

### 4.6 MCP (`mcp.*`,NEW) — 1 项 + 4 子项

| Key | 作用 |
|---|---|
| `mcp.root` | `runtime/MCPServe/` —— MCP 服务集中装位 |
| `mcp.gitnexus.dist` | `%IKAROS_ROOT%\runtime\MCPServe\gitnexus\gitnexus\dist\cli\index.js` |
| `mcp.gitnexus.node` | node 入口, = `core.node` |
| `mcp.gitnexus.version` | "1.7.0" |
| `mcp.gitnexus.command_arg` | "mcp" |

### 4.7 Ports (`ports.*`) — 5 个常用端口

| Key | Port |
|---|---|
| `ports.llama` | 8080 |
| `ports.bridge` | 7860 |
| `ports.embedding` | 8587 |
| `ports.webui` | 8648 |
| `ports.webui_internal` | 8649 |

---

## 5. HERMES 兼容变量 (`HERMES_*` — 旧脚本)

> 12 个 `HERMES_*` 变量是 Ikaros v3 之前的"老 hermes-agent"项目使用的命名空间。
> 为**不破坏历史脚本**而保留 —— 它们的值 1:1 镜像 `IKAROS_*`。

| Ikaros | HERMES 兼容 | 同指向 |
|---|---|---|
| `IKAROS_ROOT` | `HERMES_ROOT` | 项目根 |
| `IKAROS_BIN` | `HERMES_BIN` | `bin/` |
| `IKAROS_PYTHON` | `HERMES_PYTHON` | portable-python |
| `IKAROS_DATA` | `HERMES_DATA` | `data/` |
| `IKAROS_HERMES_HOME` | `HERMES_HOME` | 运行时 hermes home |
| `IKAROS_RUNTIME` | `HERMES_RUNTIME` | `runtime/` |
| `IKAROS_DEPS` | `HERMES_DEPS` | `deps/` |
| `IKAROS_MODULES` | `HERMES_MODULES` | `modules/` |
| `IKAROS_LOGS` | `HERMES_LOGS` | `data/logs/` |
| `IKAROS_CONFIG\hermes.yaml` | `HERMES_CONFIG` | 路径到 `hermes.yaml` |
| `IKAROS_MEMORY_MODELS` | `HERMES_MODELS` | `models/` |
| `IKAROS_HERMES_AGENT` | `HERMES_AGENT_ROOT` | `hermes-agent/` |

---

## 6. 跨语言 / 跨 shell 的处理差异

### CMD vs PowerShell

| 触发方式 | CMD | PowerShell |
|---|---|---|
| Bootstrap | `call ikaros-env.bat` | `. ikaros-env.ps1` |
| 读 JSON | `for /f "delims=" %%V in ('type ...json') do ...`(艰难) | `Get-Content ... \| ConvertFrom-Json` |
| 检查生效 | `echo %PATH%` 头部 | `$env:PATH -split ';'` 前 7 项 |

### Python / Node 的导入

| 工具链 | 看哪个变量 | 验证 |
|---|---|---|
| Python | `PYTHONPATH` (`IKAROS_ROOT` + `IKAROS_HERMES_AGENT`) | `python -c "import sys; print(sys.path)"` |
| Python (portable) | `portable-python/python312._pth` 内部机制 | 这玩意 `PYTHONPATH` **可能被屏蔽** |
| Node.js | `NODE_PATH` (= `IKAROS_RUNTIME\node23\node_modules`) | `node -e "console.log(require('module').globalPaths)"` |
| npm | `NPM_CONFIG_PREFIX` (空) | `npm config get prefix` 应指向项目 |

### 不同 agent 怎么知道要看这里

**入口线索**(后续接手):

1. `AGENTS.md` §16 (Portability Audit) 提过 `deps/hermes-env.bat` 是参考实现
2. **`Ikaros-environment/PATH-LAYER.md`**(本文件)
3. **`Ikaros-environment/README.md`** 已有的"环境变量列表"章节(20+ 个表格行)
4. **`Ikaros-environment/ikaros-env.bat` 顶部注释**(`REM  Centralized path config for all Ikaros components.`)

> **.PS1 顶部**也有同样注释。所以从 bat 或 ps1 任一份入手,都能看到自描述。

---

## 7. 我怎么确认 `ikaros-env.{bat,ps1}` 一致

两个文件由**同一个权威源**生成: `ikaros-paths.json`。 改 `ikaros-paths.json` 后,需要跑 `Ikaros-environment/scripts/sync-env-from-json.py` 重新生成两个脚本(目前**这个脚本还没写** —— A2 的下一步工作)。 改动:

```bash
# (A2 之后的工作 — 现在确认一致性靠手动)
diff <(grep '^set "IKAROS_' ikaros-env.bat) \
     <(grep '\$env:IKAROS_' ikaros-env.ps1 | head -100)
```

---

## 8. 接手指南 — 4 个常见场景

### 场景 1: 用户报告 "`python` 不对版本"

- 确认用户已经从 `cmd`/`powershell` 跑了 `call ikaros-env.bat` 或 `. ikaros-env.ps1`
- 跑 `where python` —— 应该命中 `%IKAROS_ROOT%\portable-python\python.exe`
- 如果指向 `%LOCALAPPDATA%\Programs\Python\Python3XX\` —— **bootstrap 没生效**

### 场景 2: 用户报告 "`import hermes` ImportError"

- 跑 `python -c "import sys; print(sys.path[:3])"`
- 第一项应该是 `%IKAROS_ROOT%`, 第二项 `%IKAROS_HERMES_AGENT%`
- 如果没有 —— `PYTHONPATH` 没被 `ikaros-env.bat` set

### 场景 3: 用户报告 "`llama-server` 找不到"

- 跑 `where llama-server.exe`
- 应该命中 `%IKAROS_RUNTIME%\llama\b9867\llama-server.exe`
- `IKAROS_LLAMA_DIR` 没在 PATH 里 prepend (Step 9)

### 场景 4: 修改 IKAROS_ROOT(把项目换盘符或备份)

- 跑 `Ikaros-environment/scripts/detect-root.ps1` —— 它有 5 级优先级,自动跟随
- 或者 **`%IKAROS_ROOT%` 是从脚本位置自动推导的**,无需手改

---

## 9. 防干扰 (Step 10 的真意)

```bat
REM ikaros-env.bat Step 10
set "NODE_PATH=%IKAROS_RUNTIME%\node23\node_modules"
set "NPM_CONFIG_PREFIX="
set "PYTHONHOME="
```

这三行的**目的是为了**: 用户可能在自己的 Windows 用户环境里设了这些变量(尤其是 `PYTHONHOME` 经常被 Python 安装器自动添加),导致项目内 `portable-python` 无法被激活。

- **`NPM_CONFIG_PREFIX=`**: 强清空后, npm 的全局命令都不再被系统污染, `--prefix` 必须显式指项目
- **`PYTHONHOME=`**: 强清空后, `portable-python` 是 self-contained, 不读 `C:\PythonXX\python311._pth` 之类的
- **`NODE_PATH=...`**: 显式指向项目 `runtime/node23/node_modules`, npm install 装的东西都在那

**如果你的项目经常因为 PATH / Python / Node 的环境变量打架而异常, 先看 Step 10 这一组。**

---

## 10. 我承认的限制

1. **没有 `Ikaros-environment/scripts/sync-env-from-json.py`** —— 我现在写的 bat 和 ps1 是手写的, **不是从 `ikaros-paths.json` 自动生成的**。 改 JSON 后必须手动同步 bat/ps1
2. **`scripts/validate-paths.py` 本身被 `.gitignore` 排除了**的提法不准确, 实际上 `Ikaros-environment/` 是 tracked, validate-paths.py 是 tracked, 但 .pyc 等可能在 cache —— 具体看 `.gitignore`
3. **`@modelcontextprotocol/server-*` 这一类 stdio MCP server** 走 `core.node` + `mcp.gitnexus.dist` 但**不走 PATH prepend** —— 它们是 `mcp_servers.{name}.args` 直接被 hermes-cli spawn,不需要 `call ikaros-env.bat` 后才能跑
4. **AGENTS.md §1 §2 §3 §4 不再把 `Ikaros-environment/` 作为路径管理"宪法"提** —— 现在我补的就是把这一节写入 agent 的可发现面

---

## 11. 给接手段的速查

```bash
# 在 Ikaros 项目的任意子目录,先 bootstrap, 再做事
call "%IKAROS_ROOT%\Ikaros-environment\ikaros-env.bat"

# 等价 PowerShell
. "$env:IKAROS_ROOT\Ikaros-environment\ikaros-env.ps1"

# 之后:
%IKAROS_PYTHON% --version
%IKAROS_NODE% --version
where llama-server.exe
where npm
echo %PYTHONPATH%
```

```
ikaros-env.{bat,ps1}         ←── 主要入口 (Step 9 = PATH bootstrap)
ikaros-paths.json            ←── 唯一权威源 (60+ paths)
PATH-LAYER.md (本文)         ←── 1:1 映射表
scripts/detect-root.ps1      ←── 5 优先级 IKAROS_ROOT 检测
scripts/validate-paths.py    ←── 12 项路径校验 (运行一次能说 70% 的问题)
README.md                    ←── 普通使用文档
```

> **任何接手智能体**: 当你看到项目需要"修 Python 路径、Node 路径、llama-server 路径、MCP server 路径" 时,**先看 `Ikaros-environment/PATH-LAYER.md`**,再做改动。

---

文件位置: `E:\Ikaros\Ikaros-environment\PATH-LAYER.md`
作者: Ikaros (2026-07-04, 与哥哥对齐"环境层"概念)
许可证: MIT (跟随项目)
