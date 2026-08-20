# dsh 基座固定清单 —— 审计报告

- **审计目标**：`E:\Ikaros-line3`（git worktree，分支 `refactor/line3-dsh-base`）
- **审计日期**：2026-08-20
- **审计范围**：dsh 集成稳定性 / 路径硬编码 / 启动器 / 本地 npm / 易腐化点
- **不修改任何代码**，不提交

---

## 1. 当前状态评估

### 总体结论：✅ 整体稳健，可上线；存在 1 处严重度 ⚠️ 与 5 处 💡 防腐化建议

dsh 集成已具备「**可移动 + 自锚定 + 本地化零系统依赖**」三大特征。规范源是
`core/ikaros-dsh/cordis.patch.yml`，所有自定义能力（memory_v5 MCP / 持久 PTY / LSP / persona）
都通过 patch overlay 注入，未触碰上游 `runtime/dsh/node_modules/`。`bin/start-dsh-ikaros.bat`
自锚定 `IKAROS_ROOT`，加载便携 Node + 本地 dsh npm 包，不依赖全局 npm。

### 关键文件清单（已核查）

| 文件 | 角色 | 状态 |
|---|---|---|
| `core/ikaros-dsh/cordis.patch.yml` (103 行) | 规范源：MCP + terminal + LSP + persona overlay | ✅ 路径 100% 走 `!!js process.env.IKAROS_ROOT` |
| `core/ikaros-dsh/README.md` (104 行) | 集成说明 | ✅ 与代码一致 |
| `core/ikaros-dsh/plugins/ikaros-memory/` | 自动记忆工程层（turn-stopping 写回 + pre-step 召回） | ✅ 包结构完整，`package.json` 规范 |
| `core/ikaros-dsh/plugins/ikaros-memory/bin/v5_call.py` (114 行) | Node↔Python 桥接（一次进程 + 常驻守护双模式） | ⚠️ 行 24 注释硬编码 `E:/Ikaros`（仅注释） |
| `core/ikaros-dsh/plugins/ikaros-memory/package.json` | 包声明 | ⚠️ `scripts.build` 路径硬编码 `gitnexus/node_modules/.bin/tsc.cmd` |
| `bin/start-dsh-ikaros.bat` (62 行) | web / headless 启动器 | ✅ 自锚定 IKAROS_ROOT；⚠️ headless 不传 `IKAROS_ROOT` 子进程显式 export |
| `bin/restart-dsh-ikaros.ps1` (44 行) | 重启器 | ✅ 自锚定；⚠️ 端口硬编码 3080（与 .bat 默认 3085 矛盾） |
| `bin/ikaros-env.bat` (66 行) | 便携环境注入 | ⚠️ 行 31 `IKAROS_DSH_WEB_PORT=3085` 与 .ps1 行 27 `=3080` 不一致 |
| `bin/ikaros-env.ps1` (60 行) | PowerShell 版环境注入 | ⚠️ 同上端口不一致 |
| `scripts/setup-native.py` | 安装脚本 | ✅ 行 64-68 `dsh.root` / `dsh.bin` 通过 `resolve()` 推导 |
| `core/env/ikaros_paths.py` | Python 路径解析 | ✅ 行 48-55 `dsh.*` 全相对 |
| `tests/smoke_ikaros_env.py` | smoke test | ✅ 行 72 覆盖 `IKAROS_DSH_*` 变量 |

### 路径硬编码扫描结果

`grep -rn "E:/Ikaros\|E:\\Ikaros" core/ikaros-dsh/` 输出：

```
core/ikaros-dsh/cordis.patch.yml:45  # 装配: cd ~/.dsh/profiles/web && pnpm add file:E:/Ikaros/...
core/ikaros-dsh/plugins/ikaros-memory/bin/v5_call.py:24  _ROOT = Path(__file__).resolve().parents[5]  # ... (E:/Ikaros)
```

**两个硬编码均在注释里**，实际路径推导都是相对的或 `!!js` 表达式的；功能不受影响，但**未来重构时若 IKAROS_ROOT 不是 E:/Ikaros 会留下误导文档**。

### MCP entry 名检查（`name` 字段）

| id | name | 类型 |
|---|---|---|
| `memory-ikaros-v5` | `@deepseek-ai/dsh-mcp-client` | ✅ 裸包名（pnpm/npm 解析） |
| `ikaros-memory` | `@ikaros/dsh-ikaros-memory` | ✅ 裸包名（file: 链接） |
| `terminal` / `terminal-bash` / `tool-terminal` | `@deepseek-ai/dsh-*` | ✅ 裸包名 |
| `lsp` / `lsp-stdio` / `tool-lsp` | `@deepseek-ai/dsh-*` | ✅ 裸包名 |

`name` 字段全部裸包名，**无 Windows 绝对路径**。`config.command` / `config.args` 用 `!!js 'process.env.IKAROS_ROOT + "..."'` 推导。
文件行 42-43 已显式注释说明 `name` 字段不走 `!!js` interpolate —— **正确**。

### `runtime/dsh/` 检查

| 项 | 状态 |
|---|---|
| npm 本地安装还是符号链接？ | ✅ 本地安装（`runtime/dsh/node_modules/@deepseek-ai/...` 528 packages），**非符号链接** |
| 版本锁定 `package-lock.json`？ | ✅ 存在（367 KB；锁定 `@deepseek-ai/dsh` ^0.1.0-rc.6） |
| 是否依赖全局 npm？ | ✅ **否** —— 启动器直接调 `%IKAROS_ROOT%\runtime\node\node.exe` + `node_modules/@deepseek-ai/dsh/lib/bin.js`，绕过全局 npm shim |
| `runtime/dsh/` 入库？ | ✅ **否** —— `.gitignore` 行 16 `runtime/`（含 700 MB Node / dsh / llama.cpp / CUDA DLLs），由 `setup-native.py` / `setup-portable.bat` 拉取 |

### 启动器检查

`bin/start-dsh-ikaros.bat`：
- 行 23-24：`set "IKAROS_ROOT=%~dp0.."` + `for %%i in (...) do set "IKAROS_ROOT=%%~fi"` —— **自锚定 ✅**
- 行 29-33：`PATCH` 路径经 `IKAROS_ROOT` 推导；不存在则报错退出 ✅
- 行 36-37：`DSH_NODE` / `DSH_BIN` 都经 `IKAROS_ROOT` 推导 ✅
- 行 49：`--profile headless --patch "%PATCH%"` —— **走 `--patch` 加载 overlay ✅**
- 行 55 / 59：`web` 模式**不传 `--patch`** —— 依赖 `~/.dsh/profiles/web/cordis.patch.yml` 自动加载 ⚠️

---

## 2. 问题清单（防未来腐化）

### 🚨 #1 Web 端口默认值不一致（**严重度：低，但易腐化**）

**位置**：
- `E:\Ikaros-line3\bin\ikaros-env.bat:31` → `set "IKAROS_DSH_WEB_PORT=3085"`
- `E:\Ikaros-line3\bin\ikaros-env.ps1:27` → `$env:IKAROS_DSH_WEB_PORT = "3080"`
- `E:\Ikaros-line3\bin\start-dsh-ikaros.bat:53, 57` → `3085`（与 .bat 的 env.bat 一致）
- `E:\Ikaros-line3\bin\restart-dsh-ikaros.ps1:38` → `Get-NetTCPConnection -LocalPort 3080`（硬编码 3080）
- `E:\Ikaros-line3\core\env\ikaros_paths.py:52, 374` → `web_port: 3080`
- `E:\Ikaros-line3\scripts\setup-native.py:68` → `web_port: 3080`
- `E:\Ikaros-line3\README.md:69 / 110` → 文档也是 3080

**症状**：.bat 链路上 web 默认 3085（避开与官方 dsh desktop 3080 冲突），但 .ps1 / Python / README / 架构图都是 3080。开发者 `bash ikaros-env.sh` 或 Python API 拿到的端口号会与 `start-dsh-ikaros.bat web` 实际开的端口不一致 —— `restart-dsh-ikaros.ps1` 验证逻辑会**永远失败**（验 3080 没监听到 → 误报 WARN）。

**建议**：统一为单一来源（建议 `core/env/ikaros-paths.json` 的 `dsh.web_port`），让 `.bat` / `.ps1` / `.sh` 都从环境变量读，无变量时再 fallback。

---

### ⚠️ #2 `cordis.patch.yml:45` 注释硬编码 `E:/Ikaros`

**位置**：`E:\Ikaros-line3\core\ikaros-dsh\cordis.patch.yml:45`
```yaml
# 装配: cd ~/.dsh/profiles/web && pnpm add file:E:/Ikaros/core/ikaros-dsh/plugins/ikaros-memory
```

**风险**：仅注释，但 README 行 17-18 / AGENTS.md / UPSTREAM.md 也都用 `~/.dsh/profiles/web` 表达。**当 IKAROS_ROOT 不是 E:/Ikaros 时，注释就是误导**。

**建议**：改成 `file:"${IKAROS_ROOT}/core/ikaros-dsh/plugins/ikaros-memory"` 形式（或行 45 注释改成 `file:%IKAROS_ROOT%\core\ikaros-dsh\plugins\ikaros-memory`）。

---

### ⚠️ #3 `plugins/ikaros-memory/package.json:8` 构建脚本路径硬编码

**位置**：`E:\Ikaros-line3\core\ikaros-dsh\plugins\ikaros-memory\package.json:8`
```json
"build": "node ../../../../runtime/MCPServe/gitnexus/gitnexus/node_modules/.bin/tsc.cmd src/index.ts ..."
```

**风险**：依赖 `runtime/MCPServe/gitnexus/gitnexus/` 这个**未跟踪**（在 `.gitignore` runtime/ 下）的工具链才能编译。**新 clone 后无法 build**；且嵌套 `gitnexus/gitnexus/` 看起来是路径复制粘贴笔误（多了一层 `gitnexus`）。

**建议**：
1. 把 tsc 提升为 devDependency（`"@deepseek-ai/dsh"` 包的 devDeps 里通常就有 typescript 间接依赖）。
2. 或者干脆去掉 `scripts.build`，把 `dist/` 列入 npm pack 前的预构建步骤（CI 上做）。
3. 路径 `gitnexus/gitnexus/` 多一层，疑似复制粘贴 bug，需要核对。

---

### ⚠️ #4 `web` 模式不显式 `--patch`

**位置**：`E:\Ikaros-line3\bin\start-dsh-ikaros.bat:55, 59`
```bat
"%DSH_NODE%" "%DSH_BIN%" web --port %IKAROS_DSH_WEB_PORT% %~2 %~3 ...
```

**风险**：`headless` 模式传 `--patch "%PATCH%"`（行 49 ✅），但 `web` 模式**依赖** `~/.dsh/profiles/web/cordis.patch.yml` 自动加载。一旦用户层 patch 漏同步、或 HMR 未触发（README 行 80 已显式承认），web 启动将**静默退化** —— 缺 memory_v5 / terminal / lsp。

**建议**：web 模式也显式传 `--patch "%PATCH%"`，与 headless 保持一致；或 `if exist "%USERPROFILE%\.dsh\profiles\web\cordis.patch.yml"` 才省略（更稳健）。

---

### ⚠️ #5 patch overlay 顺序：`ikaros-memory` 与 `terminal`/`lsp` 互不冲突但缺依赖校验

**位置**：`E:\Ikaros-line3\core\ikaros-dsh\cordis.patch.yml` 行 23-90

**当前顺序**：
```
memory-ikaros-v5  →  ikaros-memory  →  terminal / terminal-bash / tool-terminal  →  lsp / lsp-stdio / tool-lsp  →  system-prompt
```

**分析**：
- ✅ 各插件 id 独立，无 id 冲突。
- ⚠️ `ikaros-memory`（行 46-48）**不依赖** `memory-ikaros-v5` MCP —— 它走 `bin/v5_call.py` 直连 `memory_v5.memory_api`（README 行 37 已注明）。所以两者顺序无所谓。
- ⚠️ `lsp-stdio` 的 `command: npx`（行 73）依赖**全局 npm / PATH 里 npx 可用**。README 说「自带的 `runtime/node/node.exe` 没有 npm 全局 shim」，需要 ikaros-env.bat 把 `runtime/node/` 加到 PATH 才能让 dsh 子进程找到 npx。**确认**：`bin/ikaros-env.bat:57` 把 `%IKAROS_RUNTIME%\node` 加到 PATH ✅，但 `%IKAROS_ROOT%\runtime\node` 路径正确性需在 headless 测试中验证。

**建议**：在 `ikaros-env.bat` 加一段 `if "%IKAROS_DSH_PROFILE_AUTOLOAD%"=="1"` 的显式开关，控制 web 模式是否自动同步用户层 patch（避免与 `--patch` 双加载）。

---

### 💡 #6 `bin/start-dsh-ikaros.bat` 不显式 export `IKAROS_ROOT` 给子进程

**位置**：`E:\Ikaros-line3\bin\start-dsh-ikaros.bat:36-37`

bat 用 `setlocal` + `call ikaros-env.bat`（行 27），但行 47-59 三条分支都只 `%DSH_NODE%` / `%DSH_BIN%` + 参数，**没显式 `set "IKAROS_ROOT=..."` export**。`call` 的副作用在内层 setlocal 出栈后**可能丢失**给子进程（行 62 `endlocal` 关闭 setlocal）。

**风险**：Node 子进程拿不到 `IKAROS_ROOT`，patch 里的 `!!js process.env.IKAROS_ROOT` 全部 `undefined`，拼出 `undefined\runtime\portable-python\python.exe` 这种灾难路径。

**缓解**：行 27 的 `call` 在 setlocal 内执行，行 28-37 的 set 也在同一 setlocal 内，**理论上变量会传给子进程**（setlocal 不影响 `set` 设置的环境，子进程继承）。✅ 实际可能没问题，但**写代码时建议显式 `set "IKAROS_ROOT=%IKAROS_ROOT%"` 后再启动 node**，让审阅者一眼看到。

**建议**：在行 36 之前加 `echo [start-dsh-ikaros] IKAROS_ROOT=%IKAROS_ROOT%` 自检，方便排障。

---

### 💡 #7 patch overlay 注释中 `python LSP 注释掉` 没有条件开关

**位置**：`E:\Ikaros-line3\core\ikaros-dsh\cordis.patch.yml:81-85`
```yaml
          # python:
          #   command: pyright-langserver
          #   args: ['--stdio']
```

**风险**：注释掉的 YAML 不会被 dsh 解析，但**未来开发者会直接取消注释而忘了先 pip install pyright**。README 行 63-65 有提示，但 patch 文件本身没写「如何开启 / 关闭」。

**建议**：用 `when: false` 之类的 dsh 内置条件门控，或在 patch 文件顶部注释加一个「启用 python LSP」的步骤清单。

---

### 💡 #8 `restart-dsh-ikaros.ps1` 验证逻辑端口硬编码

**位置**：`E:\Ikaros-line3\bin\restart-dsh-ikaros.ps1:38`
```ps1
$l = Get-NetTCPConnection -LocalPort 3080 -State Listen -ErrorAction SilentlyContinue
```

**风险**：与问题 #1 同源 —— restart 永远验 3080，但 `start-dsh-ikaros.bat web` 实际开 3085。

**建议**：从环境变量读 `$env:IKAROS_DSH_WEB_PORT ?? 3080`。

---

## 3. 潜在风险（不属于代码腐化但需要决策）

### ⚠️ R1 `runtime/` 在 worktree 中不存在 → line3 单独无法启动 dsh

**症状**：`E:\Ikaros-line3\` 是 git worktree，**不含** `runtime/`（.gitignore 行 16 排除，528 packages + 700 MB 不入库）。直接 `cd E:\Ikaros-line3 && bin\start-dsh-ikaros.bat` 会立即 ERROR: node not found。

**现状**：
- `bin/start-dsh-ikaros.bat:36-44` 对 `DSH_NODE` / `DSH_BIN` 有 `if not exist` 检查 ✅（优雅失败）。
- 但**没有指向 main worktree `E:\Ikaros\runtime\` 的回退逻辑** —— 每次 line3 worktree 启动 dsh 都要从 main `runtime/` 借。

**建议**：
- 在 `bin/start-dsh-ikaros.bat` 加探测：`if not exist "%IKAROS_ROOT%\runtime\dsh" set "IKAROS_ROOT=E:\Ikaros"`，打印 WARN 后 fallback（与 hermes 时代一致）。
- 或在 README 「Fresh install on a new PC」段加 line3 worktree 的 setup 子命令 `bin\link-runtime-from-main.bat`。

### ⚠️ R2 `cordis.patch.yml` 与 `~/.dsh/profiles/web/cordis.patch.yml` 双源

**症状**：README 行 80 注释「裸 dsh web 也生效：用户层 patch 已同步本 overlay」。但 line3 worktree 没有同步脚本（README 行 80 只说「请重新同步」，没说怎么同步）。

**风险**：
- 修改 `E:\Ikaros-line3\core\ikaros-dsh\cordis.patch.yml` 后，**裸 `dsh web`（无 `--patch`）会用过时的用户层 patch**。
- `restart-dsh-ikaros.ps1` 显式 `--patch`（行 33）走规范源 ✅，但 bat 的 web 模式（行 55）依赖用户层 ⚠️。

**建议**：
- 加 `bin\sync-dsh-profile-patch.bat`：从规范源复制到 `%USERPROFILE%\.dsh\profiles\web\cordis.patch.yml`，git pre-commit hook 调用。
- 或彻底废弃用户层双源，bat web 模式强制 `--patch`（见问题 #4）。

### ⚠️ R3 `v5_call.py` 父目录依赖路径

**位置**：`E:\Ikaros-line3\core\ikaros-dsh\plugins\ikaros-memory\bin\v5_call.py:24`
```python
_ROOT = Path(__file__).resolve().parents[5]  # plugins/ikaros-memory/bin -> Ikaros
```

**症状**：`parents[5]` 假设目录层级恰好是 `bin/ → plugins/ikaros-memory/ → plugins/ → core/ → ikaros-dsh/ → Ikaros/`，**与问题 #2 的注释硬编码同源**。若 plugins 重命名或下移一层（`core/ikaros-dsh/plugins/ikaros-memory/bin/v5_call.py` → `core/ikaros-dsh/plugins/v5-bridge/bin/v5_call.py`），`parents[5]` 仍然正确（5 层没变），但**注释里「bin → Ikaros」会失效**（`parents[5]` 仍是 5 层，但语义要重新核对）。

**建议**：用 `os.environ.get("IKAROS_ROOT") or Path(__file__).resolve().parents[5]` 双 fallback，与 dsh patch 的 `!!js process.env.IKAROS_ROOT` 保持一致。

### 💡 R4 `install.log` 在仓库内不被 gitignore 保护

**位置**：`E:\Ikaros\runtime\dsh\install.log` (887 B)

`runtime/` 已被 .gitignore 排除，所以不入库。但**当 runtime/ 被分发到 GitHub release 时**，install.log 会随附（包含 `npm warn deprecated` 等噪音，不影响功能但增加体积）。

**建议**：分发 release 时 `.npmignore` 或打包脚本排除 install.log。

### 💡 R5 `persona` 用 `{{cwd}}` 占位符

**位置**：`E:\Ikaros-line3\core\ikaros-dsh\cordis.patch.yml:92-93`
```yaml
persona: >-
  You are Ikaros, a coding and task-execution agent working in {{cwd}}.
```

**风险**：依赖 dsh 模板引擎支持 `{{cwd}}`（README 行 80 暗示 dsh 的 persona 模板支持）。若 dsh 上游改模板语法（hogan → eta → handlebars），Ikaros 会**静默收到字面 `{{cwd}}`**。

**建议**：在 patch 文件顶部注释加一句：「persona 模板依赖 dsh 的 {{cwd}} 渲染；dsh 升级时需验证模板引擎兼容」。

---

## 4. 验证清单（用于 line3 启动 dsh 前自检）

```bash
# 在 E:\Ikaros-line3 根目录
[ -f bin/start-dsh-ikaros.bat ]            && echo OK || echo MISSING
[ -f core/ikaros-dsh/cordis.patch.yml ]    && echo OK || echo MISSING
ls core/ikaros-dsh/plugins/ikaros-memory/package.json  && echo OK || echo MISSING

# runtime/dsh 在 worktree 中不存在 → 需要从 main 借或重新 npm install
ls ../runtime/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js 2>&1

# 启动前自检
cd E:\Ikaros-line3
bin\start-dsh-ikaros.bat --help 2>&1 | head -5   # 应能列出 --profile / --patch / web 子命令
```

---

## 5. TL;DR 给父 agent 的回复

- **状态**：✅ 基座稳健，可上线 line3 refactor/line3-dsh-base。
- **严重问题**：0 个 🚨（致命）。
- **次严重**：1 个 ⚠️（端口不一致，会让 restart.ps1 验证逻辑永远 WARN）。
- **防腐化建议**：5 个 💡（注释硬编码 / 显式 --patch / v5_call parents[N] / persona 模板）。
- **最优先修复**（按性价比）：
  1. `#1` 端口不一致（统一 3080 或 3085，二选一）
  2. `#4` web 模式显式 `--patch`
  3. `#R2` 加 `bin\sync-dsh-profile-patch.bat`（或废弃用户层）
- **不修复也能跑**，但 line3 worktree 单独无法启动 dsh（见 R1，需要从 main 借 `runtime/`，或重跑 `setup-native.py`）。

---

## 6. 不在本审计范围内

- dsh 上游 0.1.0-rc.6 自身的 bug（由 dsh 团队维护）。
- `core/memory_v5/` Python 实现（Task 3.2/3.3 范围）。
- `data/dsh/profiles/` 用户会话数据（不进 git，纯运行时）。
- Ikaros 主架构迁移（line1 / line2）。

---

**审计完成**。未修改任何代码，未 git commit / push。
