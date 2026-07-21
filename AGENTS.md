# Ikaros — Project Memory Bank

> **Read this first** when picking up the project after a break.
> This file captures the project state, architecture, modification history,
> debugging tips, and the gotchas we hit along the way.

> **Last revised:** 2026-07-20 (launcher switched from Rust `ikaros.exe` to control panel `bin/ikaros-control.bat`; b10000-cuda confirmed usable via panel start). Prev: 2026-07-16 (portable-python moved under runtime).
> Two bugs killed the startup chain:
> 1. `Ikaros-environment/init.bat` had UTF-8 Chinese comments — cmd.exe parses as GBK,
>    multi-byte UTF-8 sequences become garbage commands → instant crash on double-click.
>    Fix: all comments converted to pure ASCII. **Rule: .bat files MUST be pure ASCII.**
> 2. `ikaros-desktop-pet/main.py` imported `from modules.model_manager.llm_manager import LLMManager`
>    but the module didn't exist → `ModuleNotFoundError`. Fix: created
>    `modules/model_manager/llm_manager.py` stub (scans local GGUFs + queries :8080 + cloud models).
> Also: memory watchdog now manages both :8587 (embedding) AND :8080 (local LLM for v3 extraction).

Previous: 2026-07-16 (bin/*.bat → Rust `ikaros` launcher + portable-python under runtime).
- **启动器切换为控制面板 `bin/ikaros-control.bat` (2026-07-20)**: 双击 → 拉起 `tools/ikaros-dashboard/server.py`
  (Web 面板,打开 http://127.0.0.1:9100),面板内 "start" 按钮拉起后端整栈(含 Memory watchdog + :8587 嵌入 + :8080 LLM),
  **内置完整 CUDA 环境装配**(前置 `runtime/llama/b10000-cuda` 进 PATH + 注入 `IKAROS_*` 变量)。**不要手动裸跑 `llama-server.exe`**——
  会缺 CUDA DLL 环境→加载期 SIGSEGV(已实测 f32/f16 均崩)。旧 Rust 启动器 `bin/ikaros.exe` 从未部署(仅源码
  `Ikaros-environment/ikaros-cli/src/`),正式舍弃。旧 12 个 .bat 仍在 `bin/legacy/`(回滚用)。
- **portable-python 迁入 runtime/**: 原 `E:\Ikaros\portable-python` 经 robocopy 复制到
  `E:\Ikaros\runtime\portable-python` (旧目录因 Defender 锁成孤儿待清理)。`python312._pth` 去掉失效的多级
  `..`,改由 `sitecustomize.py` 用 `__file__` 注入项目根 (5 层 parent)。环境变量源头 (ikaros-env.bat/ps1、
  ikaros-paths.json、hermes-desktop.bat)、detect-root (rust+ps1)、validate-paths.py、各文档同步改为
  `runtime\portable-python`。

Previous: 2026-07-04 (push-to-github cleanup + Ikaros v4 ship).
删去 265 个旧文件 (`bin/*.bat` / `bin/*.ps1` / `modules/*` / `bridge-rs/` / `bin/ikaros-desktop-pet-tauri/`)，
一次性 push 到 `ArtificialAngels/Ikaros` origin/main as commit **11d682f**。
新增 Ikaros v4 项目: `Ikaros-Live2D/` / `Ikaros-environment/` / `Ikaros-memory/`
(不含 data/) / `tools/ikaros-memory-v3/` (Rust)。
`.gitignore` 末尾新增 30+ 行 lockdown 块 (lines 280-314)，永久守护：
- `oldcode/` 递归整树（旧架构数据）
- `Ikaros-memory/data/v3.db` + `Ikaros-memory/data/space/`（DNA 记忆）
- `Ikaros-memory/models/*.gguf`（5GB 模型）
- `Ikaros-Live2D/src-tauri/target/` + `Cargo.lock`（Rust 编译产物）
- 凭据: `.env` / `.vault` / `auth.json` / `config.yaml` 已早被守住
- 测试诊断脚本: `tests/_debug_*` / `_*test*` / `_diagnose_*` / `benchmark_*` /
  `check_model_*` / `clean_v3_*` / `smoke_*` / `test_ikaros_dojo_daily` /
  `test_sem_*` 不推送
- `bin/oldcode/`（root oldcode 的镜像）

bridge-rw/webui_proxy/Rust bridge — 旧架构，全部扫清于本次提交。
推完即刻：**origin/main HEAD = `11d682f`**，本地比 remote 36 commits 全部追上。

Previous: 2026-06-27 (ikaros-desktop-pet: Neuro 语音气泡联动 + 右键菜单全功能集成。
Live2D 页面新增 WebSocket 连接 `ws://127.0.0.1:7860/v1/voice/ws`，自定义语言气泡
`#neuro-bubble` 和状态指示器 `#neuro-state`，处理 transcription/thinking/status/done/state
消息类型，自动重连。右键菜单集成全部 wl-live2d 功能（切换模型/服装/截图/帧检测/比例调节），
移除原生菜单按钮，CDN 扩展至 18 个模型。)
>
> Previous: 2026-06-16c (added `modules/webui_proxy` as a thin Python
> reverse-proxy in front of `hermes-web-ui` on :8648. The npm package's
> `/api/hermes/usage/stats` SQL was broken (GROUP BY model only, no
> provider/profile/base_url split, no exclusion of internal sessions like
> `source='tool'`, `id LIKE 'compress_%'`, `parent_session_id IS NOT NULL`,
> `archived=1`). The proxy intercepts only that one path and serves it
> from corrected SQL against `data/hermes-agent/state.db`; everything
> else (chat SSE / WebSocket / static assets) is passed through to
> upstream :8649. **Also fixed the PowerShell Runspace-after-exit bug**
> in all 4 start.ps1 modules — using `add_OutputDataReceived` to drain
> child stdio crashed the PowerShell host after start.ps1 returned,
> which silently took the python/node child with it. Now we let the
> child INHERIT PowerShell's stdio (already captured by the supervisor
> into the per-module log files). See §0.8).
>
> Previous: 2026-06-15f (extracted auto-restart watchdog out of
> `bin/hermes-supervisor.py` into a standalone detached
> `bin/hermes-watchdog.py` daemon. supervisor `cmd_start` no longer hangs
> in a `while True: sleep(10)` loop — it spawns the watchdog via
> `DETACHED_PROCESS` then returns, so `hermes-all.bat` exits cleanly.
> Watchdog writes its PID to `data/logs/hermes-watchdog.pid` and is
> killed by `hermes-stop.bat` (or `supervisor --watchdog-stop`) before
> the supervisor stops services. See §0.7f).
>
> Previous: 2026-06-15e (retired the 3 unused `data/knowledge`,
> `data/memory`, `data/skills` directories — all empty (or near-empty
> with stale debug payload); the real KB / memory / skills live under
> `hermes/data/`. Synced `hermes/config.py` (drop 3 path defaults),
> `hermes/workspace.py` (drop 3 `WHITELIST_DIRS` entries), and
> `config/hermes.yaml` (drop 2 explicit `path:` fields). Net −3 dirs +
> −6 source lines. `hermes-agent/` (116 MB upstream source) and
> `data/hermes-agent/` (47 MB upstream runtime state) are **not**
> touched — see §0.7e for the "why two dirs both matter" explainer).
> Previous: 2026-06-15b (removed duplicate browser open in
> `hermes-all.bat`; the npm package's own health-check hook already
> opens the browser — see §0.7b).
>
> Previous: 2026-06-13 (v3 phase close-out — privacy cleanup,
> `HERMES_BIN` ENOENT fix, full `.gitignore` overhaul, docs refresh;
> repo **renamed** `hermes-agent` → `hermes-agent-portable` on 2026-06-13,
> origin updated, all live doc URLs refreshed; §10 historical log
> entries retain the pre-rename URL for accuracy; **2026-06-13 (junction
> fix)** — first commit of `deps/` to git (previously local-only),
> refactored `deps/hermes-env.{bat,ps1}` to resolve `%HERMES_RUNTIME%`
> / `runtime\node23` directly instead of via four `deps\node\tools\
> llamacpp\bin\python-test` directory junctions whose absolute
> reparse-point targets broke the project when it was moved to a
> new drive letter (E: -> F:), and added an auto-heal step that
> rmdir's any leftover junction on startup — see §0.5).
> For the user-facing introduction, see [README.md](README.md).

---


## Revision Timeline (chronological; see git log for details)

- **2026-07-07b** - Ikaros v3 memory fully removed (after V4 cutover). `git rm` (recoverable from history): `Ikaros-memory/ikaros-memory-v3.py`, `vector_search.py`, `memory_reflect.py`, `tools/ikaros-memory-v3/` (Rust). Sent to Recycle Bin (recoverable): `Ikaros-memory/data/v3.db` (256K), `Ikaros-memory/.tmp_test_migrate/`, 3 V3 test files (`tests/test_sem_extract.py`/`benchmark_ikaros_memory.py`/`clean_v3_test.py`), Hermes `plugins/ikaros_v3/` (orphaned dynamic-loader), `chroma_fix_verify.py` (verified deleted V3 `vector_search`). Config follow-up: `ikaros-env.bat/.ps1` `IKAROS_MEMORY_SCRIPT`→`v4/store.py`; `ikaros-paths.json` script/db→v4; `validate-paths.py` checks `v4/v4.db`; `bin/ikaros-mem.bat` dropped v3 route; `gitignore_guard.py` v3.db→v4/v4.db. `migrate_from_v3.py` retained (V4 asset, gracefully handles missing v3.db). All live paths run on V4 only; re-verified compile + v4 wiring.

- **2026-07-07** - PyQt6 desktop pet removed; architecture is now Tauri v2 + Live2D pet (`Ikaros-Live2D`) + Hermes Desktop (Electron) + Hermes Dashboard (:9119) + memory watchdog (:8587/:8080). Actions:
  1. Deleted `bin/oldcode/` (756 files, 73MB, gitignored legacy) and empty root `oldcode/` — sent to Recycle Bin (recoverable).
  2. Deleted entire `bin/ikaros-desktop-pet/` (PyQt6 GUI: `main.py`, `detached.py`, `audio_engine.py`, tests, launchers, character assets, `live2d/`, monitor). The live voice backend was NOT in this dir.
  3. Relocated shared backend `cloud_chat.py` `bin/ikaros-desktop-pet/cloud_chat.py` -> `bin/cloud_chat.py` (imported by `bin/ikaros-voice-ws.py` [:7870], `bin/ikaros-repl.py`, `tests/smoke_ikaros_v3.py`). Fixed `sys.path` in `ikaros-repl.py` + `smoke_ikaros_v3.py`.
  4. Live voice chain preserved: Tauri `App.vue` -> `ws://127.0.0.1:7870/v1/voice/ws` -> `bin/ikaros-voice-ws.py` -> `cloud_chat.py` -> `Ikaros-memory/cogno_5d.py`.
  5. Verified: `py_compile` + `from cloud_chat import cloud_chat` OK.

- **2026-07-04b** - ikaros-start.bat crash fix (2 bugs):
  1. `Ikaros-environment/init.bat` had UTF-8 Chinese comments → cmd.exe GBK parse fail → instant crash.
     All .bat comments converted to pure ASCII. **GOTCHA: .bat = ASCII only, no exceptions.**
  2. `modules/model_manager/llm_manager.py` was missing → pet `ModuleNotFoundError`.
     Created `LLMManager` stub: scans `data/models/` + `Ikaros-memory/models/` for GGUFs,
     queries `:8080/v1/models`, knows cloud model names, supports async fetch + cache + persist.
  3. Memory watchdog (`bin/ikaros-memory-watchdog.py`) now **manages** :8080 LLM (was detect-only).
     Embedding :8587 + LLM :8080 both auto-started and auto-restarted by watchdog.
     local LLM runs on :8080 for v3 memory extraction (saves cloud tokens).
- **2026-06-27 (Quest handoff)** - 灵感挖掘 + 桥问题交接。Ikaros 不写代码改自己, 把 OpenDesktop-Pet 6 大特性写到
  `data/ikaros-coordination/handshake.2026-06-27.odp-inspiration.json` (7415B): P0 三层记忆 / P1 主动互动循环 / P1 截屏视觉 / P2 TTS 多引擎 / P3 身体分区点击。
  桥 uvicorn 应用层卡死问题写到 `handshake.2026-06-27.bridge-uvicorn-unresponsive.json` (7519B): 7 修复建议 + 测试命令。
  协作 README 更新 (tracked), git commit + **不 push** (哥哥等加密源码)。
- **2026-06-27** - ikaros-desktop-pet: Neuro 语音气泡联动 + 右键菜单全功能集成。
  `live2d/index.html` 新增 WebSocket 连接 `ws://127.0.0.1:7860/v1/voice/ws`，
  自定义语言气泡 `#neuro-bubble`（顶部居中，暗色半透明，自动消失）和状态指示器
  `#neuro-state`（底部，emoji + 状态文字）。处理消息类型: transcription/thinking/
  status/done/state，WebSocket 自动重连（3s 间隔）。`main.py` 新增 `show_bubble()`/
  `show_neuro_state()` Python API。右键菜单集成全部 wl-live2d 功能（切换模型/服装/
  截图/帧检测/比例调节/随机模型），移除原生菜单按钮（`menus:[]`, `hitFrame:false`），
  MutationObserver 永久移除 HTML title tooltip，CDN 扩展至 18 个模型，修复循环切换。
- **2026-06-26** - disable `modules/agent_bridge_stub` (renamed `module.json` to
  `module.json.disabled`), add `HERMES_AGENT_ROOT` env var to
  `modules/webui/start.ps1`. The stub was blocking :18765 so the npm webui's
  real broker could never start; chat-run requests hung indefinitely.
- **2026-06-16c** - `modules/webui_proxy` (new thin Python reverse-proxy on :8648) +
  PowerShell Runspace-after-exit fix in all 4 `start.ps1` modules. See §0.8.
- **2026-06-16** - root completion check: `bin\hermes-supervisor.bat` now calls
  `hermes-root.bat verify` to fail-fast on stale env var or `.hermes-root` cache
  (prevents supervisor launching lla with a broken path after USB drive-letter swap).
  `bin\hermes-all.bat` double-verifies at the user-facing entry for clearer error attribution.
- **2026-06-16b** - skills 路径归属澄清 + 根目录脚本收敛: bridge `HERMES_HOME=data/hermes-agent`
  所以对话走 `data/hermes-agent/skills/`,源码仓库与 npm dist 都不参与执行;51 个 DWG/DXF
  调试脚本一次性移到 `tools/dwg/`(原文件 untracked,无 git rename 历史),
  `bridge_pool.py` system prompt 追加 `PROJECT_CONVENTIONS` 约束 LLM 落盘位置。
- **2026-06-15f** - extract watchdog: new `bin/hermes-watchdog.py` (detached); supervisor drops
  its in-line `while True: sleep(10)` loop (-25 lines).
- **2026-06-15e** - retire 3 unused `data/{knowledge,memory,skills}` directories (all empty);
  sync `hermes/config.py`, `hermes/workspace.py`, `config/hermes.yaml`.
- **2026-06-15d** - compress revision-log comments round 2 (Python / bridge_runtime).
- **2026-06-15c** - compress revision-log comments round 1 (bat / ps1: supervisor / hermes-all).
- **2026-06-15b** - drop duplicate browser-open in `hermes-all.bat`: the npm package has
  its own health-check hook that opens the browser.
- **2026-06-15a** - drop `.\hermes-web-ui\` dev source (npm global is the single source).
- **2026-06-15** - `setup-portable.bat` gains a Node.js download step.
- **2026-06-14** - junction sweep: module.json / `fix-eol.py` / smoke tests.
- **2026-06-13** - junction de-coupling for drive-letter portability: the 4 `deps/` junctions
  are gone, replaced by direct `runtime/*` paths; auto-heal rmdir any leftover junction
  on startup; repo renamed `hermes-agent` -> `hermes-agent-portable`; privacy scrub +
  `.gitignore` overhaul.

---

## 0.8. 2026-06-16c — `modules/webui_proxy` (port 8648) + PowerShell Runspace-after-exit fix

Two changes shipped in the same revision because they share a debugging session.

### Part A — `modules/webui_proxy/`

**Symptom.** The npm package `hermes-web-ui` ships a `/api/hermes/usage/stats`
endpoint backed by an obfuscated `Pw()` function in `dist/server/index.js`.
Inspecting the SQL it generates against `data/hermes-agent/state.db` shows
two problems:

1. `GROUP BY model` only — sessions that hit the same model name across
   different providers (`custom` vs `minimax-cn`) or base URLs
   (`http://127.0.0.1:8080/v1` vs `https://api.minimaxi.com/v1`) get
   bucketed together. Cross-provider cost reporting is meaningless.
2. No exclusion of internal sessions — `source='tool'`, `id LIKE
   'compress_%'`, `parent_session_id IS NOT NULL`, and `archived=1` rows
   show up alongside the real user sessions, inflating the counts.

**Why a separate proxy instead of patching the npm package?**

- The npm package is updated via `npm install -g hermes-web-ui`. Any
  hand-edit to `dist/server/index.js` is overwritten on the next install.
- The Vue 3 frontend is hard-coded to fetch `/api/hermes/usage/stats`;
  we cannot change the path.
- A thin Python proxy intercepts only the broken endpoint and forwards
  everything else (chat SSE, WebSocket, static assets, /api/hermes/health,
  /api/hermes/logs/*) unchanged.

**Implementation.**

- `modules/webui_proxy/webui_proxy.py` (446 lines, stdlib only —
  `http.server`, `urllib`, `sqlite3`). One `ProxyHandler` class with
  `do_GET/POST/PUT/DELETE/PATCH/OPTIONS`; `do_GET` checks for
  `USAGE_STATS_PATH = "/api/hermes/usage/stats"` and dispatches to
  `_handle_usage_stats()` (SQL) or `_proxy_pass()` (urllib to upstream
  with hop-by-hop header filtering and a 502 fallback for "upstream
  unavailable" / "connection refused" / "timed out").
- `modules/webui_proxy/start.ps1`, `stop.ps1`, `health.ps1`,
  `module.json` follow the same pattern as the other modules
  (`--port`, `--upstream`, `--state-db` CLI flags; port 8648; depends
  on `webui` which must be listening on 8649 first).
- `modules/webui/module.json` no longer claims port 8648; it now
  declares port 8649 with a `description` that explicitly says
  "proxied to :8648 via modules/webui_proxy".

**SQL diff** (webui_proxy's corrected version vs the npm package's):

```sql
-- npm Pw() (the broken version):
SELECT model, SUM(...) FROM sessions
WHERE started_at > ? AND model IS NOT NULL
GROUP BY model
ORDER BY ...

-- webui_proxy.py (the fixed version):
SELECT
    model, billing_provider AS provider, billing_base_url AS base_url,
    SUM(input_tokens) AS input_tokens, ...,
    COUNT(*) AS sessions,
    SUM(COALESCE(api_call_count, 0)) AS api_calls
FROM sessions
WHERE started_at > ?
  AND model IS NOT NULL AND model != ''
  AND source != 'tool'                              -- exclude tool sessions
  AND id NOT LIKE 'compress_%'                      -- exclude compressor
  AND (parent_session_id IS NULL OR parent_session_id = '')
  AND COALESCE(archived, 0) = 0                     -- exclude archived
GROUP BY model, billing_provider, billing_base_url  -- split by billing lane
ORDER BY sessions DESC, model ASC
```

Note: the `profile` column from the npm package's `Pw()` does not
exist in `state.db.sessions` (the project enforces profile per-request
via the `X-Hermes-Profile` header upstream; it's not persisted in
the session row). Including `profile` in the GROUP BY would raise
a SQL error, so the proxy splits by `(model, provider, base_url)` —
the two dimensions that actually identify a billing lane in the
local DB. The date axis is dense (zero-rows for empty days are
filled in `daily_usage`) so the chart x-axis is always contiguous
from `today - days` to `today` (inclusive of today, so `days=30`
actually returns 31 rows — see `tests/smoke_webui_proxy.py` for
the exact expectations).

**Acceptance test** (`tests/smoke_webui_proxy.py`, 8 scenarios):

1. `GET /api/hermes/usage/stats?days=30` → 200, `total_sessions=4`
   (was 5 before the internal-session exclusion), `model_usage`
   rows split by (model, provider, base_url), `daily_usage` 31 rows.
2. `days=90` → 91 daily rows.
3. `days=365` → 366 daily rows.
4. `days=abc` (invalid) → falls back to 30 → 31 daily rows.
5. `days=1000` (out of range) → falls back to 30 → 31 daily rows.
6. `GET /api/hermes/health` → 401 unauthorized (passthrough to
   upstream 8649, which returns 401 because no cookie).
7. `GET /` → 200, body `<!doctype html>...<html lang="zh-CN">...`
   (the Vue 3 SPA, passthrough).
8. Stability: 3 consecutive `usage/stats` calls all return 200.

### Part B — PowerShell Runspace-after-exit fix

**Symptom (the killer).** During the Part A testing, the first
supervisor-launched `webui_proxy` accepted the TCP connection on
:8648, returned 0 bytes of HTTP, and the client got
`http.client.RemoteDisconnected`. But the same `webui_proxy.py` run
from a plain PowerShell prompt (no supervisor) returned 200 with the
correct JSON. The bug was not in the Python — it was in how
`start.ps1` was talking to the child process.

**Root cause.** All 4 `modules/*/start.ps1` (bridge, llm_engine,
webui, webui_proxy) used this pattern to capture the child
process's stdout/stderr into a per-module log file:

```powershell
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError  = $true
$proc = [System.Diagnostics.Process]::Start($psi)
$logWriter = [System.IO.StreamWriter]::new($logPath, ...)
$logWriter.AutoFlush = $true
$proc.add_OutputDataReceived({
    if ($null -ne $_.Data) { $logWriter.WriteLine($_.Data) }
})
$proc.BeginOutputReadLine()
# ... later: $proc.Dispose(); $logWriter.Dispose(); exit 0
```

This looks innocent but it has two Windows-specific traps:

1. **`StreamWriter` ctor on a locked file.** The supervisor's
   `start_module()` already opened the same `data/logs/<module>.log`
   in append mode (`log_f = open(path, "a", buffering=1)` —
   `bin/hermes-supervisor.py:313`) and only closes it after the
   module's port-health check passes. So when `start.ps1` runs and
   tries to `StreamWriter::new($logPath, $true, ...)`, the ctor
   can fail with `IOException: access denied` because the
   supervisor is still holding the file open. The `try/catch`
   silences that, but then `$logWriter` is `$null` and the
   `add_OutputDataReceived` callback throws
   `PSInvalidOperationException: PropertyNotFound` (the .NET
   object's `AutoFlush` property is gone).

2. **Runspace-after-exit crash.** Even if (1) is dodged (e.g. by
   using `try/catch` and `$null -ne $logWriter` guards), the
   `add_OutputDataReceived` callback is a **PowerShell script
   block** which needs a Runspace to execute. The script block
   runs on a `System.Threading.ThreadPool` thread. When
   `start.ps1` exits (via the `exit 0` at the bottom), the
   PowerShell host disposes the Runspace. The next stdout/stderr
   event on the background thread then throws
   `PSInvalidOperationException: There is no Runspace available`,
   the PowerShell process crashes ("The PowerShell process will
   exit"), and any detached child process loses its inherited
   stdout/stderr pipe — which makes the next `print()` /
   `sys.stderr.write()` in the child raise `BrokenPipeError` and
   the child exits.

That last sentence is the silent-killer. The supervisor polls
`:8648` for `LISTENING` *before* the Runspace crash happens
(typically the child binds the port within ~50 ms of launch).
So the supervisor sees `[OK] webui_proxy (:8648)`, reports
success, spawns the watchdog, and exits. A few hundred ms
later, the child process dies from the broken pipe, but the
watchdog hasn't re-checked yet (its interval is 10 s). If the
user happens to load the UI in those first few seconds, it
works; if they wait until 10 s later, the child is already
dead, the watchdog restarts it, the race restarts, and the
dashboard shows intermittent `RemoteDisconnected`.

**Fix (applied to all 4 start.ps1 modules).** Drop the
`add_OutputDataReceived` / `StreamWriter` pattern entirely.
Let the child **inherit** PowerShell's stdio:

```powershell
$psi.UseShellExecute        = $false
$psi.RedirectStandardInput  = $false
$psi.RedirectStandardOutput = $false   # <-- key change
$psi.RedirectStandardError  = $false   # <-- key change
$proc = [System.Diagnostics.Process]::Start($psi)
```

PowerShell's stdio is itself redirected to the per-module log
files by the supervisor (`stdout=log_f, stderr=err_f` in
`bin/hermes-supervisor.py:329-330`), and on Windows file
handles are duplicated on `CreateProcess` when the child
doesn't redirect its own stdio. So the child writes go
straight to the same `data/logs/<module>.{log,err}` files —
without any in-PowerShell buffering / capture layer, without
a Runspace, and without a `StreamWriter` that can lock
against the supervisor's log file handle.

We also dropped the `[System.IO.File]::WriteAllText(...)`
truncate in each `start.ps1` for the same reason (the
supervisor's open file handle conflicts with the truncate;
better to let the supervisor own truncation, which it does
at line 310-311 of `bin/hermes-supervisor.py`).

**Files changed (Part B):**

- `modules/webui_proxy/start.ps1` — drop truncate, drop
  `StreamWriter`/`add_OutputDataReceived`, switch to
  inherit-stdio + 27-line rationale comment.
- `modules/webui/start.ps1` — same.
- `modules/bridge/start.ps1` — same.
- `modules/llm_engine/start.ps1` — same.
- (No change to `modules/env_bootstrap/start.ps1`; that one
  is a one-shot tool, not a long-running service, so it
  doesn't hit the same race.)

**Acceptance test** (run after the fix):

```
$ python bin/hermes-supervisor.py --start
  Order:  env_bootstrap -> llm_engine -> bridge -> webui -> webui_proxy
  [OK]   llm_engine (:8080)
  [OK]   bridge (:7860)
  [OK]   webui (:8649)
  [OK]   webui_proxy (:8648)
  STARTED: 5 module(s)

$ python tests/smoke_webui_proxy.py
  === ALL TESTS PASSED ===        # 8/8 scenarios, ~250 ms total
```

**Net diff vs the broken state:**

- 4 start.ps1 files: −logWriter / −errWriter / −add_OutputDataReceived
  / −truncate-LogFile = ~70 source lines removed, ~30 lines of
  rationale comment added.
- 1 new module (`webui_proxy/`): ~480 lines (Python + 4 ancillary
  files).
- 1 new smoke test (`tests/smoke_webui_proxy.py`): 102 lines.
- 1 new port topology (webui on :8649 internal, webui_proxy on
  :8648 public — the supervisor's dependency graph now has
  `webui_proxy → webui`, so the reverse-proxy is the last thing
  to start, which is the only correct order).

---


## 1. What This Is

**Ikaros** -- a Windows desktop AI companion (桌宠) with a Live2D pet, local+cloud
memory, and voice. **No-bridge architecture** since 2026-07-07: there is no
`:7860` bridge and no PyQt6 pet. The pet is a **Tauri v2 + Vue 3 + Live2D**
app; memory is a local **V4** SQLite+FTS5+Chroma system; the LLM path is cloud
(DeepSeek V4 / MiniMax) with a local `:8080` local LLM fallback.

**One-click UX:** `bin\ikaros-start.bat` -> Tauri pet in system tray + Hermes
Dashboard at `http://localhost:9119/`. Stop with `bin\ikaros-sleep.bat`.

---

## 2. Architecture (no-bridge)

| Port  | Process                                | Role                                                       |
|-------|----------------------------------------|------------------------------------------------------------|
| :7870 | **voice-ws** (`bin/ikaros-voice-ws.py`)| Tauri pet speech link (ws -> cloud_chat + cogno_5d + edge_tts) |
| :8587 | **nomic-embed-text** (llama-server)    | V4 embeddings / semantic search                            |
| :8080 | **local LLM** (llama-server)            | V4 memory extraction + reflection; cloud LLM fallback      |
| :9119 | **Hermes Dashboard** (`hermes.exe dashboard`) | Web UI                                              |
| --    | `:7860` bridge / `:8648` hermes-web-ui | **REMOVED 2026-07-07** (no-bridge refactor)               |

**Voice / LLM data flow:**
```
Tauri Pet (Ikaros-Live2D) --ws :7870--> ikaros-voice-ws.py
        |  (tray menu . bubbles . Live2D)            |
        |                                            v
        |                                      cloud_chat.py
        |                                        |- cloud: DeepSeek V4 / MiniMax
        |                                        |- local :8080 local LLM (fallback)
        |                                                 |
        |                                          cogno_5d.py (5D anchor)
        v                                                 |
   edge_tts (TTS back to pet bubble) <--------------------+

Memory (Ikaros-memory V5): SQLite+FTS5 + Chroma  ->  data/v5/v5.db
   ikaros-memory-watchdog.py manages :8587 + :8080, runs V4 reflection
   (consolidate / dedup / promote / distill / reflect / cleanup).
```

**Components:**
- **Pet** -- `Ikaros-Live2D` (Tauri v2 + Vue 3 + Live2D). Click-through,
  system-tray context menu (`src-tauri/src/tray.rs`). Launched by
  `bin/ikaros-live2d.bat` (release exe). 2 windows: `main` (transparent,
  decorations off) + `monitor` (hidden, toggled from tray).
- **Frontend** -- Hermes Desktop (Electron, standalone) + Hermes Dashboard
  (`:9119`, `hermes.exe dashboard --port 9119`).
- **Memory** -- `Ikaros-memory` V4, watchdog-managed (see above).
- **Environment** -- `Ikaros-environment/init.bat` sets `IKAROS_ROOT`,
  `IKAROS_BIN`, `IKAROS_PYTHON`, `IKAROS_MEMORY_DATA`, `IKAROS_LOGS`.

> WARNING: Historical note -- the original design doc below (the `E:\Hermes Agent\`
> `modules/bridge`, `:7860` FastAPI, `:8648` Web UI, `hermes-all.bat`, PyQt6 pet)
> was REMOVED in the 2026-07-07 no-bridge refactor. Sections 1-3 above describe
> the current architecture; the changelog further down remains as project history.

---

## 3. Project Layout (current -- `E:\Ikaros`)

```
E:\Ikaros\
├── AGENTS.md                       # THIS FILE (current architecture in S1-3)
├── README.md                       # user-facing docs (current architecture)
├── bin\
│   ├── ikaros-start.bat            # * MAIN no-bridge launcher (5-step)
│   ├── ikaros-sleep.bat            # stop all Ikaros processes
│   ├── ikaros-live2d.bat           # launch Tauri pet (release exe)
│   ├── ikaros-voice-ws.py          # :7870 voice WS (pet speech link)
│   ├── cloud_chat.py               # LLM router (cloud + :8080 fallback) + cogno_5d
│   ├── ikaros-memory-watchdog.py   # manages :8587 + :8080 + V4 reflection
│   ├── ikaros-mem.bat / ikaros-repl.py
│   └── Hermes-dashboard.bat        # launch :9119 dashboard
├── Ikaros-Live2D\                  # * Pet: Tauri v2 + Vue 3 + Live2D
│   ├── src\App.vue                 # pet UI, tray-event handler, voice ws client
│   ├── src-tauri\src\tray.rs       # system tray menu (Rust)
│   ├── src-tauri\tauri.conf.json   # windows: main (transparent) + monitor
│   ├── public\live2d\              # Live2D model assets
│   └── dist\                       # built frontend (frontendDist)
├── Ikaros-memory\                  # * V4 memory system
│   ├── v5\                         # store.py / search.py / reflect/ (V5)
│   ├── cogno_5d.py                 # 5D cognition anchoring
│   └── data\v5\v5.db               # SQLite+FTS5 + Chroma vector store
├── Ikaros-environment\             # env bootstrap (init.bat -> IKAROS_* vars)
├── data\hermes-agent\              # Hermes Desktop (Electron) + skills + sessions
├── exProject\                      # sibling OSS clones (Live2DPet, MewCo-AI) - ref only
└── tools\ikaros-monitor\           # standalone monitor tool
```

> **Removed 2026-07-07:** PyQt6 pet (`bin/ikaros-desktop-pet/`) and the
> `:7860` Rust/PyQt6 bridge. The pet is now `Ikaros-Live2D` (Tauri). V3 memory
> (`Ikaros-memory/ikaros-memory-v3.py` etc.) was also removed; memory is 100% V4.



> ⚠️ **HISTORICAL BELOW** — the remainder of this file (Path Resolution, component
> deep-dives, and the dated changelog) describes the **pre-2026-07-07** Hermes
> Agent design (`E:\Hermes Agent`, `:7860` bridge, `:8648` Web UI, `hermes-all.bat`,
> PyQt6 pet). All of that was **removed** in the no-bridge refactor; the current
> architecture is documented in §1–3 above. Path resolution is now handled by
> `Ikaros-environment/init.bat` (not `hermes-root.py`).

### Path Resolution — Single Source of Truth (NEW 2026-06-10)

Hermes is **portable across USB drives** — the project root can live on `E:\`,
`F:\`, `G:\`, etc. depending on which slot the user plugged the drive into.
This is solved by a **single source of truth** that every script defers to:

```
bin/hermes-root.py       — Python resolver (the ONLY place that decides HERMES_ROOT)
bin/hermes-root.bat      — Thin bat wrapper so cmd / ps1 can call it
deps/hermes-env.bat      — Consumes the resolver's output and exports 14 HERMES_* vars
deps/hermes-env.ps1      — PowerShell equivalent
```

**Resolution priority** (first hit wins, in `bin/hermes-root.py`):

1. `HERMES_ROOT` env var (explicit override from a caller)
2. `<root>/.hermes-root` cache file (atomic write, written by `init` / `persist`)
3. `<bin>/..` (one level up from this script: assume `<root>\bin\`)
4. Scan drive letters `D:\..Z:\` for `<drive>:\Hermes Agent\portable-python\python.exe`

**Every other script** (bat, ps1, py) **MUST NOT** re-implement path resolution.
The only acceptable call sites are:

```bat
REM bat (any script in bin/ or elsewhere)
call "%~dp0..\deps\hermes-env.bat"
REM then use %HERMES_ROOT%, %HERMES_PYTHON%, %HERMES_BIN%, ...
```

```powershell
# PowerShell (any .ps1 module)
. "$PSScriptRoot\..\deps\hermes-env.ps1"
# then use $env:HERMES_ROOT, $env:HERMES_PYTHON, $env:HERMES_BIN, ...
```

```python
# Python (e.g. hermes-supervisor.py)
# Use _resolve_hermes_root(HERE) which is defined at the top of the file
# and delegates to bin/hermes-root.py resolve via subprocess.
```

**Diagnostic subcommands** of `bin/hermes-root.py`:

| Subcommand  | Purpose                                          |
|-------------|--------------------------------------------------|
| `resolve`   | Print absolute HERMES_ROOT path (single line)    |
| `verify`    | Validate all required markers exist              |
| `init`      | bat-friendly: print `KEY=VALUE` env block         |
| `scan`      | Scan all drive letters for candidates            |
| `persist`   | Write `.hermes-root` cache                       |
| `clean`     | Remove `.hermes-root` cache                      |

Example:

```
$ bin\hermes-root.bat verify
HERMES_ROOT: E:\Hermes Agent
Source: cache:.hermes-root
[OK] All required markers present
```

**CRLF maintenance** — `bin\fix-eol.py` normalizes line endings on every
`.bat` / `.cmd` / `.ps1` to CRLF. cmd.exe does not parse LF-only bat files
correctly (paths with spaces get truncated, scripts fail silently). Run
`portable-python\python.exe bin\fix-eol.py --all` after editing any bat,
or `bin\hermes-all.bat` will warn you automatically (see §7).

**Why Python and not PowerShell for the resolver** — we previously had
`modules\supervisor\orchestrator.ps1` doing the orchestration, but it
relied on `cmd /c "powershell -File ..."` bridges that broke on paths
with spaces. The Python `subprocess.Popen` with list args goes straight
to `CreateProcessW`, sidestepping all cmd /c / PowerShell -File fragility.
See §10 Debugging for the painful history.

---

## 4. Components

### Hermes Web UI (EKKOLearnAI/hermes-web-ui) — Main Interface
- **Source**: [https://github.com/EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui)
- **Tech Stack**: Vue 3 + TypeScript + Vite + Naive UI (frontend) + Koa 2 (BFF backend)
- **Port**: 8648 (configurable via `PORT` env var)
- **Features**:
  - AI Chat: Real-time streaming via Socket.IO `/chat-run`, multi-session management, Markdown rendering
  - Platform Channels: Unified config for 8 platforms (Telegram/Discord/Slack/WhatsApp/Matrix/Feishu/WeChat/WeCom)
  - Usage Analytics: Token tracking, cost estimation, 30-day trends
  - Cron Jobs: Create/edit/pause/resume scheduled tasks
  - Model Management: Auto-discover models, provider management, OAuth login
  - Multi-Profile: Isolated configs, import/export/clone
  - File Browser: Remote file management (local/Docker/SSH/Singularity)
  - Group Chat: Multi-agent rooms with @mention routing
  - Skills & Memory: Browse/search installed skills
  - Logs: Agent/server/error logs with filtering
  - Web Terminal: Integrated terminal via node-pty
- **Integration**: Configured via `bin\webui-new.bat` with portable Python and local llama-server

### hermes/server.py
- FastAPI app
- Serves the Hermes WebUI (nesquena/hermes-webui) from `hermes/static/` at `/` and `/static/`
- Key endpoints: `/health` (JSON status), `/v1/embeddings`, `/v1/models` (live-proxied from
  llama-server, falls back to `data/models/*.gguf` scan via `hermes/gguf.py`),
  `/api/chat/*`, `/api/sessions`, `/api/memory`, `/api/skills`, `/api/task` (autonomous plan-execute),
  `/api/webui/*` (stubs for the upstream WebUI's ~25 expected endpoints, all answered by
  `static/api-adapter.js` client-side)
- **Hash-based embeddings** at `/v1/embeddings` — used by WebUI RAG
  (search quality is poor but it boots without a real embedding model)
- **Autonomous task API** at `/api/task` — POST `{goal, wait}` triggers the Planner
  (sync returns full result, async returns `task_id` to poll at `GET /api/task/{id}`)

### hermes/llm.py
- `LLMRouter` with fallback chain
- Providers: `OpenAIProvider` (covers OpenAI, llama-server, MiniMax via
  OpenAI-compat), `AnthropicProvider`, `MockProvider`
- MiniMax config in `hermes.yaml` is `provider: openai` with MiniMax base URL

### Hermes WebUI (nesquena/hermes-webui)
- Three-panel dark UI: left session list / center chat / right workspace
- Served at `/` by FastAPI from `hermes/static/` (no Node.js, no build step)
- 16 theme skins, light/dark, streaming-markdown, KaTeX math, Prism syntax highlighting
- **api-adapter.js** (in `hermes/static/`) wraps `window.fetch` + `EventSource` to
  translate the upstream WebUI's expected endpoints onto our `/api/chat/*` + `/v1/*`
  backends. Missing endpoints (workspaces, kanban, crons, etc.) return sane empty
  defaults so the UI continues to boot. See `static/api-adapter.js` for the full route
  table (~25 mapped + ~30 no-op).
- Single-model dropdown reflects live `/v1/models`: proxies llama-server when up,
  scans `data/models/*.gguf` when down

### llama-server (b9503+)
- `--alias qwen2.5-3b-instruct` makes the model id clean (default
  returns filename like `Qwen2.5-3B-Instruct-Q4_K_M.gguf`)
- `--n-gpu-layers N` controls GPU offload: 0=CPU, 99=full GPU, N=hybrid
- Per-model NGL/ctx-size come from `data\models\router-preset.ini`
  (see `bin\start-llm-router.ps1` for the launcher)
- b9538+ supports router mode (`--models-dir` + `--models-preset` +
  `--models-max`): single process hosts all GGUFs in
  `data\models\`, switches on `model` field, LRU evicts.

### hermes/sessions.py (NEW 2026-06-07)
- `SessionStore` — disk-backed chat session store. One JSON file per session
  at `hermes/data/sessions/<session_id>.json`. Atomic writes (tempfile + `os.replace`).
- API: `list_sessions()`, `get_session(sid)`, `upsert_session(sid, data)`,
  `append_message(sid, msg)`, `delete_session(sid)`, `rename_session(sid, title)`.
- Replaces the previous in-memory `agent._chat_sessions` cache.
- Used by `/api/chat/sessions`, `/api/chat/sessions/{id}` (GET, DELETE, PATCH),
  and `/api/chat/start` (persists user + assistant messages on each chunk/finish).

### hermes/workspace.py (NEW 2026-06-07)
- `WorkspaceManager` — whitelisted file browser. Trust boundary is `HERMES_ROOT`.
  Whitelisted subdirs: `data/{knowledge,memory,models,skills,logs}`, `docs`, `tests`,
  plus root files `README.md` / `AGENTS.md`. Anything else returns 403.
- Path-traversal defense: `Path.resolve()` + `Path.is_relative_to()` + Windows
  `normcase` (case-folding FS bypass).
- API: `list_workspaces()`, `add_workspace(path)`, `remove_workspace(path)`,
  `list_dir(rel)`, `read_file(rel, max_bytes=200k)`, `media_path(rel)` (binary).
- Persistence: `hermes/data/workspaces.json` (atomic + `asyncio.Lock`).
- Endpoints: `/api/workspaces`, `/api/workspaces/add`, `/api/workspaces/remove`,
  `/api/list`, `/api/file`, `/api/media`.

### hermes/webui_settings.py (NEW 2026-06-07)
- `WebUISettingsStore` — atomic JSON store for the WebUI's user preferences
  (theme, skin, language, display, agent, memory, session, privacy, ...).
- 32 default keys defined in `DEFAULT_SETTINGS`. POST applies a 1-level
  nested deep-merge (nested dict keys are merged, not replaced).
- API: `get_settings_store()` (singleton), `.load()`, `.update(patch)`, `.all()`.
- Persistence: `hermes/data/webui_settings.json` (atomic + `asyncio.Lock`).
- Endpoints: `GET /api/webui/settings` returns full object;
  `POST /api/webui/settings` accepts partial patch and returns `{ok, settings}`.

### hermes/kanban.py (NEW 2026-06-07)
- `KanbanStore` — board/task/event store with atomic JSON writes, asyncio Lock,
  capped events log (2000), CSS-safe color sanitizer, default board + 5
  sample tasks bootstrap on first use.
- Board model: `board_id`, `slug`, `name`, `description`, `icon`, `color`,
  `columns` (list of column ids), `created_at`, `updated_at`, `archived`.
- Task model: `task_id`, `board_id`, `title`, `body`, `status`, `assignee`,
  `tenant`, `priority`, `tags`, `due_at`, `blocked`, `blocked_reason`,
  `created_at`, `updated_at`, `archived`.
- 22 endpoints under `/api/kanban/*` (all 4 HTTP methods on boards/tasks,
  plus block/unblock, bulk, comments, worktree, aggregates, events).
- SSE/dispatch/comments/worktree are noop stubs per spec; UI falls back to
  30s polling on `/api/kanban/events`.
- Persistence: `hermes/data/kanban/{boards,tasks,events}.json`.

### hermes/cron.py (NEW 2026-06-07)
- `CronManager` — scheduled job runner using `croniter` for next-fire
  calculation. 30-second background loop scans for due jobs and dispatches
  them in background asyncio tasks.
- Action types: `shell` (subprocess), `task` (agent.run_task), `webhook` (POST).
- Job model: `id`, `name`, `cron_expr`, `action`, `enabled`, `no_agent`,
  `script`/`prompt`, `deliver`, `profile`, `toast_notifications`, `skills`.
  Serialized with UI-shape fields (`schedule_display`, `next_run_at`,
  `last_run_at`, `last_status`, `last_error`, `last_output`, `state`).
- Endpoints: `/api/crons`, `/api/crons/create`, `/api/crons/update`,
  `/api/crons/delete`, `/api/crons/run`, `/api/crons/pause`, `/api/crons/resume`,
  `/api/crons/status`, `/api/crons/history`, `/api/crons/delivery-options`.
- Persistence: `hermes/data/crons/jobs.json` (atomic + `asyncio.Lock`).
  Background loop started in `create_app` startup; stops on shutdown.

### hermes/llm.py (streaming support, NEW 2026-06-07)
- `LLMRouter.stream_chat(...)` and `collect_stream(...)` — async generator over
  provider chunks. Providers: `OpenAIProvider.stream()` (covers OpenAI,
  llama-server, MiniMax via OpenAI-compat), `MockProvider.stream()`.
- Used by `/api/chat/start` → `asyncio.Queue` → `/api/chat/stream/{id}` SSE.

### bin/hermes-model-run.{bat,ps1} (NEW 2026-06-07)
- **Purpose**: dedicated real-time viewer for the llama-server backend
  (model load progress, offload decisions, HTTP request lines, prompt-eval
  / generation timing, errors). Window title: "Hermes Model Running".
- Tails `hermes/data/logs/llm-server.log` + `llm-server.err` (written by
  `start-llm.ps1` via `RedirectStandardOutput` / `RedirectStandardError`).
- Smart color highlighting: magenta for model load, green for "HTTP server
  listening", yellow for eval time / tokens-per-second, red for errors,
  cyan for HTTP request lines, dark-yellow for warnings.
- 400ms polling loop, file-locked-safe (skip round on lock error).
- Boot banner + initial 5-line tail dump of each file.
- `hermes-all.bat` step 7 launches it; `hermes-stop.bat` step 5 kills its
  powershell process and step 6 closes the cmd window by title match.
- **Prerequisite**: `start-llm.ps1` no longer passes `--log-disable` (was
  silencing everything). With it removed, llama-server streams its internal
  log to stdout, which gets redirected to `llm-server.log` for the viewer
  to tail.

---

## 5. Key Decisions & Why

| Decision                                 | Why                                                         |
|------------------------------------------|-------------------------------------------------------------|
| Hermes WebUI (nesquena) as main UI       | Mature three-panel dark UI, no Node.js build, 16 theme skins, streaming markdown |
| `--alias qwen2.5-3b-instruct`            | Avoid filename-based model id mismatch between GGUF and WebUI |
| Hash-based embeddings (RAG shim)         | Avoid downloading 100MB+ embedding model just to boot RAG   |
| One launcher `hermes-all.bat`            | User experience: one double-click = everything             |
| Smart NGL (auto offload calculation)      | Support loading models larger than VRAM (e.g. 22GB on 8GB) |
| Bundle all llama.cpp variants             | Portable — works on any GPU (NVIDIA/AMD/Intel)             |
| Skip CPU when VRAM full of weights       | Hybrid offload with <5 layers = full CPU is faster         |
| CRLF line endings for all .bat files      | cmd.exe does NOT parse LF-only files (bug: truncates paths)  |
| client-side api-adapter.js                | Translate upstream WebUI's endpoints onto ours — no need to fork upstream Python BFF |
| Selective upstream sync via `bin/hermes-upstream-sync.py` (2026-06-17) | `hermes-agent/` is a Phase-11-locked read-only snapshot; we never `git clone` to overwrite it. Clone to `upstream/` (gitignored), `diff` to see what changed since our pin, `pick` one file at a time after review. Avoids pulling breaking changes from upstream we don't actually need. |

---

## 6. Multi-Model Loading

llama-server is **single-model per process**. Three options:

1. **Switch model** — kill llama-server, restart with different `--model`:
   ```bat
   set MODEL=%HERMES_ROOT%\data\models\Qwen2.5-7B-Instruct-Q4_K_M.gguf
   bin\hermes-all.bat
   ```

2. **Multiple llama-server instances** on different ports (8080, 8081, 8082),
   switch the WebUI's model picker to point at the desired one. Resource-hungry
   but lets you hot-swap.

3. **Ollama-compatible import** — run `python hermes/scripts/import_ollama_blobs.py`
   to convert Ollama `sha256-XXXXX` blobs to `.gguf` files in `data\models\`.

For the 22.8GB `f5ee307a2982.gguf` (qwen3) on 8GB VRAM, smart NGL
calculates ~16 layers on GPU + rest on CPU. Works, but slow.

---

## 7. Common Gotchas (READ THIS BEFORE EDITING!)

### Windows / cmd.exe
- **NEVER hardcode a drive letter** (e.g. `E:\Hermes Agent\...`) in any
  script. Hermes is portable across USB drives — the slot the user plugs
  the drive into determines the letter. Always go through the resolver:
  - bat: `call "%~dp0..\deps\hermes-env.bat"` then use `%HERMES_ROOT%`
  - ps1: `. "$PSScriptRoot\..\deps\hermes-env.ps1"` then use `$env:HERMES_ROOT`
  - py: `from bin.hermes_root import resolve` or use `_resolve_hermes_root(HERE)`
  See §3 (Path Resolution) for the full mechanism. If you see `E:\` in any
  new file under `bin\` or `deps\`, reject the change.
- **CRLF for .bat files!** LF-only → cmd can't parse → paths with spaces
  get truncated, scripts fail silently. **Don't hand-roll a PowerShell
  converter** — use the project tool:
  ```bat
  portable-python\python.exe bin\fix-eol.py --all
  ```
  After every bat edit, verify: `CR=NN, LF=NN` (must be equal). The same
  tool also fixes `.ps1` (which we keep LF but the tool normalizes anyway —
  harmless). `bin\hermes-all.bat` calls `fix-eol.py --check` at startup and
  warns you if any bat is in a bad state.
- **Pre-commit hook blocks LF-only bat commits.** Run once after cloning:
  ```bat
  bin\install-git-hooks.bat
  ```
  This sets `core.hooksPath=.githooks` (the versioned hooks directory at the
  repo root, NOT the per-clone `.git\hooks\`). From then on every
  `git commit` runs `.githooks\pre-commit`, which calls
  `portable-python\python.exe bin\fix-eol.py --all --check` and aborts the
  commit if any of the 17 Hermes-owned scripts (bin/*.bat/*.cmd/*.ps1 +
  deps/hermes-env.{bat,ps1}) have wrong line endings. To skip in an
  emergency: `git commit --no-verify`. To uninstall:
  `bin\install-git-hooks.bat uninstall`. The hook gracefully no-ops on
  fresh clones where `portable-python/python.exe` is missing yet.
  ```powershell
  # Legacy hand-rolled conversion (only if fix-eol.py is broken):
  $c = Get-Content file.bat -Raw
  [System.IO.File]::WriteAllText(file.bat, $c -replace "`r`n","`n" -replace "`n","`r`n", [System.Text.UTF8Encoding]::new($false))
  ```

- **msys/git-bash paths (`/e/Ikaros`, `/tmp`) passed to NATIVE Windows programs get treated as RELATIVE → spill to `E:\e\...` / `E:\tmp`.** This is the #1 source of phantom root dirs. A native exe (robocopy.exe, the embedded portable-python, node, the Rust launcher) does NOT understand msys path conversion; if its CWD is `E:\`, a literal `/e/Ikaros/...` argument resolves to `E:\e\Ikaros\...`, and `/tmp/foo` resolves to `E:\tmp\foo`. We hit this on 2026-07-15: the portable-python migration (robocopy / `shutil`) was fed `/e/Ikaros/portable-python` + `/e/Ikaros/data` from a git-bash context → created a frozen 279 MB shadow `E:\e\Ikaros` (superseded by live `E:\Ikaros`, safe to delete) and an `E:\tmp` (IDE tasks + vite `studio-*.html`). **IRON RULE:** any script that shells out to a native Windows tool MUST use absolute Windows paths — `E:/Ikaros/...` (forward slash is fine for native tools) or `E:\Ikaros\...` — OR derive the root from the script's own location (`Path(__file__)` / `$PSScriptRoot` / `%~dp0`). NEVER pass `/e/Ikaros/...` or `/tmp` to a non-msys program. The launchers already do this right: `init.bat`/`ikaros-env.bat` resolve `IKAROS_ROOT` via `%~dp0`, and `main.rs` `order_studio_impl` pins the `npm` CWD to `root.join("hermes-studio")`. If you ever see `E:\e\` or `E:\tmp` reappear, grep the last script that ran for `/e/` or `/tmp` literals.

- **`cmd /c "path with space"`** — truncates at the space. Workarounds:
  - `cmd /c "bat.bat" arg` (bat is relative, run from its dir)
  - Wrap the whole command in outer quotes
  - Or invoke from a wrapper bat

- **`for /f "tokens=*" %%V in ('cmd with --flag=value,flag2')`** — the comma
  breaks the parser. Use `usebackq` + backticks:
  ```bat
  for /f "usebackq tokens=*" %%V in (`cmd --flag=value`) do ...
  ```
  Or wrap in PowerShell to avoid cmd parsing entirely.

- **`set /a` is 32-bit signed integer.** For files > 2GB, use PowerShell:
  ```bat
  for /f "tokens=*" %%S in (`powershell -NoProfile -Command "$f=(Get-Item -LiteralPath '%FILE%').Length; [int][math]::Floor($f/1MB)"`) do set "MB=%%S"
  ```

### Hermes WebUI (nesquena upstream)
- **`__WEBUI_VERSION__`, `__MAX_UPLOAD_BYTES__`, `__CSRF_TOKEN_JSON__` placeholders
  in `index.html` are filled in by server.py** at request time. If you copy the
  static dir to a different web server, do the substitution yourself or the
  bootstrap script will 404 on the versioned asset URLs.
- **Adapter must load BEFORE `pwa-startup.js`** — `<script src="api-adapter.js">`
  in `<head>` is non-negotiable; the wrapper needs to be in place before any other
  script calls `fetch`.
- **`/v1/models` resolution order** — first try `http://127.0.0.1:8080/v1/models`
  (live proxy), then `data/models/*.gguf` scan via `hermes.gguf.list_gguf_models()`,
  then empty. The WebUI uses this list as the model dropdown — if llama-server is
  down, you see filename stems like `Qwen2.5-7B-Instruct-Q4_K_M`. To use one, your
  `hermes.yaml` `llm.router.providers.local.alias` must match.
- **Hermes Agent upstream is `nousresearch/hermes-agent`** — the WebUI was built
  for that. Our adapter translates the ~25 endpoints it actually hits on boot/chat;
  everything else (workspaces, kanban, crons, voice, OAuth, etc.) is no-op'd. Don't
  expect those panels to do anything useful.

### llama-server
- **Model id from `/v1/models` defaults to the filename** (ugly).
  Use `--alias clean-name` to override.

- **Single model per process.** To switch, restart with different `--model`.

### hermes (Python)
- **Config env expansion**: `os.path.expandvars` doesn't support bash
  `${VAR:-default}` syntax. `hermes/config.py` has custom regex
  `_ENV_VAR_RE` for this — don't replace with plain `expandvars`.

- **`.env` loading**: config.py searches in cwd, parent, then `hermes/`
  package parent dir (absolute path). Works regardless of cwd.

---

## 8. Modification Log (chronological)

| When        | Change                                                              |
|-------------|---------------------------------------------------------------------|
| Day 1       | Built hermes package: config, llm router, memory, KB, skills, server |
| Day 1       | Embedded portable-python 3.12.10 + llama.cpp CPU b9503 + 3 GGUF     |
| Day 1       | Built React admin SPA → `hermes/web_dist` (now deprecated)           |
| Day 1       | Wrote `bin\hermes-all.bat` v1 (vulnerable to path issues)            |
| Day 2       | Fixed hermes-all.bat CRLF issue (was LF-only, paths truncated)        |
| Day 2       | Added `--alias qwen2.5-3b-instruct` to llama-server                  |
| Day 2       | Integrated Open WebUI 0.9.6: install + bootstrap + config            |
| Day 2       | Added `/v1/embeddings` shim in hermes/server.py (hash vectors)        |
| Day 2       | Discovered: OW shows system Ollama models (we want only ours)        |
| Day 3       | Fixed: `ENABLE_OLLAMA_API=false` in hermes-all.bat + start-openwebui.bat |
| Day 3       | Created `hermes/scripts/bootstrap_openwebui.py` (auto signup + add model) |
| Day 3       | Created `setup-runtime.bat` (download ALL llama.cpp variants, aria2 16-thread) |
| Day 3       | Added `start-llm-smart.bat` (auto NGL based on model size + VRAM)    |
| Day 3       | Imported user's Ollama blob (qwen3 22.8GB) → `f5ee307a2982.gguf`    |
| Day 3       | Project cleanup: trashed 1-off test scripts, kept 2 functional tests |
| Day 3       | THIS FILE created                                                 |
| 2026-06-06  | **llama.cpp b9503 → b9538 upgrade** (Qwen3 MoE / Qwen3.5 MoE support) |
| 2026-06-06  | Cleaned 22.29GB `Qwen3.6.incompatible-b9503.gguf` (model+llama.cpp upgrade obsoletes) |
| 2026-06-06  | Trashed `hermes/web_dist/` (React admin SPA) — server.py uses HTML_FALLBACK now |
| 2026-06-06  | Trashed `scripts/` (6/3 legacy) — bin/ replaced all |
| 2026-06-06  | Switched default model: `Qwen2.5-7B-Instruct` → `Qwen3.5-35B-A3B-Q4_K_M` (20.5GB MoE) |
| 2026-06-06  | Verified Qwen3.5-35B-A3B works: n_params=34.66B, n_ctx_train=262144, chat "Hello! How can I assist you today?" OK |
| 2026-06-06  | Studied ComfyUI-aki-v3 for inspiration (see §13 Roadmap below)        |
| 2026-06-06  | **A**: `bin/hermes-models.py` CLI 多模型切换器 (list/switch/download/gopeed) — parses GGUF v3 header (arch, ctx_len, n_tensors) |
| 2026-06-06  | **A**: `hermes/gguf.py` module — extracted GGUF v2/v3 header parser, reused by CLI + web UI |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher` page — web UI for model switching (replaces deprecated `web_dist/` admin SPA) |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher/switch` (POST) — async subprocess runs `switch-model.bat`, returns when done |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher/download` (POST) — creates gopeed-web task via Python communication bridge |
| 2026-06-06  | **A**: Integrated `gopeed-web` (89MB single exe) into `runtime/gopeed-web.exe` as the Python communication bridge for downloads |
| 2026-06-06  | **B**: `hermes/firstrun.py` + `bin/hermes-firstrun.bat` — detects NVIDIA/AMD/Vulkan, downloads cudart via gopeed-web if missing, graceful CPU fallback |
| 2026-06-06  | **B**: `hermes-firstrun.bat` wired into `hermes-all.bat` as Step 0 (idempotent check, doesn't block startup) |
| 2026-06-06  | **C**: `hermes/doctor.py` + `bin/hermes-doctor.bat` — 8-section health report (runtime, models, GPU, services, gopeed, python, disk, env) |
| 2026-06-06  | **D**: `hermes/gopeed_client.py` — gopeed-web API client (urllib only, no deps). gopeed-web API differs from desktop gopeed (POST body wrapped in `req`, response `data` is task_id string, opts at `meta.opts`) |
| 2026-06-06  | Memory: 120s bash timeout, gopeed+file-lock download check, gopeed-web API quirks, GGUF v3 type table |
| 2026-06-10  | **Hot-swap architecture (Phase 14)** — closed WebUI ↔ llama-server disconnect |
| 2026-06-10  | **bridge/server.py v0.3.0** — added 5 endpoints: `POST /v1/models/swap`, `GET /v1/models/status`, `POST /v1/models/evict`, `POST /v1/models/warmup`, `GET /v1/models/warmup/{id}`. All paths from env vars (HERMES_BRIDGE_URL/HERMES_MODELS_DIR), no hardcoded drives. |
| 2026-06-10  | **hermes_bridge.py patch** — added `model_swap` / `model_warmup` / `model_status` actions to BOTH worker (line 2589) and broker (line 3808) `handle()`. Broker does NOT auto-forward unknown actions — must mirror. Uses stdlib `urllib.request` (no httpx dep) for portability. |
| 2026-06-10  | **E2E verified** — `model_swap` HTTP 200 `{"success":true}`; `model_warmup` 2 models in <3s with progress polling; `model_status` returns resident + available list; `/v1/chat/completions` still works after swap (HTTP 200 with valid usage). |
| 2026-06-10  | **llama-server b9538 router mode placeholder quirk** — when nothing is resident, `/props.model_alias == "llama-server"` and `/props.model_path == "none"`. Used in evict endpoint to return noop instead of triggering reload. |
| 2026-06-06  | **CRITICAL BUGFIX**: `hermes-stop.bat` v1 used `taskkill /IM llama-server.exe` literal — but the actual binary is `llama-server-cuda-12.4.exe`. Old stop left **stale llama-server processes holding VRAM** (one PID survived 20+ hours, working set -1140MB = leaked kernel handles). v2 fix: use `llama-server*` wildcard + PowerShell-based kill for clean output. |
| 2026-06-06  | Also fixed: all `bin\*.bat` files were **LF-only** (Edit tool had stripped CRs), causing cmd.exe to mis-parse multi-line `powershell -Command` blocks (visible as random "X 不是内部或外部命令" noise). Restored CRLF on all 9 bat files.
| 2026-06-07  | **Track 1: streaming-and-sessions** — `hermes/sessions.py` (SessionStore: one JSON per session, atomic write + asyncio.Lock) + `hermes/llm.py` (`stream()` on OpenAI/Mock, `stream_chat`/`collect_stream` on router) + `hermes/server.py` (`/api/chat/start`, `/api/chat/stream/{id}` SSE, `/api/chat/cancel`, `/api/chat/stream/status`, persistent `/api/chat/sessions{,/{id}}`, legacy `/api/chat/send` kept) + `hermes/static/api-adapter.js` (removed EventSource mock, chat/start is passthrough, cancel/status forward to real endpoints). SSE event shape: `{type: starting|delta|done|error|replay, content?, stream_id, session_id, model, provider, ...}`. Persistence path: `data/sessions/<session_id>.json`. Owner had to move the catch-all `@app.api_route('/api/{path:path}')` to the very last position in `create_app` because FastAPI matches routes in registration order; added a multi-line warning comment. |
| 2026-06-07  | **Track 2: workspace-browser** — `hermes/workspace.py` (WorkspaceManager: HERMES_ROOT trust boundary, case-insensitive whitelist `data/{knowledge,memory,models,skills,logs}` + `docs` + `tests` + root files `README.md`/`AGENTS.md`, path-traversal defense via `Path.resolve()` + `is_relative_to()` + Windows `normcase`, binary sniff for `read_file`, mime for media, atomic JSON persistence). Added 6 endpoints to `server.py`: `GET/POST /api/workspaces{,/add,/remove}`, `GET /api/list`, `GET /api/file`, `GET /api/media`. `api-adapter.js` updated: removed noop transforms, added `dropParams` route field, made workspaces/list/file/media passthrough. Persisted at `data/workspaces.json`. |
| 2026-06-07  | **Track 3: settings-persistence** — `hermes/webui_settings.py` (WebUISettingsStore + DEFAULT_SETTINGS with 32 keys + 1-level nested deep-merge + asyncio.Lock + atomic write). Replaced server's `GET/POST /api/webui/settings` noop handlers with real ones; the store singleton is instantiated in `create_app`. `api-adapter.js` simplified: removed the hardcoded 32-key default on `/api/settings` GET, both GET and POST are now passthrough. Persisted at `data/webui_settings.json`. |
| 2026-06-07  | **Track 4: kanban-board** — `hermes/kanban.py` (KanbanStore: Board+Task models, atomic JSON writes for boards/tasks/events, asyncio.Lock, capped events log at 2000 entries, CSS-safe color sanitizer, default board + 5 sample tasks bootstrap, board switcher pointer). 22 endpoints registered in `server.py` between `/api/webui/noop` and the workspace block (all 4 HTTP methods on `boards/{slug}` and `tasks/{id}` so spec's PUT and UI's PATCH both work). `api-adapter.js` v0.5: 14 explicit kanban passthrough routes using `url:null+passthrough:true`. SSE/dispatch/comments/worktree are intentional noop per spec (UI falls back to 30s polling). Persisted at `data/kanban/{boards,tasks,events}.json`. |
| 2026-06-07  | **Track 5: cron-scheduler** — `hermes/cron.py` (CronManager + Job dataclass + atomic JSON persistence + 30s background scan loop + shell/task/webhook runners + UI-shape serializers). Started in `create_app` startup, stopped on shutdown. 10 endpoints registered BEFORE the `/api/{path:path}` catch-all: list/create/update/delete/run/pause/resume/status/history/run+filename/delivery-options. Action types: `shell` (subprocess), `task` (agent.run_task), `webhook` (POST). `api-adapter.js`: replaced 2 crons noop entries with passthrough; fixed fetch wrapper to use original URL when `route.url` is null. `requirements.txt`: added `croniter==6.0.0`. Persisted at `data/crons/jobs.json`. |
| 2026-06-07  | **Track 6: final-integration** — All 5 tracks verified end-to-end on a live mock-mode server (port 7860). 13 GET/POST endpoints all 200; SSE stream produced 53+ chunks; settings (`theme=sepia`, `display.streaming=false`) + session `e2e-test-session` (5 messages) + kanban default board (6 tasks) all survived `Stop-Process` + restart. Kanban CRUD roundtrip (POST→GET→PATCH→DELETE) verified. Cron CRUD roundtrip (create→list→run→delete) verified. `bin\*.bat` CRLF audit: 9/13 CRLF-OK, 4 still LF-only (`gpu-detect.bat`, `hermes.bat`, `model-manager.bat`, `verify-server.bat`) — pre-existing issue, not introduced by these tracks. AGENTS.md §3/§4/§8/§12/§13 updated; README.md mentions new WebUI features. Full e2e transcript in `deliverable-final.md`.
| 2026-06-07  | **Hermes WebUI merge (nesquena/hermes-webui)**: replaced the in-house single-file `chat_ui.py` with the upstream three-panel dark UI. Source: `D:\PZS0X\下载\hermes-webui-master\hermes-webui-master\static\` (3.5MB: 18 vanilla-JS files + 366KB CSS with 16 skins + vendored streaming-markdown + KaTeX). Copied to `hermes/static/`, new UI served at `/`; old `chat_ui.py` kept as `/chat` fallback. Adaptation: `hermes/static/api-adapter.js` (23KB) wraps `window.fetch` + `EventSource` to translate the new UI's ~25 expected endpoints onto our existing `/api/chat/*` + `/v1/*` backends; missing endpoints (workspaces, kanban, crons, etc.) return sane empty defaults so the UI continues to boot. server.py: `GET /` serves `hermes/static/index.html` with `__WEBUI_VERSION__` / `__MAX_UPLOAD_BYTES__` / `__CSRF_TOKEN_JSON__` placeholder substitution; added 11 new `/api/webui/*` stub endpoints; added `app.mount("/static", StaticFiles(...))` for the new asset tree. **Two bugs hit and fixed during integration**: (1) `/api/webui/*` registered AFTER the catch-all `/api/{path:path}` got swallowed — moved all webui routes before the catch-all; (2) duplicate `@app.get("/")` returned the legacy DASHBOARD_HTML — removed the old one. **Knowingly not implemented** (UI may show empty panels / "no data" / disabled features): streaming Markdown renders but no real token-by-token SSE (we block on /api/chat/send and emit one fake delta — works but not live), workspaces/file browser, kanban boards, cron jobs, projects, memory editor, voice, OAuth/passkeys, multi-profile, web terminal. Adding these is straightforward but out of scope for v0.1.
| 2026-06-07  | **Full cutover to Hermes WebUI**: deleted `hermes/chat_ui.py` (336 lines), `bin/hermes-web.bat`, `data/openwebui/` (residual data), and `docker/` (unused). Removed `GET /chat` endpoint, `DASHBOARD_HTML` constant, and the `DASHBOARD_HTML` fallback from `GET /` and `GET /health` — both now 503 if `hermes/static/index.html` is missing (install is broken). `bin/hermes-all.bat` v8: title says "WebUI mode", opens `http://localhost:7860/` (not `/chat`), drops the legacy `:7870` line. **LLM model dropdown fix**: rewrote `GET /v1/models` to **live-proxy** `http://127.0.0.1:8080/v1/models` when llama-server is up (so the UI sees the exact `--alias` model llama-server has loaded), then fall back to scanning `data/models/*.gguf` via `hermes/gguf.py` and exposing filename stems as model ids (matches llama-server's default alias), then empty list. Response now includes `_size_gb` / `_arch` / `_ctx_len` / `_quant` / `_filename` extras on each model entry (the adapter surfaces them in the WebUI's tooltip). Updated AGENTS.md §1, §2, §3, §4, §5, §7, §9, §10, §12 to remove all Open WebUI references and reflect the new architecture (2 processes instead of 3, WebUI at :7860, no `:7870`).
| 2026-06-07  | **Hermes Model Running window**: new persistent `bin/hermes-model-run.bat` + `.ps1` that tails `data/logs/llm-server.log` + `llm-server.err` with smart color highlighting (model load magenta, HTTP requests cyan, eval-time yellow, errors red). User asked for a way to "see what the LLM is doing" — previously `llm-server.log` was 0 bytes because `start-llm.ps1` passed `--log-disable`. Removed that flag so llama-server now streams its internal log to stdout, which gets redirected to the log file. Step 7 of `hermes-all.bat` launches the window; `hermes-stop.bat` step 5 kills the powershell and step 6 closes the cmd window by title match. Title set via `$Host.UI.RawUI.WindowTitle` with try-catch guard against host-less invocations. Initial 5-line tail dump at boot, 400ms polling loop, file-locked-safe. CRLF verified on both new files. AGENTS.md §3, §4, §8 updated.
| 2026-06-08  | **Hermes Web UI Integration (EKKOLearnAI)**: integrated [hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui) as the main web interface. Source: `data/webui-new/app/` (Vue 3 + Koa + Socket.IO). New launcher: `bin/webui-new.bat` with environment setup for portable Python (`HERMES_AGENT_BRIDGE_PYTHON`), data isolation (`HERMES_WEB_UI_HOME`, `HERMES_HOME`), and gateway disable (`HERMES_WEB_UI_DISABLE_GATEWAY_AUTOSTART=1`). Updated `hermes-all.bat` to start Web UI at :8648. **Compatibility fixes**: (1) Python bridge path injection for portable Python; (2) Data directory isolation to `data/webui-new/data/` and `data/hermes-agent/`; (3) Auto-generated `hermes-agent/config.yaml` with llama-local provider pointing to `http://127.0.0.1:8080/v1`; (4) Node.js dependency on system PATH (Web UI requires Node.js). Architecture now has 3 processes: llama-server (:8080), Hermes FastAPI (:7860), Hermes Web UI (:8648). README.md rewritten with acknowledgment to EKKOLearnAI/hermes-web-ui project. AGENTS.md §1/§2/§3/§4 updated.
| 2026-06-08  | **Skill installation for Web UI**: 9 skills installed to `data/hermes-agent/skills/` across 4 categories — 4 from upstream `optional-skills/finance/` (excel-author, pptx-author, comps-analysis, dcf-model) + 4 from community GitHub via `git clone` (drawio-skill from `Agents365-ai/`, hermes-dojo from `Yonkoo11/`, avoid-ai-writing from `conorbronsdon/`, plur-memory + plur-session-end from `plur-ai/plur`). Config change: `data/hermes-agent/config.yaml` `toolsets:` list extended with `skills` (was `[hermes-cli]` only) — required to load the upstream `skills` toolset. **Process note**: `hermes skills install <name> --force` is rate-limited by GitHub API (60 req/hr unauthenticated) so we used `git clone --depth=1` as the rate-limit-free fallback. 2 short names (`research-agent`, `multiagent`) not present in upstream source tree and were skipped pending a specific source URL from the user. See §11 for the full list. AGENTS.md §3/§8/§11 updated.
| 2026-06-08  | **Built-in skill install (round 2)**: copied 5 built-in skills from `E:\Hermes Agent\hermes-agent-source\skills\` to `data/hermes-agent/skills/` — `productivity/{powerpoint, ocr-and-documents, nano-pdf, google-workspace}` and `creative/claude-design`. Total now 14 active skills across 4 categories (finance 4, productivity 6, creative 3, autonomous-ai-agents 1). User confirmed trust = same-source GitHub repo so no security scan. AGENTS.md §3/§15 updated.
| 2026-06-08  | **Portability audit (full project)** — user asked: every file/service/dep/env that `hermes-all.bat` opens must be inside the `Hermes Agent` folder itself (plug-and-play on a fresh Windows PC, no PATH, no drive-letter literals). Audited all `bin/*.bat` (18), `bin/*.ps1` (5), root `*.bat`/`*.ps1` (4), `hermes/*.py`, `hermes-agent-source/`, `data/webui-new/app/bin/*.mjs`, `data/hermes-agent/config.yaml`, `portable-python/`, `runtime/node23/`. Fixed: `bin/verify-server.bat` (4-line rewrite to use `%~dp0..`), `bin/webui-new.bat` (portable dev hint + PowerShell fix-up of `mcp_servers.hermes-studio.env.HERMES_WEB_UI_HOME/HERMES_WEBUI_STATE_DIR` on every launch, idempotent), `hermes/scripts/install_skill.py` + `rebuild_kb.py` (`Path(__file__).resolve().parents[2]`). Deleted: root `start_llm_server.bat` (dead code, called missing `local_llm_server.py`), root `update_env.ps1` (contained a live **MiniMax API key** — would have leaked to GitHub), 47 debug-residue files in `data/logs/` (17 `_diag*.bat` + 1 `_test.ps1` + 1 `_test_arg.bat` + 25 `_diag*.txt` + 2 `removed-*.bat` + 17 underscore-prefixed session logs). Verified portable: portable-python runs in any cwd ✓; `runtime/node23/node.exe` resolves via `%HERMES_ROOT%` ✓; `hermes-agent-source/` has no hardcoded paths ✓; webui `hermes-web-ui.mjs` ✓. Items left as-is documented in §16 (env-var fallbacks, docstring examples, third-party Node build scripts, doctor diagnostic strings). New `§16 Portability Audit` written.
| 2026-06-10  | **Modular refactoring (Phase 1-5 complete)** — Three core principles: (1) No reinventing wheels, (2) Bridge don't modify upstream, (3) Keep upstream clean. **Phase 1**: Created `deps/` dependency zone with `hermes-env.bat`+`hermes-env.ps1` (centralized env vars), `manifest.json` (version tracking), and NTFS junctions: `deps/node/`→`runtime/node23`, `deps/llamacpp/bin/`→`runtime`, `deps/tools/`→`runtime`. Python intentionally NOT junctioned (would break `python312._pth` `..` resolution). **Phase 2**: Created `modules/` skeleton with 6 modules: `llm-engine/` (port 8080), `bridge/` (7860), `webui/` (8648), `env-bootstrap/` (GPU detect), `model-manager/` (downloaders), `supervisor/` (orchestrator). Each has `module.json` (self-describing: name, version, type, runtime, network, lifecycle, depends_on, env), `start.ps1`, `stop.ps1`, `health.ps1` (for services). **Phase 3**: `supervisor/orchestrator.ps1` reads all `modules/*/module.json`, topologically sorts by `depends_on`, starts services in order with health checks, stops in reverse order. Supports `--status`, `--stop`, `--dry-run`. Updated `bin/hermes-all.bat` v2 and `bin/hermes-stop.bat` v2 to call orchestrator. **Phase 4**: Merged `hermes/gpu.py`+`hermes/firstrun.py` GPU parts → `modules/env-bootstrap/gpu_detect.py`. Merged `hermes/download.py`+`hermes/gopeed_client.py` → `modules/model-manager/downloader.py`. Implemented `bridge/sitecustomize.py` two monkey-patches: PATCH 1 (Windows path raw-string preprocess, wrapping `tools.code_execution_tool.execute_code`) and PATCH 2 (Windows-cwd terminal wrapper, wrapping `tools.environments.base.BaseEnvironment.execute`). Copied to `portable-python/Lib/site-packages/sitecustomize.py` for auto-load. **Phase 5**: Deleted duplicate files: `hermes/skills.py`, `hermes/prompts.py` (upstream covers), `hermes/gpu.py`, `hermes/gopeed_client.py`, `hermes/scripts/gpu_detector.py` (merged into modules). Kept in `hermes/`: `config.py`, `__init__.py`, `__main__.py`, `workspace.py`, `memos_client.py`, `knowledge.py`, `watchdog.py`, `download.py`, `mirror.py`, `gguf.py`, `firstrun.py` (last 3 to be removed after Phase 6 verification). Updated AGENTS.md §3 project layout + §8. |
| 2026-06-08  | **Console Switch bug + Process.Start fix + NGL=0 + new health probe + setup-portable** — five related fixes: **(1)** `hermes-console.ps1` Switch-Model previously used `Start-Process cmd /c "bat" "gguf"` which silently failed (cmd's quote-pair rule + spaces in `E:\Hermes Agent\...`) — no `last-launch.json`, no llama-server PID, no logs. Switched to `Start-Process -FilePath $startBat -ArgumentList @($ModelPath)` (ShellExecuteEx detaches the child reliably). Verified end-to-end: 35B → kill → 3B switch takes 3s. **(2)** `start-llm.ps1` had two real bugs: (a) `$pid = $wmi.ProcessId` triggered `VariableNotWritable` (PID is a read-only auto-variable), so the script aborted AFTER the child had already been spawned — leaving an orphan llama-server. (b) It was WMI + cmd /c indirection that PowerShell-session-detach problems couldn't shake. Replaced the whole WMI + cmd redirect block with `Start-Process -FilePath $BinFull -ArgumentList $argList -RedirectStandardOutput/Error -WindowStyle Hidden -PassThru`. Confirmed this works: 35B stayed up across 3 separate PS session exits. **(3)** `start-llm-smart.bat` NGL calculator had two bugs: (a) `if %VRAM_FREE_MB% GTR 0` was immediate-expansion but VRAM was set inside a `for /f` block above — the read saw empty string, so NGL=0 with the misleading "no NVIDIA GPU detected" message even when 7GB VRAM was free. (b) The `if A else if B else if C` chained form raised `'else' is not recognized as an internal or external command` on some Windows builds. Fixed: all reads inside the NGL block now use `!VRAM_FREE_MB!` (delayed expansion), and the chain is rewritten as nested `if/else`. Re-ran with 3B model: NGL=99, Mode="GPU (full offload, 2007MB / 6996MB free VRAM)" ✓. **(4)** New `bin/hermes-health.ps1` — three-layer liveness probe with millisecond timestamps: `/health` (TCP-up), `/v1/models` (loader done), `/v1/completions` (model warm). Reports each layer with `HH:mm:ss.fff` and total elapsed. Wired into `hermes-console.ps1` Switch-Model step 3; hermes-all.bat uses it on a future commit. End-to-end: 3B switch + health probe reported "ALL OK in 210ms" with the model returning text from a "ping" prompt. **(5)** New `bin/setup-portable.bat` — idempotent first-boot bootstrap. Detects and downloads missing pieces: (1) `portable-python/` from python.org official embed zip (~10MB), (2) `runtime/llama-server-cuda-12.4.exe` from ggml-org's official b9503 release on GitHub (~250MB with CUDA DLLs), (3) `data/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf` from Hugging Face official mirror (~2GB). Each piece is checked separately; subcommands `python`, `runtime`, `model`, `status` for fine control. Downloads use `runtime/aria2c.exe -x16 -s16` if present, else PowerShell `Start-BitsTransfer`. Exits 1 with `MISSING` on partial failure so `hermes-all.bat` can warn-and-continue. Wired into `hermes-all.bat` as new step `[0/8]` (renumbered 2-7 to 3-8). All `.bat` files normalized to CRLF: 19/19 OK. **(6)** `hermes-model-run.ps1` evaluated per user request: **functionality is sound** — it correctly tails `data/logs/llm-server.{log,err}` with smart color highlighting (model load in magenta, offload in dark-magenta, HTTP requests in cyan, eval time in yellow, errors in red, warnings in dark-yellow), 400ms polling loop, file-locked-safe, initial 5-line tail dump. What it shows is **the llama-server backend's own log** (load progress, offload decisions, HTTP request lines, prompt-eval/eval/total times, tokens/s) — NOT the model's token-by-token "thinking" text. For that, the server would need `--verbose` (which prints the full prompt + generated text per request), but that's a separate enhancement; the script's current role is "watch the server is healthy and what it's doing" and it does that correctly.

| 2026-06-08  | **`MINIMUM_CONTEXT_LENGTH = 64_000` gate + 3B 32K override** — user hit `Error: Model Qwen2_53BInstructQ4_K_M has a context window of 32,768 tokens, which is below the minimum 64,000 required by Hermes Agent. Choose a model with at least 64K context, or set model.context_length in config.yaml to override.` Root cause: `hermes-agent-source/agent/model_metadata.py:133` hardcodes `MINIMUM_CONTEXT_LENGTH = 64_000` (for tool-calling working memory). `cli.py:5378` rejects `ctx_len < MINIMUM_CONTEXT_LENGTH`; `run_agent.py:661` resolves `target_ctx = max(config_context_length or 0, 64K)`. The **3B model's `n_ctx_train` is only 32K**, so server reports `n_ctx=32768`, agent computes `effective_context_length` from that, and the gate fails. **Fix:** add `context_length: 65536` to `data/hermes-agent/config.yaml` under `model:` — `agent/agent_init.py:1370` reads `_model_cfg.get("context_length")` directly, so this value is honoured and the gate passes. The actual server still runs 32K (n_ctx_train cap) and will warn + cap any request that exceeds 32768, but the chat-run-socket pre-flight check is what was blocking, and it now passes. Also updated `hermes-console.ps1` Switch-Model: `$ctxLen = 65536` for 3B/7B, 131072 for 35B (was 32768 for 3B — would have re-broken the override next time the user switched back to 3B). **Important side-effect:** the WebUI node process caches `_config_context_length` at startup, so changing the value in `config.yaml` (or in the WebUI's own model context window field, which is `model_context_length` and writes back via `hermes-agent-source/hermes_cli/web_server.py:399`) requires restarting the WebUI before the new value is honoured. Sequence used: edit config.yaml → `bin\webui-new.bat stop` (PID 23656 gone) → `bin\webui-new.bat start` (new PID 26460, reads the updated config) → 8648 returns 200 → 3B chat now flows. **Active WebUI session at the time of the fix:** `mq59rjli3ip8yk` (URL `http://localhost:8648/#/hermes/session/mq59rjli3ip8yk`). The desktop app's settings panel exposes this same field at `apps/desktop/src/app/settings/constants.ts:279` (`modelContextLength: 'Context Window'`, default 0 = use server-detected), reachable through the i18n key `modelContextLength` in `zh.ts` ("上下文长度") / `zh-hant.ts` ("上下文長度") / `ja.ts` ("モデルコンテキストウィンドウ") etc.
| 2026-06-08  | **Live process layout (after this session):** llama-server PID 27444 (`llama-server-cuda-12.4`, 22:01:47 start, ~2.3GB WS, 3B Qwen2.5 at 32K ctx) on :8080 — Hermes FastAPI PID 26176 ("Hermes-API", ~90MB WS) on :7860 — Hermes WebUI PID 26460 (node 22+, ~177MB WS) on :8648. All three serve 200 OK. Use `bin\hermes-stop.bat` for full shutdown, `bin\webui-new.bat stop` for just the WebUI (keeps model + API running, useful for picking up config.yaml edits without dropping the model).
| 2026-06-08  | **Pushed commit `f3d4140` to `origin/main`** at `https://github.com/ArtificialAngels/hermes-agent.git`. 15 files changed, 954 insertions, 167 deletions (the full §16 portability audit + 5 bug fixes from this session, plus the two new scripts `bin/hermes-health.ps1` and `bin/setup-portable.bat`).
| 2026-06-08  | **Hermes-agent (`hermes-agent-source/`) sandbox-leak + Windows-path SyntaxError fix** — user uploaded an xlsx in the WebUI, pointed the model at the local `parse_excel` skill, and the run failed with two stacked bugs visible in the response: **(a)** the auto-generated `script.py` lived under `C:\Users\PZS0X\.mavis\agents\mavis\workspace\.opencode\tmp\hermes_sandbox_sn9iixd4\` — i.e. in **Mavis's workspace, not in `E:\Hermes Agent\`**, violating the plug-and-play "every file under the project folder" rule. **(b)** the script body contained `parse_excel('E:\Hermes Agent\data\...')` and Python raised `SyntaxError: (unicode error) 'unicodeescape' codec can't decode bytes in position 35-36: truncated \uXXXX escape` because `\H` / `\A` / `\u` inside the string literal look like the start of unicode escapes. Root cause: `hermes-agent-source/tools/code_execution_tool.py:1135` called `tempfile.mkdtemp(prefix="hermes_sandbox_")` with no `dir=` argument, so it followed `tempfile.gettempdir()` → `TMP` env → the Mavis workspace's `.opencode/tmp`. Fix in `hermes-agent-source` (this is the user's **forked** repo, not the main project, so the diff lives in `E:\Hermes Agent\hermes-agent-source\.git\` and is on `main`, ahead of `ArtificialAngens/hermes-agent` by 1): pin the sandbox under `<HERMES_HOME>/tmp/sandbox` using `get_hermes_home()` (which on the WebUI launch resolves to `E:\Hermes Agent\data\hermes-agent` thanks to `HERMES_HOME` being set by `bin\webui-new.bat`). Add `_auto_rawstring_windows_paths(code)` pre-pass on the script before writing `script.py` — regex `(?<![rR])(['"])([A-Z]:\\[A-Za-z0-9_.\\ ()~+@#${},!:-]+)['"]` matches Windows-path-shaped string literals and the `sub` rewrites the whole match to a raw string. **Critical regex gotchas hit while writing this** (record for next time): (1) the closing delimiter must be a literal quote-class `['"]`, NOT a backref `\1` — in a raw string `r'\1'` is two characters (`\` + `1`) and Python `re` interprets that as a literal backslash + digit, not a backref; use `\\1` to get an actual backref, or skip backref entirely and use a literal class. (2) The character class MUST include both `:` and `\\` (escape backslashes in code) — `E:\foo` won't match if the class only has `A-Z` because `:`, `\`, and the path-separator backslash all need to be in the allow-list. (3) Use a `(?<![rR])` lookbehind to skip already-raw literals so `r'C:\foo'` is left untouched. Verified end-to-end with seven test fixtures: single/double-quote paths, multiple paths in one call (`multi("X:\a\b","Y:\c\d")` → `multi(r"X:\a\b",r"Y:\c\d")`), already-raw, and plain-`\n` strings. All seven pass. Pushed commit `c8d1e0ea8` to the user's `hermes-agent-source` git. **Open question for the user:** do you want to PR this back to `NousResearch/hermes-agent` upstream, or keep it as a local fork patch?
| 2026-06-08  | **Active state at end of session:** WebUI on :8648 (node PID 26460, 22:10:28 start, ~177MB WS, just restarted to pick up `data/hermes-agent/config.yaml` `context_length: 65536` override for the 64K gate), Hermes FastAPI on :7860 (PID 26176 "Hermes-API", ~90MB WS), llama-server on :8080 (PID 27444, `llama-server-cuda-12.4`, 22:01:47 start, ~2.3GB WS, running `Qwen2.5-3B-Instruct-Q4_K_M.gguf` at 32K ctx). Active chat session: `mq59rjli3ip8yk` at `http://localhost:8648/#/hermes/session/mq59rjli3ip8yk`. `hermes-agent-source/` git is on commit `c8d1e0ea8` (sandbox pin + raw-string preprocess), 1 commit ahead of `origin/main`. Main project on commit `f3d4140` (already pushed to `ArtificialAngels/hermes-agent`), no further source-tree changes pending from this session beyond these two commit-log entries.
| 2026-06-09  | **Switch-model bug — `LLAMA_MODEL` env var inherited from parent overrode `argv`** — user reported that `hermes-console.ps1` Switch-Model and `hermes-all.bat` initial launch were always loading the same model (3B or 7B) regardless of what was picked in the dropdown. Root cause: `bin\start-llm-smart.bat` had `if not "%LLAMA_MODEL%"=="" set "MODEL=%LLAMA_MODEL%"` AFTER `set "MODEL=%~1"` — so the env var set by `hermes-all.bat` L94 (`set "LLAMA_MODEL=%MODEL%"`) was inherited by the child cmd started via `start /MIN`, and unconditionally overrode any explicit argv. **Fix:** swap the precedence: argv > env > default. Also `hermes-console.ps1` [3/5] Verify step now compares `/v1/models` response to the requested alias and reports red `MISMATCH`/`FAILED` instead of green `SUCCESS` when they differ — the previous "verify" only printed the model ID and never compared, which is why this whole class of bugs was invisible. Pushed as `3618291`. AGENTS.md §3 / §7 / §8 (this entry) updated.
| 2026-06-09  | **Detach bug — `Start-Process` + parent cmd window close killed llama-server** — `bin/start-llm.ps1` used `Start-Process` (ShellExecuteEx) which left the new process attached to the parent PowerShell's console. When the user closed the parent cmd window, Windows broadcast `CTRL_CLOSE_EVENT` to every process attached to that console, and llama-server (a console app) responded by exiting. Verified by user: bat works directly when run in foreground, but disappears after closing the cmd. **Fix:** replace `Start-Process` with `System.Diagnostics.Process.Start(ProcessStartInfo)` + `UseShellExecute=$false` + `CreateNoWindow=$true` + `RedirectStandardOutput/Error=$true`. This goes through `CreateProcess` with `CREATE_NO_WINDOW`, the new process has no console of its own and is NOT attached to the parent's, so the parent's CTRL_CLOSE_EVENT never reaches it. Output drains to log files via `BeginOutputReadLine()`. Also removed the `Wait-Process` at end of script (was meant to keep ps1 alive as a process-group guard, but on Win11 it just made the chain die harder when parent cmd closes). Pushed as `8334330`.
| 2026-06-09  | **cmd /c `""<path>""` double-double-quote — silent-fail pattern** — `bin\hermes-all.bat` L95 used `start "Hermes-LLM" /MIN cmd /c ""%HERMES_ROOT%\bin\start-llm-smart.bat""`. The `""path""` is the standard cmd /c escape for paths with spaces, but it has a known silent-fail pattern: cmd /c strips the outer quotes and the leading quote of the inner string, then looks up the bat as a literal quoted executable name (which doesn't exist). cmd /c exits with no error, the parent `start` returns successfully with no child cmd, and the user sees nothing. **Fix:** use `cd /d "%HERMES_ROOT%"` first, then `start "Hermes-LLM" /MIN cmd /c "bin\start-llm-smart.bat"` with a relative path that has no quotes around it. Pushed as `04f626e`.
| 2026-06-09  | **Refactor: llama-server router mode (b9538+) — abandon kill+restart for multi-model** — user gave up on the kill+restart model-switch flow after multiple rounds of fixes and pointed at llama.cpp's native **router mode** (`--models-dir` + `--models-preset` + `--models-max`). Confirmed via `llama-server.exe --help` that b9538 supports all of it. **New architecture**: SINGLE llama-server process started with `--models-dir data\models`; switches models on demand when an API request arrives with `model="<filename>"`. With `--models-max 1` and LRU eviction, only the most-recently-used model is resident in VRAM at a time — fits 3B/7B/35B-MoE on 8GB GPU (35B uses 16 GPU layers + CPU offload via preset). **New files**: `bin\start-llm-router.ps1` (single launcher, .NET Process.Start for proper detach, --models-max computed from free VRAM), `data\models\router-preset.ini` (per-model NGL/ctx/temp). **Updated**: `bin\hermes-all.bat` step 2 calls the new ps1; `bin\hermes-console.ps1` Switch-Model no longer kill+restarts — it POSTs `/v1/models/load` to preload, updates config.yaml, sends a tiny warmup; `data\hermes-agent/config.yaml` default model id is now the GGUF filename (`Qwen2.5-3B-Instruct-Q4_K_M.gguf`) to match what router exposes; `hermes/scripts/model_manager.py` rewritten to call `/v1/models/load` instead of stop+start. **Deleted**: `bin\start-llm-smart.bat`, `bin\start-llm.ps1`, `bin/switch-model.bat`, `tests/verify_smart_ngl.py`, `test_model_switch.py` — all obsolete with router mode. **.gitignore**: added `!data/models/*` so the preset ini can be tracked. Pushed as `ce99e4d`. README.md §"启动方式" + "目录结构" + new "Router 模式" section updated.
| 2026-06-09  | **Active state at end of session:** All 5 commits from this session pushed to `origin/main`. Last commit is `ce99e4d` (router mode refactor). `bin\start-llm-router.ps1` is the single source of truth for LLM launch; `data\models\router-preset.ini` is the per-model config. WebUI dropdown → llama-server router → LRU eviction. Zero restart cycles.
| 2026-06-10 | **stdin inherit bug fix** — `bin/start-llm-router.ps1`, `bin/start-bridge-server.ps1`, `bin/start-webui.ps1` all used `.NET [System.Diagnostics.Process]::Start($psi)` with `RedirectStandardInput=$false` which is a no-op (means INHERIT, not “don't redirect”). With `UseShellExecute=$false + CreateNoWindow=$true`, the child inherited the parent's stdin handle (a pipe from cmd/bat), and llama-server detected non-console stdin and exited immediately with "Input redirection is not supported". **Fix:** wrap each binary in `cmd /c "<bin> <args>" < NUL` — cmd.exe opens NUL device for stdin, the real child inherits cmd's stdin (NUL = valid device, not a pipe). Also fixed PID recovery: since the direct child is now cmd.exe, the real server PID is obtained from `netstat -aon` matching the listening port. **Also:** cleaned `bin/setup-portable.bat` — removed hardcoded 3B model download (`DEFAULT_MODEL_URL` + `DEFAULT_MODEL_PATH`), replaced with a simple `*.gguf` existence check and a message to use `hermes-models.py` or WebUI model manager. Verified: all three services (8080/7860/8648) return 200 OK. |

| 2026-06-09  | **Full upstream cutover — clean hermes-agent v0.16.0 + hermes-web-ui v0.6.12, hermes/*.py dedup, bridge skeleton** — user copied fresh clean copies of both upstream repos into project root (`hermes-agent/` 100.8MB / 2082 .py + 514 .ts v0.16.0; `hermes-web-ui/` 59.6MB / 515 .ts + 126 .vue v0.6.12) and deleted the old `hermes-agent-source/` fork. Decisions confirmed: **1.A** delete `data/webui-new/app/` (EKKOLearnAI 0.6.11 fork); **2.A** try running clean v0.16.0 directly; **3.A** build deps/ + clean 16 duplicate .py + bridge module skeleton. **What was done:** (1) `mavis-trash data/webui-new/app/` ✅ — the 0.6.11 fork with 4 local mods (loadModel/unloadModel controller + .gguf filter + 2 API helpers) is gone; (2) Verified `hermes-agent v0.16.0` imports end-to-end (`hermes_cli.main`, `AIAgent`, `agent`, `cron.jobs`, `hermes_state`, `gateway`, `tools` all importable; `HERMES_HOME=E:\Hermes Agent\data\hermes-agent` honored by upstream `get_hermes_home()`); (3) **Removed broken editable finder** (`__editable___hermes_agent_0_16_0_finder.py` + `.pth`) — both still pointed at deleted `hermes-agent-source/` paths; (4) **Added `../hermes-agent` to `portable-python/python312._pth`** so `hermes_cli`/`run_agent`/`agent`/`tools`/`cron`/`gateway` all resolve from clean source; (5) **Backed up 13 duplicate .py to `data/_backup/hermes_dups_2026-06-09/`** (agent, cron, doctor, embeddings, kanban, llm, memory, planner, sessions, webui_settings, mock + scripts/{install_skill, model_manager}) — upstream v0.16.0 has equivalent or richer implementations; (6) **`mavis-trash hermes/server.py`** (97KB) — broken imports of the 11 deleted modules; replaced with `bridge/server.py` skeleton (FastAPI app, `/health` returns 200 with version + endpoint manifest, 8 endpoints planned: `/v1/models`, `/v1/models/load`, `/v1/chat/completions`, `/api/chat/sessions`, `/api/workspaces`, `/api/kanban`, `/api/crons`, `/api/webui/settings`); (7) **Rewrote `hermes/__init__.py`** as thin shim (re-export doc only, no eager imports — upstream is authoritative); (8) **Rewrote `hermes/__main__.py`** as thin CLI delegate (`from hermes_cli.main import main as upstream_main; sys.exit(upstream_main())`); (9) **Fixed `hermes/knowledge.py`** — it imported deleted `hermes.memory.cosine_similarity` and `hermes.memory.Embedder`; inlined a minimal `cosine_similarity` + `Embedder` base class + `HashEmbedder` fallback (deterministic 384-dim hash-based pseudo-embedder for offline use). All 13 truly-independent hermes/*.py modules now import cleanly (`config`, `skills`, `gguf`, `gpu`, `workspace`, `watchdog`, `knowledge`, `mirror`, `prompts`, `download`, `firstrun`, `gopeed_client`, `memos_client`). (10) **Built `bridge/` skeleton**: `__init__.py` (version `0.1.0-skeleton`), `README.md` (architecture diagram + what's-in/where/why), `server.py` (FastAPI app with TODO imports for upstream `AIAgent`/`SessionDB`/`JobStore`/`KanbanDB`/our `WorkspaceManager`/`list_gguf_models`), `sitecustomize.py` (monkey-patch template for `c8d1e0ea8` + `d59d06c2d` — both documented with original-commit context, ready for `portable-python/Lib/site-packages/sitecustomize.py` install); (11) **Built `deps/README.md`** documenting the layout (`hermes-agent/` and `hermes-web-ui/` at root are upstream deps; not moved to `deps/` because PYTHONPATH and `_pth` already point at root, and moving would force every ref to update). **Smoke tests pass**: `python -m hermes --help` delegates to upstream and shows 50+ subcommands (`chat, model, fallback, gateway, proxy, setup, kanban, cron, doctor, security, skills, plugins, memory, mcp, sessions, claw, version, update, acp, profile, dashboard, desktop, logs, ...`); `from bridge.server import app` → FastAPI title="Hermes Bridge" v0.1.0-skeleton; `TestClient(app).get('/health')` → 200 with `{"status":"ok","version":"0.1.0-skeleton","upstream":"hermes-agent-0.16.0","endpoints_implemented":["/health"],...}`. **KNOWN BROKEN — launcher chain needs next-session fix**: (a) `bin/hermes-all.bat` L122 calls `python -m hermes serve --port 7860` — **upstream's CLI has NO `serve` subcommand** (closest is `dashboard` which starts upstream's own FastAPI at a different port); (b) `bin/webui-new.bat` L9 references deleted `data\webui-new\app`, L63 `cd hermes-agent\data\webui-new\app` (wrong), L80 `set "HERMES_AGENT_ROOT=%HERMES_ROOT%\hermes-agent-source"` (deleted). The launcher needs to either (i) call `python -m bridge.server` directly (with our FastAPI as :7860), or (ii) call upstream's `hermes dashboard` and let it own :7860. Decision deferred to user. **Also known**: `data/webui-new/` (parent dir) still contains upstream hermes-agent's old state files (`auth.json`, `config.yaml`, `kanban.db`, `state.db`, `crons/`, `kanban/`, `memory/`, `sessions/`, `skills/`, `logs/`, etc.) — this was the OLD `HERMES_HOME` before `bin/webui-new.bat` was changed to point at `data/hermes-agent/`. NOT deleted (real data, may contain valuable sessions) — user decides whether to back up + clean. **Decision not yet made**: PR `c8d1e0ea8` (sandbox pin + raw-string) and `d59d06c2d` (Windows-cwd terminal) back to upstream NousResearch, or keep as monkey-patch in `bridge/sitecustomize.py` forever.
| 2026-06-10  | **★ Path-management reform — single source of truth for HERMES_ROOT (USB-portable)** — user raised the "project is plug-and-play, drive letter changes" concern. The old setup had each .bat / .ps1 re-deriving `HERMES_ROOT` independently (`set "HERMES_ROOT=%~dp0.."`), and one hardcoded `HERMES_DATA_DIR=E:/Hermes Agent/hermes/data` in `.env`. Replaced with: **(1)** `bin/hermes-root.py` — Python resolver with 4-tier priority (env var → `.hermes-root` cache → script-location inference → drive-letter scan across D:..Z: for `\Hermes Agent\portable-python\python.exe`); 6 subcommands (`resolve`, `verify`, `init`, `scan`, `persist`, `clean`); `init` outputs a bat-parseable `KEY=VALUE` env block. **(2)** `bin/hermes-root.bat` — thin bat launcher (ASCII-only, CRLF). **(3)** `deps\hermes-env.bat` / `.ps1` — completely rewritten to consume `init`'s output (down from 69 lines of hand-rolled env to 36 lines of consumption + cuda/PATH tweaks). **(4)** Refactored 8 bat files (`hermes-all`, `hermes-stop`, `hermes-supervisor`, `hermes-firstrun`, `hermes-model-run`, `hermes-console`, `gpu-detect`, `install-embeddings`) to all go through `deps\hermes-env.bat` first. Removed the old 8.3 short-path workaround (`HERMES_ROOT_S=%%~sI`) since we no longer bridge through PowerShell `-File`. **(5)** `bin/hermes-supervisor.py` — added `_resolve_hermes_root(HERE)` helper that delegates to `bin/hermes-root.py resolve` via subprocess (env-var fast-path, then subprocess, then `here.parent.parent` fallback). **(6)** `bin\fix-eol.py` — permanent CRLF maintenance tool (replaces ad-hoc PowerShell conversions in AGENTS.md §7); accepts file list or `--all`; `--check` mode for CI/hooks. **(7)** `.env` — removed the hardcoded `HERMES_DATA_DIR=E:/Hermes Agent/hermes/data`; `hermes/config.py` already uses `load_dotenv(override=False)` so it honors the process env (set by `deps\hermes-env.bat` from `HERMES_ROOT`) over .env. **(8)** Deleted the entire `modules\supervisor\` directory (`module.json`, `orchestrator.ps1`, `start.ps1`, `stop.ps1`) — superseded by Python supervisor. **E2E verified** — corrupted `.hermes-root` to `Z:\NonExistent\Fake\Path`, `hermes-root.py verify` correctly reported `Source: inferred:script-location` (downgrade), `init` auto-repaired the cache, full env block produced 14 vars with `HERMES_STATUS=ok`. AGENTS.md §2/§3/§4/§7/§8 (this entry) updated. |
| 2026-06-10  | **★ Pre-commit hook + versioned git hooks** — make the CRLF check permanent at the git level so LF-only .bat / .ps1 files can never enter the repo. **(1)** `.githooks/pre-commit` — bash script (LF-only, 1376 bytes) that calls `portable-python\python.exe bin\fix-eol.py --all --check` and exits 1 on any failure. Skips gracefully if `portable-python` is missing (fresh clone). **(2)** `bin\install-git-hooks.bat` — one-shot installer that runs `git config core.hooksPath .githooks` (relative to repo root), with `uninstall` arg to revert. **(3)** Updated `bin\fix-eol.py` `--all` mode to scan ONLY Hermes-owned scripts (`bin/*.bat/*.cmd/*.ps1` one level + `deps/hermes-env.{bat,ps1}`) — 17 files total — instead of all 177 bat/ps1 under `deps/` (which would falsely fail on third-party node_modules with LF line endings). **(4)** Verified end-to-end: `git commit --allow-empty` triggers the hook and prints `[pre-commit] OK: all .bat / .ps1 files are CRLF.`; with a synthetic LF-only test file, `fix-eol.py --check` returns exit 1 as expected. **Phase 1 hook installed**: `core.hooksPath=.githooks`. AGENTS.md §3 / §7 / §8 updated. |

| 2026-06-10 | **Phase 7-13 收尾：硬路径消除 + CUDA 11/12/13 多版本 + 模块回归 + 文档同步** — 6 个 Phase 一气完成, 最终验证全绿. 逐项摘要:
| 2026-06-28  | **🗑️ Removed Python bridge**: deleted `bridge/server.py` (135KB) + `bridge/voice_server.py` (17KB). Rust bridge (`bridge-rs/src/main.rs`, 28 endpoints, 8 MB RSS) fully replaces them. `modules/bridge/start.ps1` and `stop.ps1` simplified to Rust-only. Backup at `data/_backup_python_bridge_removed/20260628/`. |
  - **Phase 7 (硬路径消除)**: 修复 `hermes/scripts/import_ollama_blobs.py:21` (`Path('E:/Hermes Agent')` → `Path(__file__).resolve().parents[2]`); `hermes/workspace.py:49` (`'E:\\Hermes Agent'` → `str(self.root)`); `hermes/config.py:188/217-219` (删掉 `E:/D:` fallback, 改用 `HERMES_ROOT` env); AGENTS.md 3 处文档示例 (`E:\Hermes Agent` → `%HERMES_ROOT%`). **二次扫描**: 在 modules/bin/bridge 全量 grep `'[EeDdCc]:[\\/][Hh]ermes'` 返回 **0 匹配**.
  - **Phase 8 (CUDA 11-13 多版本)**: 新建 `runtime/cuda/{11.8,12.4,13.0}/` 目录结构 + `manifest.json` (描述版本 + 包含的 DLL). 扩展 `modules/env_bootstrap/gpu_detect.py` 5 个新函数: `detect_driver_version()` / `driver_to_cuda_version()` / `find_cuda_runtime()` / `install_cuda_runtime()` / `recommend_cuda_version()` (driver→CUDA 映射表: ≥555→13.0, ≥525→12.4, ≥470→11.8, ≥450→11.0, <450→None). 修改 `modules/llm_engine/start.ps1` 调用 `recommend` 动态选 CUDA, 不存在则触发 `install`. 修改 `deps/hermes-env.bat`+`.ps1` 加 `CUDA_VERSION`/`LLAMACPP_BIN_CUDA` 变量. `setup-portable.bat` 增加多版本下载步骤.
  - **Phase 9 (修复 breakage)**: `tests/test_hermes.py` 删掉已死引用 `from hermes.gpu import detect_gpu`, 注释指向新模块; `modules/model_manager/manager.py` 创建 (统一 CLI: list/info/download/import-ollama), module.json `script` 字段保持不变; `hermes/__init__.py` 重写 docstring, 移除 9 个已删/待迁移条目 (skills/prompts/gpu/gopeed_client/gguf/mirror/download/firstrun), 保留 5 个真独立的 (config/knowledge/memos_client/watchdog/workspace).
  - **Phase 10 (删除重复)**: `bin/hermes-firstrun.bat` 改为调用 `python -m modules.env_bootstrap %*`; 删除 `hermes/download.py` (47KB, 已被 modules/model_manager/downloader.py 取代) + `hermes/firstrun.py` (32KB, 已被 modules/env_bootstrap/gpu_detect.py 取代); 删除 `bin/start-llm-router.ps1` + `start-bridge-server.ps1` + `start-webui.ps1` (3 个旧启动脚本, 已被 modules/*/start.ps1 取代). `hermes/scripts/rebuild_kb.py` 改用新下载器.
  - **Phase 11 (迁移 bridge 依赖)**: `hermes/gguf.py` → `modules/model_manager/gguf.py` (含 import path 调整); `hermes/mirror.py` → `modules/model_manager/mirror.py`; `modules/model_manager/__init__.py` 暴露 `list_gguf_models`/`parse_gguf_meta`/`DownloadManager`/`GopeedClient`/`mirror_url`; `bridge/server.py:120` 和 `bin/hermes-models.py:36` 更新 import 为 `from modules.model_manager.gguf import ...`. `modules/model_manager/manager.py` 把所有子模块通过 `__all__` 统一暴露.
  - **Phase 12 (清理废弃)**: 删除整个 `hermes/scripts/` 目录 (import_ollama_blobs.py 已迁到 model_manager); 删除 `hermes/data/webui_settings.json` + `hermes/data/workspaces.json` (旧 HERMES_HOME 残留); 删除 `runtime/node/` 旧版 Node (junction `deps/node` 已指向 `runtime/node23`). `hermes/data/logs/hermes-download.ps1` 残留日志也被清掉.
  - **Phase 13 (最终验证)**: (a) 路径扫描 0 匹配 ✓; (b) `runtime/cuda/{11.8,12.4,13.0}/manifest.json` 全部就位 ✓; (c) `python -m modules.env_bootstrap status` → GPU NVIDIA RTX 3070 8192MB 驱动 610.47 ✓; `recommend` → `12.4` ✓; `check` → `[check] OK: CUDA 12.4 ready` ✓; `python -m modules.model_manager.manager list` → 2 个 GGUF 模型列出 ✓; `modules\llm_engine\start.ps1` 实际启动 llama-server (PID 37232) ✓; `modules\supervisor\orchestrator.ps1 -Status` 显示 6 个模块 ✓; `-DryRun` 显示拓扑排序 ✓; `bin\gpu-detect.bat` 返回 JSON ✓.
  - **🚨 Phase 13.3 期间发现的关键 BUG 并已修复**: `modules/env-bootstrap/`/`model-manager/`/`llm-engine/` 三个目录用 **连字符 (hyphen)** 命名, 但 Python `import modules.env_bootstrap.gpu_detect` 需要 **下划线 (underscore)** — 连字符在 Python 包名里是 **非法的标识符**, 所有 `python -m modules.X.Y` 入口都因此 ImportError. 修复: 三个目录全部重命名为下划线版本 (`env_bootstrap`/`model_manager`/`llm_engine`), 同步更新 `module.json` 的 `name` 字段、`start.ps1`/`stop.ps1`/`health.ps1` 头注释、`bridge/module.json` 和 `webui/module.json` 的 `depends_on` 字段、AGENTS.md §3 + `hermes/__init__.py` 文档. **原理记录**: NTFS 支持连字符文件名, 但 Python `importlib` 只接受 PEP 508 标识符 (`[A-Za-z_][A-Za-z0-9_]*`), 这是隐性陷阱 (目录可见但 import 失败, 错误信息是 `ModuleNotFoundError: No module named 'modules.env-bootstrap'` 容易误判为 "包不存在").
  - **Phase 13.4 (文档同步)**: AGENTS.md §2 加 "Module architecture (Phase 1-13, completed 2026-06-10)" 说明; §3 项目布局反映重命名后的目录 (`env_bootstrap`/`model_manager`/`llm_engine`), 移除 `hermes/` 包里已删的 download.py/firstrun.py/gguf.py/mirror.py 4 个条目, runtime/ 树改为 `cuda/{11.8,12.4,13.0}/manifest.json` 多版本结构, bin/ 树移除 3 个 start-*.ps1; `deps/manifest.json` 加 3 条 CUDA 运行时记录 (11.8/12.4/13.0, 物理路径 `runtime/cuda/<ver>/`); `tests/test_hermes.py` 顶部 SyntaxWarning (行 6 `\\` 转义) 顺手修掉; `deps/hermes-env.bat`+`.ps1` 注释里 "llm-engine" → "llm_engine"; `bin/hermes-firstrun.bat` 注释 "env-bootstrap" → "env_bootstrap".
  - **Active state at end of session**: 6 个模块全部就绪 (`env_bootstrap`/`model_manager`/`llm_engine`/`bridge`/`webui`/`supervisor`), 拓扑依赖正确解析, `python -m modules.<name>.<script>` 全通, llm-engine 实测能拉起 llama-server. 整个项目现在可以 `xcopy /E /I` 到任意盘符/目录后 `bin\hermes-all.bat` 即用 (plug-and-play).

---

## 9. Setup Flow (clean install from scratch)

```bash
# 1. Download all llama.cpp variants + aria2 (one-time, ~280MB)
bin\setup-runtime.bat

# 2. Run it
bin\hermes-all.bat
# → browser opens at http://localhost:7860/  (the new Hermes WebUI)
# → WebUI is unauthenticated by default; data lives in agent memory
# → chat: type a message, pick a model from the dropdown (auto-populated
#   from llama-server at :8080 if up, else scanned from data/models/*.gguf)
```

To switch default model, edit `hermes-all.bat` line `set "MODEL=..."` (line ~13).

To install a different GGUF, drop it in `data\models\`, then either:
- Restart `bin\hermes-all.bat` after editing the MODEL line, OR
- Use Ollama's `import_ollama_blobs.py` to convert `sha256-XXXX` blobs

---

## 10. Debugging

### Log files
- `hermes\data\logs\hermes.log` — Hermes FastAPI + bootstrap.log
- Each launcher writes to its own window (visible in title bar)
- Browser DevTools Network tab shows the adapter's URL translations live

### Common issues
| Symptom                                  | Cause / Fix                                |
|------------------------------------------|--------------------------------------------|
| bat flashes and exits                     | LF line endings → convert to CRLF          |
| `'E:\Hermes' is not recognized`           | Space in path + bad cmd /c invocation      |
| llama-server OOM                          | Model > VRAM → NGL=0 (CPU only)            |
| WebUI model dropdown empty                | llama-server down + no GGUF in `data/models/` |
| WebUI "Model '' was not found"            | Model id mismatch → check llama-server `--alias` matches what's in `hermes.yaml` `llm.router.providers.local` |
| `MiniMax`/cloud "invalid api key"         | API key not activated on provider platform |
| WebUI stuck on "Loading..." forever       | `api-adapter.js` not loaded → check Network tab for /api/webui/* 404s |

### Reset to clean state
```bash
# Wipe Hermes in-memory chat session cache (only sessions created in this process lifetime)
"%HERMES_ROOT%\portable-python\python.exe" -c "from hermes.agent import HermesAgent; from hermes.config import load_config; a = HermesAgent(load_config(), use_mock=True); a._chat_sessions.clear(); print('cleared')"

# Run E2E test (no GPU needed)
"%HERMES_ROOT%\portable-python\python.exe" "%HERMES_ROOT%\tests\test_hermes.py"

# Verify NGL math
"%HERMES_ROOT%\portable-python\python.exe" "%HERMES_ROOT%\tests\verify_smart_ngl.py"

# Verify GGUF scan works
"%HERMES_ROOT%\portable-python\python.exe" -c "from modules.model_manager.gguf import list_gguf_models; from pathlib import Path; import json; print(json.dumps(list_gguf_models(Path(os.environ['HERMES_ROOT']) / 'data' / 'models'), indent=2, default=str))"
```

### Verify GPU is actually used
Open a separate terminal:
```bash
nvidia-smi
```
Look for `python.exe` or `llama-server.exe` row → check **GPU-Util** column.
If 0% → CPU mode, no GPU offload.

---

## 11. Testing

| Test                          | Purpose                                      | When to run        |
|-------------------------------|----------------------------------------------|---------------------|
| `tests\test_hermes.py`        | 17 E2E checks (mock LLM, no GPU)            | After major changes |
| `tests\verify_smart_ngl.py`  | Verify NGL math for all models               | After bat changes   |
| `bin\hermes-all.bat` e2e      | Real LLM full pipeline                       | Before commits      |

`test_hermes.py` uses `HERMES_LLM_MOCK=1` so it runs without GPU/LLM.

---

## 12. Known Limitations / TODO

- **GPU is RTX 3070 8GB** — fits 7B Q4_K_M, partial offload for 22GB qwen3
- **MiniMax API key not activated** — returns 2049 invalid
- **llama-server is single-model** — multi-model needs multi-instance
- **Hash embeddings are placeholders** — RAG quality is poor (until user runs `bin\install-embeddings.bat`)
- **WebUI streaming is now real (NEW 2026-06-07)** — `hermes/llm.py` `stream()` + `/api/chat/start` + `/api/chat/stream/{id}` SSE delivers per-chunk JSON `{type, content}` events. Adapter's old EventSource mock removed.
- **WebUI panels for workspaces / kanban / crons are real (NEW 2026-06-07)** — `hermes/workspace.py`, `hermes/kanban.py`, `hermes/cron.py` power them. See §4.
- **Kanban SSE / dispatch / comments / worktree are noop stubs** — UI falls back to 30s polling on `/api/kanban/events`. Real-time event push and agent dispatch are TODO.
- **Cron `/api/crons/pause` and `/resume` returned 400 in one harness test** — body schema mismatch; the endpoints are registered and respond 200 from the WebUI. Unverified whether the harness body was the issue; e2e-step3 in deliverable.md has the full request/response.
- **Auth is off** — Hermes WebUI has no login screen. Don't expose :7860 to the internet.
## 13. Roadmap: 1+2+4 Plan (in progress)

User confirmed priorities: **4 (KB) → 1 (embeddings) → 2A (autonomous tasks)** + native skill marketplace.

### ✅ 4. Knowledge Base management — DONE
- `hermes/scripts/rebuild_kb.py` — wipes `index.jsonl` + `sources/`, re-ingests `data/knowledge/*.md` with sane limits
- Per-doc cap: `--max-chunks 1000`
- Result: 256k bloated chunks → 13 clean chunks (5 files, all with embeddings)
- Runtime add: planned (TODO: `hermes kb add <path>` CLI)

### ⚙️ 1. Real embeddings — FRAMEWORK DONE, MODEL OPTIONAL
- `hermes/embeddings.py` — `SBERTEmbedder` (sentence-transformers) + `HashEmbedderFallback`
- `hermes/server.py` `/v1/embeddings` uses the new factory; auto-falls back to hash
- `bin\install-embeddings.bat` — installs sentence-transformers + downloads all-MiniLM-L6-v2 (~330MB)
  - **Not run yet** — user's internet is 137KB/s, big downloads are slow
  - When user has fast internet: `bin\install-embeddings.bat` (interactive, asks confirm)
  - Sets `HERMES_EMBEDDER=auto` (default) — uses sbert if installed, hash if not

### ✅ 2A. Autonomous task execution — DONE
- `hermes/planner.py` — `Planner` class with `TaskStep` / `TaskResult` dataclasses
- Loop: LLM generates JSON plan → execute skills one by one → on failure, replan → summarize
- CLI: `hermes task "<goal>" --mock --json` (use `--mock` to test without LLM)
- HTTP: `POST /api/task` (sync or async with task_id polling via `GET /api/task/{id}`)
- `hermes agent.run_task(goal)` method wraps the planner
- Constants: `MAX_REPLANS=3`, `MAX_STEPS=20` (prevent runaway)
- 17/17 tests pass (planner tested with mock)
- Real LLM test still pending (user needs to run with `bin\start-llm-smart.bat` first)
- Wrapper: `bin\hermes-task.bat "<goal>"` for one-liner use
- Health probe: `GET /health` returns `{status, version, cloud_available, local_available, mode}`

Original sketch (from planning):
```python
async def plan_and_execute(self, goal: str) -> str:
    plan = await self.llm.plan(goal, available_skills=self.skills.list())
    for step in plan:
        try:
            result = await self._execute_step(step)
        except Exception as e:
            plan = await self.llm.replan(goal, plan, step, e)
    return summary
```

Actual implementation lives in `hermes/planner.py`, much richer (replan on step failure, error recovery, JSON parsing with tolerance for non-strict LLM output).

### ✅ Skill marketplace — FRAMEWORK DONE
- `hermes/scripts/install_skill.py`:
  - `list` — show installed + registry
  - `install <name|url>` — download + verify SHA256 + safety check
  - `remove <name>` — uninstall
  - `publish <name> <url> --sha ... --desc ...` — add to registry
- Registry: `hermes/data/skills/registry.json` (JSON list of {name, url, sha256, desc})
- User can curate the registry themselves (or set up a public GitHub repo)



---


### ✅ 2026-06-07 — 6-track parallel integration — DONE
- **Track 1 streaming-and-sessions** (owner commit c93cb6b): real SSE via `hermes/llm.py stream()` + `hermes/sessions.py SessionStore` (atomic JSON, asyncio.Lock, one file per session at `data/sessions/<sid>.json`). Endpoints: `/api/chat/start` (returns `{stream_id, session_id, effective_model, effective_model_provider}`), `/api/chat/stream/{id}` (SSE `data: {type,content,...}`), `/api/chat/cancel`, `/api/chat/stream/status`, persistent `GET/PATCH/DELETE /api/chat/sessions{,/{id}}`. Adapter's old EventSource mock removed.
- **Track 2 workspace-browser**: `hermes/workspace.py` (HERMES_ROOT trust boundary, whitelist-gated file browser, path-traversal defense, atomic JSON). Endpoints: `/api/workspaces{,/add,/remove}`, `/api/list`, `/api/file`, `/api/media`. Persisted at `data/workspaces.json`.
- **Track 3 settings-persistence**: `hermes/webui_settings.py` (32-key DEFAULT_SETTINGS + 1-level nested deep-merge + atomic write). `GET/POST /api/webui/settings` is now real (was noop). Persisted at `data/webui_settings.json`.
- **Track 4 kanban-board**: `hermes/kanban.py` (KanbanStore: boards/tasks/events with atomic JSON, default board + 5 sample tasks bootstrap, 2000-event cap, CSS-safe color sanitizer). 22 endpoints registered. SSE/dispatch/comments/worktree are noop stubs (UI falls back to 30s polling). Persisted at `data/kanban/{boards,tasks,events}.json`.
- **Track 5 cron-scheduler**: `hermes/cron.py` (CronManager + Job dataclass + croniter + 30s background loop + shell/task/webhook action runners + UI-shape serializers). 10 endpoints. Started in `create_app` startup, stops on shutdown. `requirements.txt` + `croniter==6.0.0`. Persisted at `data/crons/jobs.json`.
- **Track 6 final-integration** (this task): 13 endpoints all 200 on a live mock-mode server; SSE 53+ chunks; settings `theme=sepia` + `display.streaming=false` survive `Stop-Process` + restart; session `e2e-test-session` (5 msgs) survives; kanban default board (6 tasks) survives; kanban CRUD + cron CRUD roundtrips verified. AGENTS.md §3/§4/§8/§12/§13 updated. Full transcript in `deliverable-final.md`.
- **Architecture now has 6 new modules** + 4 new data dirs/files (all in §3). Adapter grew from `~25 mapped + ~30 noop` to `~75 mapped, 0 noop` (the upstream WebUI's workspaces/kanban/crons/etc. panels are now real).

### ✅ WebUI panels: workspaces / kanban / crons — REAL
Previously `§12 Known Limitations` listed these as no-op. They are now backed by real modules (see §4 and the 6-track entry above). Adapter's noop transforms for these routes are gone.

### ⚠️ Real-time push + agent dispatch are still noop
- Kanban: SSE / dispatch / comments / worktree endpoints are stubs (UI uses 30s polling on `/api/kanban/events`).
- Crons: action runners are real; but no streaming/notification back to the WebUI.
- Next: WebSocket / SSE upgrade for kanban + cron status.

### Skill marketplace — unchanged
Still framework-only (`hermes/scripts/install_skill.py`); no marketplace backend yet.

## 14. Conversation Reference

This project was built across one long session on 2026-06-04/05. Key
turns (in Mavis conversation memory if picked up later):
- Built portable framework (Day 1)
- Fixed hermes-all.bat CRLF bug
- Integrated Open WebUI (overcame: missing __main__, RAG embedder,
  model id mismatch, Ollama auto-detect)
- Smart NGL launcher (overcame: 32-bit int overflow, nvidia-smi in for/f)
- Memory bank + cleanup (this file)

---

## 15. Installed Skills (NEW 2026-06-08)

14 skills live at `data/hermes-agent/skills/`, organized by upstream category.
Loaded into the Web UI agent via the `skills` toolset (see `config.yaml` `toolsets:`).

### From upstream `optional-skills/finance/` (copy)

| Skill | Purpose | Has `pip` deps in SKILL.md |
|---|---|---|
| `finance/excel-author` | Build .xlsx with named ranges + formula audit trail | `openpyxl>=3.0` |
| `finance/pptx-author` | Build .pptx with presentation design conventions | `python-pptx>=0.6` |
| `finance/comps-analysis` | Comparable Company Analysis (Excel model) | `openpyxl` |
| `finance/dcf-model` | DCF discounted cash flow model (Excel) | `openpyxl` |

### From upstream `skills/` built-in tree (copy, round 2)

| Skill | Purpose | Notes |
|---|---|---|
| `productivity/powerpoint` | Build .pptx with comprehensive conventions (1MB, biggest skill) | Full templates + slide-design rules |
| `productivity/ocr-and-documents` | OCR scanned PDFs / extract text + tables from images | Likely tesseract-based |
| `productivity/nano-pdf` | Lightweight PDF reading / extraction | Smallest skill (1.4 KB) |
| `productivity/google-workspace` | Google Docs / Sheets / Slides integration | 83 KB, requires `gcloud` auth |
| `creative/claude-design` | Visual design system + mockup conventions | 20 KB |

### From community GitHub repos (`git clone --depth=1`)

| Skill | Source | Purpose |
|---|---|---|
| `creative/drawio-skill` | `Agents365-ai/drawio-skill` | drawio diagram generation (flowcharts / architecture / ER / UML) |
| `creative/avoid-ai-writing` | `conorbronsdon/avoid-ai-writing` | Audit + rewrite text to strip AI-isms (multiple voice profiles) |
| `productivity/plur-memory` | `plur-ai/plur` (`skills/plur-memory/`) | Persistent engram memory across sessions |
| `productivity/plur-session-end` | `plur-ai/plur` (`skills/plur-session-end/`) | Extract durable learnings at session end |
| `autonomous-ai-agents/hermes-dojo` | `Yonkoo11/hermes-dojo` | Self-improvement system — analyzes past sessions, auto-patches skills |

### How they get discovered

- Web UI agent spawn-time: scans `~/.hermes/skills/` (= `E:\Hermes Agent\data\hermes-agent\skills/`)
- Each category dir has subdirs, each subdir has `SKILL.md` with `name:` frontmatter
- Slash commands (`/excel-author`, `/drawio-skill`, etc.) auto-injected as user messages (not system prompt) — preserves prompt caching
- Toolset filter: agent must have `skills` toolset enabled in `config.yaml` `toolsets:` list

### Install method (rate-limit-free)

`hermes skills install <name> --force` would route through `unified_search` → GitHub API (60 req/hr). To avoid burning the rate limit we used direct `git clone --depth=1` of each `user/repo` into the staging dir, then `shutil.copytree` into `data/hermes-agent/skills/<category>/<name>/`. For built-ins we just `shutil.copytree` from `E:\Hermes Agent\hermes-agent-source\skills/<cat>/<name>/` directly. The Web UI's skill scanner doesn't care about provenance, only the directory structure.

### Skipped (2 of 6 user-requested, from round 1)

- `research-agent` — not in upstream `skills/` or `optional-skills/`; needs a specific GitHub source URL from the user
- `multiagent` — same; closest upstream skill is `skills/research/research-paper-writing` but that's not the same thing

### To install more later

```bash
# Method 1: upstream catalog (uses GitHub API, may rate-limit)
"E:\Hermes Agent\portable-python\python.exe" "E:\Hermes Agent\hermes-agent-source\hermes_cli\skills_hub.py" install <name> --force

# Method 2: direct git clone (no rate limit) — for community skills
# 1. git clone --depth=1 https://github.com/<user>/<repo>.git to scratchpad
# 2. find the canonical SKILL.md (root or skills/<name>/)
# 3. shutil.copytree to data/hermes-agent/skills/<category>/<skill-name>/
# 4. restart Web UI

# Method 3: copy built-in (no network needed)
xcopy /E /I "E:\Hermes Agent\hermes-agent-source\skills\<cat>\<name>" "E:\Hermes Agent\data\hermes-agent\skills\<cat>\<name>"
```

---

## 16. Portability Audit (2026-06-08)

User asked: every file/service/dep/env that `hermes-all.bat` opens must be
**inside the `Hermes Agent` folder itself** — no system PATH, no
`E:\Hermes Agent\…` hardcoded literals, no missing `~/.mavis/...` lookups.
On a fresh Windows PC the project must be plug-and-play: copy the folder,
double-click `bin\hermes-all.bat`, browser opens. Goal: zero
post-install configuration.

### Audit method

For every script reached from `hermes-all.bat`:

1. `hermes-all.bat` (entry) → all the bat/ps1/py/binaries it spawns
2. For each child script: grep all path-like tokens; flag literals
   matching `[C-Z]:\\` (real drive letters, not `\s` regex escapes or
   `C:\Windows` placeholders in error text)
3. Cross-check env vars injected into subprocesses (NODE, PYTHONPATH,
   HERMES_*) resolve to paths under `HERMES_ROOT`
4. For Python: any module that uses `Path('E:\Hermes Agent')` literal
   instead of `Path(__file__).resolve().parents[N]`
5. Spot-check fallback (portable-python: runs in any cwd ✓; Node:
   bundled in `runtime/node/`)

### Fixes applied this session

| File | Was | Now |
|---|---|---|
| `bin/verify-server.bat` | `cd /d "E:\Hermes Agent"` + literal `E:\Hermes Agent\portable-python\python.exe` | `set "HERMES_ROOT=%~dp0.."` + `%HERMES_ROOT%\portable-python\python.exe` |
| `bin/webui-new.bat` | dev hint `mklink /J "E:\hermes-web-ui-main"` hardcoded in error message | portable git-clone hint pointing at `ArtificialAngels/hermes-agent` |
| `bin/webui-new.bat` | bootstrap wrote `data/hermes-agent/config.yaml` once and never touched it, so `mcp_servers.hermes-studio.env.HERMES_WEB_UI_HOME` stayed at the old install's drive letter | PowerShell fix-up block rewrites the two mcp env values to current `HERMES_ROOT` on every launch, idempotent |
| `hermes/scripts/install_skill.py` | `HERMES_ROOT = Path(r'E:\Hermes Agent')` | `HERMES_ROOT = Path(__file__).resolve().parents[2]` |
| `hermes/scripts/rebuild_kb.py` | `HERMES_ROOT = Path(r'E:\Hermes Agent')` | same |
| Root `start_llm_server.bat` | hardcoded `E:\` + dead code (called `local_llm_server.py` which doesn't exist + used PATH `python` not portable) | **deleted** (no references in repo) |
| Root `update_env.ps1` | hardcoded `E:\` + **contained a live MiniMax API key in plaintext** | **deleted** (would have leaked key to GitHub) |
| `data/logs/debug-residue-*.bat` (17) | early NGL debug scripts, never invoked | **deleted** |
| `data/logs/debug-residue-*.ps1` (1) | ps1 startup repro harness, never invoked | **deleted** |
| `data/logs/_diag*.txt` (25) | stdout from the deleted diag bats | **deleted** |
| `data/logs/_*.{log,err,txt}` (17) | other underscore-prefixed debug dumps from 6/5–6/6 sessions | **deleted** |
| `data/logs/removed-*.bat` (2) | backups of superseded scripts | **deleted** |

### Items intentionally left as-is

| File | Why kept |
|---|---|
| `hermes/scripts/import_ollama_blobs.py` L25 | `os.environ.get('USERPROFILE', r'C:\Users\PZS0X')` — env-var lookup with a one-user default that won't fire on a normal install. Cosmetic only. |
| `hermes/config.py` L169 | Comment `# E:\Hermes Agent\.env when running from anywhere` — doc, not code. |
| `hermes/workspace.py` L49 | Docstring example of the `workspaces.json` shape — not a real value. |
| `hermes/workspace.py` L75 | Already portable: `Path(__file__).resolve().parent.parent` ✓ |
| (removed) | `hermes/doctor.py` was deleted in an earlier cleanup phase; no portable rewrite needed. |
| `data/webui-new/app/bin/hermes-web-ui.mjs` L109 | `process.env.SystemRoot || 'C:\\Windows'` — env-var fallback, doesn't fire in practice. |
| `hermes-agent-source/scripts/install.ps1` L210 | User-facing hint about `setx NODE_EXTRA_CA_CERTS "C:\path\to\corp-ca.pem"` — placeholder text in a help message. |
| `data/webui-new/app/portable/*.bat`, `runtime/node/install_tools.bat`, etc. | Third-party (Node build scripts). Not touched. |

### Portability checklist (recurring)

When adding a new script, the test is mechanical:

```powershell
# from a fresh shell, with the folder on D:\ or C:\ or any drive:
cd D:\Hermes Agent
.\bin\hermes-all.bat
# → browser opens :8648, all 3 services up, model loads, chat works
# If anything needed a registry entry, %APPDATA% lookup, system Python,
# system Node, or `E:\` literal, audit fails.
```

### GitHub-readiness

- All hardcoded `E:\Hermes Agent\` literals that the project would have
  shipped: removed (4 files) or marked as doc-only (4 files).
- API keys that lived in repo-tracked `.ps1` files (`update_env.ps1`):
  removed. Remaining secrets live in `data/hermes-agent/config.yaml`
  (gitignored) and `.env` (gitignored).
- `data/webui-new/app/node_modules/` is the only remaining ~big
  dependency, shipped pre-bundled so `npm install` is not required.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Ikaros** (5074 symbols, 14071 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/Ikaros/context` | Codebase overview, check index freshness |
| `gitnexus://repo/Ikaros/clusters` | All functional areas |
| `gitnexus://repo/Ikaros/processes` | All execution flows |
| `gitnexus://repo/Ikaros/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
