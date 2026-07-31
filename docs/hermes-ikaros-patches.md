# Hermes × Ikaros 补丁规范（Spec）

> **用途**：每次将 `core/hermes` 更新到新版 upstream 后，稳定地重新打上 Ikaros 定制补丁。
> 本文件是**补丁意图的唯一事实来源（source of truth）**，同时供"确定性打补丁"和"LLM 兜底重实现"两套流程使用。
> 它解决的核心问题：upstream 大改时，旧 diff 对不上新代码；此时必须把"补丁要达成什么"告诉模型去重写，而不是给原始 diff。

## 0. 基线指针（每次重打后必须更新）
- **Upstream tip**（打补丁所基于的 upstream 提交）：`14db1a9`
- **Ikaros 补丁提交**（单提交）：`42255841b` — 含 e939c80 七个补丁 + 推理透出/token_compressor 增强（父链 14db1a9 ← e939c80 ← 本提交）
- 更新方式：重打完成后把本段两个值改成新的对应提交（新 Ikaros 提交的父 = 新 upstream tip）。

## 0.5. 补丁源文件位置
- **补丁源文件目录**：`patches/hermes/`（被 Ikaros 主仓库 git 跟踪）
- 镜像 hermes-agent 目录结构，存放 7 个 A 类补丁文件 + 3 个 B 类插件/技能目录
- **不放在 `core/hermes/` 下面**——防止 hermes `git reset --hard` / `git clean` 时被误删
- `bin/hermes-update-and-patch.py` 的 `restore_from_source()` 从此目录读取，作为 cherry-pick 失败时的轻量降级路径（比 LLM 兜底更快、更可靠）
- 修改补丁内容时，**必须同时更新 `patches/hermes/` 里的源文件**，否则下次重打会用到旧版本

## 1. 补丁总览
Ikaros 对 hermes 的定制分两类：
- **A 类：7 个 tracked 文件补丁**（动态，需随 upstream 重打）
- **B 类：3 个插件 / 技能目录**（静态，原样复制，不打补丁）

> 注：`package-lock.json` **不是** Ikaros 补丁——它直接取 upstream 版本（Ikaros 未改）。
> `config.yaml` 是本地运行配置（指向 Ikaros `:8080` 本地 LLM + deepseek），不属于补丁、不进提交、每次更新后保留即可。

## 2. 应用协议（三步法）
1. `git fetch` → `git checkout <新 upstream tip>`。
2. **第①步（确定性）**：`git cherry-pick 42255841b`（即当前 Ikaros 提交，含 e939c80 全部补丁 + 推理透出/token_compressor 增强）。
   - 干净通过 → 跳到 §4 验证。
   - 冲突 / 失败 → 视为"相关区域有大改"，升级第②步。
   - （让 git 冲突当"是否大改"的检测器，不要另写启发式。）
3. **第②步（源文件恢复）**：从 `patches/hermes/` 直接复制补丁源文件到 `core/hermes/` 工作树（`restore_from_source()`）。
   - 这是比 LLM 更轻量的降级路径——如果补丁内容本身不需要随 upstream 变化（大多数情况），直接复制即可。
   - 复制后跑 §4 验证。通过 → 跳到提交 + 更新 §0。
   - 验证失败（upstream 接口确实变了，旧补丁不兼容）→ 升级第③步。
4. **第③步（LLM 兜底）**：派一个**受约束的子 agent**（不是真起 hermes-agent 产品）指向 `core/hermes`，喂入 §7 的提示词模板 + 本文件 §5 的补丁意图。模型只允许改 §3 的 allowlist 文件，按意图在新代码上重实现。
   - 完成后必须 §4 验证通过。
   - 验证通过后 **`git commit` 生成新的 Ikaros 单提交**，并把 §0 基线指针更新为新提交（父 = 新 upstream tip），形成可维护循环。
   - **同步更新 `patches/hermes/` 源文件**（把 LLM 重实现后的新版本复制回去）。
5. **验证**（见 §4）。任一失败 → 回滚到更新前快照，报告失败。

## 3. 允许改动范围（allowlist，硬约束）
- 文件：`cron/scheduler.py`、`hermes_cli/web_server.py`、`plugins/context_engine/__init__.py`、`scripts/run_tests.sh`、`scripts/run_tests_parallel.py`、`tests/cron/test_scheduler.py`
- 目录：`plugins/context_engine/ikaros_v5/`、`plugins/memory/ikaros_v5/`、`skills/creative/tldraw-skill/`
- **除以上外，任何文件都不得改动。** LLM 兜底时必须显式声明此 allowlist，防止模型"热心"重写其他代码。

## 4. 验证清单（两步都必须过）
- [ ] `git status` 干净（除本地 `config.yaml` 外无残留；之前的散落文件如 `venv.broken`、`skills/mlops/*`、`optional-skills/*`、`website/...` 等不得出现）
- [ ] hermes 可 import：`python -c "import hermes_cli.web_server, plugins.context_engine"`
- [ ] `list_context_engine_names()` 能发现 `ikaros_v5`：`python -c "from plugins.context_engine import list_context_engine_names; assert 'ikaros_v5' in list_context_engine_names()"`
- [ ] `cron/scheduler.py` 的 `_cron_session_id` 为 `f"cron_{job_id}"`（固定，非带时间戳）
- [ ] `scripts/run_tests.sh` 同时探测 `bin/activate`（MSYS）与 `Scripts/activate`（Windows）
- [ ] 至少 `py_compile` 全过：`python -m compileall -q cron hermes_cli plugins scripts tests`
- [ ] 行尾：提交后 `git diff` 无意外 CRLF/LF 翻转（hermes `.gitattributes` 强制 CRLF）

## 5. 逐补丁细节（A 类：tracked 文件）

### 5.1 `plugins/context_engine/__init__.py`
- **意图**：新增 `list_context_engine_names()`，在**不 import 引擎**的前提下目录扫描 `plugins/context_engine/`，返回可用引擎名列表。供 Dashboard schema 动态列出 `context.engine` 可选项（含 `ikaros_v5`）。
- **锚点**：插在 `discover_context_engines()` 函数定义**之后**。
- **参考实现**（来自 `8edf1ec8f`）：
```python
def list_context_engine_names() -> List[str]:
    """Scan plugins/context_engine/ for engine directory names (no import).

    Safe to call at module import time — only a directory listing, no engine
    imports. Returns the bare plugin names so the Dashboard schema can list
    them as selectable ``context.engine`` values. The built-in ``"compressor"``
    is owned by the runtime (not this directory) and is added by the caller.
    """
    names = []
    if not _CONTEXT_ENGINE_PLUGINS_DIR.is_dir():
        return names
    for child in sorted(_CONTEXT_ENGINE_PLUGINS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        if not (child / "__init__.py").exists():
            continue
        names.append(child.name)
    return names
```
- **约束**：纯目录扫描，禁止 import 任何引擎（否则拖慢模块导入 / 可能循环依赖）。`compressor` 由调用方加，本函数不返回它。

### 5.2 `hermes_cli/web_server.py`（3 处改动）
- **意图**：让 Dashboard 的 `context.engine` 下拉**动态发现**已安装的引擎，而非写死 `["default","custom"]`。
- **锚点 / 改动点**：
  1. 在 `_timezone_options()` 之后新增 `_context_engine_options()`。
  2. `_SCHEMA_OVERRIDES` 里 `"context.engine"` 的 `"options"` 由 `["default","custom"]` 改为 `_context_engine_options()`。
  3. 在 `_memory_provider_schema_options()` 之后新增 `_context_engine_schema_options(cfg)`，并在 `_schema_with_dynamic_provider_options()` 里 `merge("context.engine", _context_engine_schema_options(cfg))`。
- **参考实现**：
```python
def _context_engine_options() -> List[str]:
    """Discovered context engines for the ``context.engine`` select.

    Directory-scan only (no engine imports), safe at module import time.
    ``"compressor"`` (the built-in) is always first; discovery failures
    degrade to the bundled default rather than dropping the field.
    """
    options = ["compressor"]
    try:
        from plugins.context_engine import list_context_engine_names

        options.extend(list_context_engine_names())
    except Exception:
        pass
    # Dedupe, preserve order
    return list(dict.fromkeys(options))


def _context_engine_schema_options(cfg: Dict[str, Any]) -> List[str]:
    """Discovered context engines for a per-request schema merge.

    Reuses the cheap directory scan of :func:`_context_engine_options` and
    additionally preserves the currently-configured engine, so a value
    selected in config but not (yet) discoverable — e.g. a plugin removed
    from disk — never silently vanishes from the dropdown.
    """
    options = _context_engine_options()

    ctx = cfg.get("context")
    configured = ctx.get("engine") if isinstance(ctx, dict) else None
    if configured and configured not in options:
        options = [*options, configured]

    return options
```
并在 `_schema_with_dynamic_provider_options()` 内 `merge("memory.provider", _memory_provider_schema_options(cfg))` 之后加：
```python
    merge("context.engine", _context_engine_schema_options(cfg))
```
- **约束（重要）**：若 upstream 已新增 `_timezone_options()` 等**独立函数**，两者都保留，互不覆盖；只新增 Ikaros 函数。若 upstream 把 `_SCHEMA_OVERRIDES` 改名 / 重构，按新结构等价插入，不要删 upstream 原有字段。

### 5.3 `cron/scheduler.py`
- **意图**：cron 的 session id 按 `job_id` **复用固定值**，避免每个 cron 周期新建 session 导致堆积。
- **锚点**：`run_job()` 内 `_cron_session_id = f"cron_{job_id}_{...时间戳...}"` 这一行。
- **参考实现**（替换那一行并加注释）：
```python
    # 按 job_id 复用固定 session: 避免每个 cron 周期产生新 session 堆积
    # 所有同 job 的 think 循环追加到同一个 session 里
    _cron_session_id = f"cron_{job_id}"
```
- **约束**：仅改这一处；配套测试 `tests/cron/test_scheduler.py` 的断言也要同步改（见 5.6）。

### 5.4 `scripts/run_tests.sh`
- **意图**：在 U 盘 / Windows 便携环境下正确探测 venv——同时支持 MSYS 的 `bin/activate` 与 Windows 的 `Scripts/activate`，并透传 `PYTHONUTF8=1`、`HOMEDRIVE` / `HOMEPATH` / `USERPROFILE` 等环境变量。
- **锚点**：venv 探测那段（约第 41–98 行）。
- **参考实现（核心片段）**：
```bash
PYTHON=""
VENV=""

# 1) Scan known venv locations for activate script
for candidate in "$REPO_ROOT/.venv" "$REPO_ROOT/venv" "$HOME/.hermes/hermes-agent/venv"; do
  # MSYS2/Git-Bash venv → bin/activate; Windows venv → Scripts/activate
  if [ -f "$candidate/bin/activate" ]; then
    VENV="$candidate"
    VENV_BIN="bin"
    break
  fi
  if [ -f "$candidate/Scripts/activate" ]; then
    VENV="$candidate"
    VENV_BIN="Scripts"
    break
  fi
done
# 2) verify pytest; 3) fall back to HERMES_PYTHON; env -i 段加 PYTHONUTF8=1 / HOMEDRIVE / HOMEPATH / USERPROFILE
```
（完整 58 行改动以 `8edf1ec8f` 为准，重打时 `git show 8edf1ec8f -- scripts/run_tests.sh` 取原文。）
- **约束**：保留原 pytest 存在性检查；不要删 upstream 的 Nix devShell / `HERMES_PYTHON` 回退逻辑。

### 5.5 `scripts/run_tests_parallel.py`
- **意图**：修一个 f-string 笔误（`print("[ERROR] ... {e}")` 漏了 `f` 前缀）。
- **锚点**：`_load_durations()` 里的那行 print。
- **参考实现**：
```python
        print(f"[ERROR] Failed to load json durations file! {e}")
```
- **约束**：仅此一行；上游若已修则无需再改（冲突检测会自然跳过）。

### 5.6 `tests/cron/test_scheduler.py`
- **意图**：同步 5.3 的 session id 变更——断言由 `startswith("cron_test-job_")` 改为 `== "cron_test-job"`（两处：`TestRunJobSessionPersistence` 中两处断言）。
- **参考实现**：两处均改为 `assert kwargs["session_id"] == "cron_test-job"` / `assert sid == "cron_test-job"`。
- **约束**：必须与 5.3 同步；若上游测试结构大改，按新结构等价断言，不要删其他测试。

## 6. B 类：静态插件目录（原样复制，不打补丁）
以下目录是 Ikaros 自有资产，**不随 upstream 变化**，每次更新后直接原样复制进 `core/hermes` 对应路径即可（它们已包含在 `8edf1ec8f` 提交中，cherry-pick 会一并带过来；若走 LLM 兜底也只需 `cp -r`，无需修改内容）：
- `plugins/context_engine/ikaros_v5/` — Ikaros V5 上下文引擎插件（`name: ikaros_v5`，把 V5 记忆注入压缩摘要以保留长对话连续性）。
- `plugins/memory/ikaros_v5/` — Ikaros V5 记忆 provider 插件（`name: ikaros-v5`）。
- `skills/creative/tldraw-skill/` — tldraw 白板绘图技能（`tldraw-skill` v1.2.1，生成 `.tldr` 并导出 PNG/SVG）。

## 7. LLM 兜底提示词模板（第②步喂料）
> 将本模板 + §5 逐补丁意图 + §0 当前 upstream 提交 + 允许改动范围（§3）一起发给受约束子 agent。
> 模型须先用 `git diff <upstream> HEAD -- <allowlist 文件>` 看清当前状态，再按意图重实现，**只允许改 allowlist 文件**。

```
你是要把一组「Ikaros 对 hermes 的定制补丁」移植到 hermes 的新版 upstream 代码上。
仓库：E:\Ikaros\core\hermes。当前 upstream 基线：<填 §0 的 upstream 提交>。

【硬约束】只允许修改以下文件/目录，其他任何文件都不要碰：
- cron/scheduler.py, hermes_cli/web_server.py, plugins/context_engine/__init__.py,
  scripts/run_tests.sh, scripts/run_tests_parallel.py, tests/cron/test_scheduler.py
- plugins/context_engine/ikaros_v5/, plugins/memory/ikaros_v5/, skills/creative/tldraw-skill/
（若这些目录已存在且内容正确，不要改动它们，只确保存在。）

【补丁意图】逐条（详见 hermes-ikaros-patches.md §5）：
1. plugins/context_engine/__init__.py：新增 list_context_engine_names()，目录扫描返回引擎名，不 import 引擎。
2. hermes_cli/web_server.py：新增 _context_engine_options() 与 _context_engine_schema_options(cfg)，
   并把 _SCHEMA_OVERRIDES 的 context.engine.options 改为动态发现。
   ⚠️ 若 upstream 已有同名/类似独立函数（如 _timezone_options），两者都保留，互不覆盖。
3. cron/scheduler.py：_cron_session_id 改为固定 f"cron_{job_id}"（去掉时间戳），并同步改对应测试。
4. scripts/run_tests.sh：venv 探测同时支持 bin/activate 与 Scripts/activate，env -i 段透传
   PYTHONUTF8=1、HOMEDRIVE、HOMEPATH、USERPROFILE。
5. scripts/run_tests_parallel.py：修 _load_durations 的 f-string 笔误。
6. tests/cron/test_scheduler.py：session id 断言由 startswith("cron_test-job_") 改为 == "cron_test-job"（两处）。

【代码风格】保持与周围代码一致；hermes .gitattributes 强制 CRLF，新增文件用 CRLF。
不要用英文旁白/续写污染代码；只做最小必要改动。

【完成后自检】
- python -c "import hermes_cli.web_server, plugins.context_engine" 通过
- from plugins.context_engine import list_context_engine_names; assert 'ikaros_v5' in list_context_engine_names()
- python -m compileall -q cron hermes_cli plugins scripts tests 通过
- git status 仅显示本次预期改动（无散落文件）
全部通过后再告知完成。
```

## 8. 安全 / 幂等
- 更新前先 `git stash` 或打一个备份提交，便于失败回滚。
- **绝不**裸跑 `llama-server.exe`、**绝不**自动 `push`、**绝不**碰 allowlist 外文件。
- 失败时产出清晰报告：应用了什么 / 哪步失败 / LLM 改了哪些文件。
- 半完成态极难收拾（本次已踩坑：缺失 git 对象 + 18 个散落文件 + 未提交），脚本须能从任意中间态安全恢复或整体回滚。
- WorkBuddy 沙箱 safe-delete 可能 fail-closed 拦截删除（对 `venv.broken` 这类残留），清理用同盘 `mv` 移到 `tmp/` 而非硬删。

## 9. 控制面板集成（9100 更新控制 + 9119 启动预检）

> 把「补丁维护」从纯脚本提升到产品层：让 9100 控制面板能直观看到 hermes 版本与补丁状态、并一键更新；并让 9119（Hermes Dashboard）每次启动前自动确认补丁已就位。
> 实现位置：`core/dashboard/server.py`（后端）+ `core/dashboard/index.html` / `panel.html` + `assets/dashboard.css`（前端）。

### 9.1 9100 面板 — Hermes 版本更新控制
- `hermes_dashboard` 卡片新增「版本 / 补丁」区，实时显示：
  - `HEAD` 短哈希（当前 hermes 版本）
  - 上游 tip（取自 §0 `Upstream tip`，信息展示用）
  - 补丁状态徽标：`补丁已打` / `补丁缺失` / `未知`，以及 `有未跟踪改动` 提示（允许 `config.yaml`）
- 两个按钮：
  - **检查补丁** → `POST /api/hermes/check`：跑与启动同款的预检（缺失则自动补上），返回结果。
  - **更新并打补丁** → `POST /api/hermes/update`：调用 `bin/hermes-update-and-patch.py --apply`（fetch + reset + cherry-pick + LLM 兜底 + 验证 + 更新 §0），返回脚本输出尾部。可能耗时数十秒～数分钟。

### 9.2 9119 启动预检
- `start_component_hermes_dashboard()` 在 spawn Hermes 之前调用 `ensure_hermes_patch_applied()`：
  - 补丁已就位 → 直接启动。
  - 缺失 → **自动把 §0 的 Ikaros 补丁 commit `git cherry-pick` 到当前 HEAD**（轻量、**不 fetch / 不 reset**，专为「更新把补丁冲掉」的常见场景设计），成功即启动。
  - cherry-pick 冲突（upstream 大改）→ `git cherry-pick --abort` 并告警，**但仍继续启动**（未打补丁的 hermes 仍可运行，仅缺 Ikaros 集成）；后续由人工跑「更新并打补丁」走 LLM 兜底。
- 预检**绝不阻塞** 9119 启动；完整 fetch/reset/LLM 兜底只在手动按钮触发。

### 9.3 补丁检测判据（重要）
- 不用 `git merge-base --is-ancestor <固定 Ikaros commit>` 判定 —— 分层 cherry-pick / 重打新提交后，原 commit 不再是 HEAD 祖先，会误报「缺失」。
- 改用 `git log --oneline --grep "apply Ikaros integration patches" HEAD`：覆盖**原提交 / 分层 cherry-pick / 重打新提交**三种来源，稳定可靠。
- 状态接口：`GET /api/hermes/status` 返回 `{head, upstream_tip, patch_applied, dirty, detail}`，供前端轮询。

### 9.4 安全约束（延续 §8）
- 启动预检不 fetch / 不 reset / 不 push，只做最小 cherry-pick 补丁。
- 完整 `hermes-update-and-patch.py --apply`（含 fetch/reset）仅由手动按钮触发，且脚本内**不实现 push**（本地 `main` 是 Ikaros 修补分支，push 会污染 `origin/main`）。
- 任何路径都不碰 allowlist（§3）外文件。
