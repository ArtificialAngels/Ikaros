# Hermes × Ikaros 补丁规范（Spec）

> **用途**：每次将 `runtime/hermes-agent` 更新到新版 upstream 后，稳定地重新打上 Ikaros 定制补丁。
> 本文件是**补丁意图的唯一事实来源（source of truth）**，同时供"确定性打补丁"和"LLM 兜底重实现"两套流程使用。
> 它解决的核心问题：upstream 大改时，旧 diff 对不上新代码；此时必须把"补丁要达成什么"告诉模型去重写，而不是给原始 diff。

## 0. 基线指针（每次重打后自动更新）

> **核心约定（2026-08-05 起）**：`runtime/hermes-agent` 仓库**永远保持纯净 upstream**——Ikaros 定制补丁**绝不进入 `runtime/hermes-agent` 的 git 历史**。补丁只以两种形式存在：① `patches/hermes/` 下的**补丁源文件**（被 Ikaros 主仓 git 跟踪，是事实源）；② 运行时由 `bin/hermes-update-and-patch.py --apply` 把 `patches/hermes/` 的 delta **重放到 `runtime/hermes-agent` 工作树，作为未提交的 working-tree overlay**（9 个 A 类文件 modified + 1 个 B 类目录 added），hermes 直接跑这个 overlay。因此 `runtime/hermes-agent` 的 `git status` 永远显示这些 overlay 改动（**这是预期、正确的**），`git log` 永远是纯 upstream。

- **Upstream tip**（`runtime/hermes-agent` 当前 HEAD，纯净 upstream 提交）：`f5be9236e00ddf2f2a412697f267078fc4ee068e`
- **Ikaros 补丁提交（overlay 源，不并入 HEAD）**：分支 `ikaros-patches-backup` → `5610941565c11f8e77a8a19691ea7253f20a659f`（最后一个带 Ikaros 补丁的已知良好提交）。它**不被 `runtime/hermes-agent` 的 HEAD 引用**，纯粹用于需要时整体恢复 overlay（`git checkout ikaros-patches-backup -- .`）或作为 `git diff <upstream_tip> <ikaros_commit>` 的 `theirs` 端计算 delta。**本段值是脚本运行的事实来源，不要手工改错。**
- **补丁事实源（overlay 内容来源）**：`patches/hermes/`（被 Ikaros 主仓 git 跟踪），镜像 `runtime/hermes-agent` 结构，含 9 个 A 类补丁文件 + 1 个 B 类技能目录 + 外置插件源 ikaros_v5（§6b）。
- **为什么不再 commit 单提交**：旧方案用 `finalize()` 把 overlay 提交成 `runtime/hermes-agent` 的一个 Ikaros 单提交，导致 upstream `git reset --hard` / `git log` 里混着 Ikaros 提交；且本腐败仓库的 `reset --hard` 偶发删 working-tree 文件（恢复用 `git checkout ikaros-patches-backup -- .`）。新方案让 `runtime/hermes-agent` 历史 100% upstream，overlay 始终是未提交工作树——`--apply` 每次 `reset --hard <upstream>` 后重放，干净可重入。

## 0.5. 补丁源文件位置
- **补丁源文件目录**：`patches/hermes/`（被 Ikaros 主仓库 git 跟踪）
- 镜像 `runtime/hermes-agent` 目录结构，存放 9 个 A 类补丁文件 + 1 个 B 类技能目录 + 1 个外置插件源（ikaros_v5，§6b）
- **不放在 `runtime/hermes-agent/` 下面**——防止 hermes `git reset --hard` / `git clean` 时被误删
- 它是**补丁事实源**，供三处使用：① `--apply` 的 `apply_patch_delta()` 以 `git diff <upstream_tip> <ikaros_commit>` 计算 delta 并 3-way 重放到 `runtime/hermes-agent` 工作树（overlay，不提交）；② B 类目录缺失时 `_copytree_lf()` 从它复制（LF 归一）；③ `finalize()` 的 `refresh_patch_source()` 把 A/B 类同步回这里，保持事实源与 overlay 源提交一致。
- 修改补丁内容时，**必须同时更新 `patches/hermes/` 里的源文件**，否则下次重打会用到旧版本。

## 1. 补丁总览
Ikaros 对 hermes 的定制分两类：
- **A 类：9 个 tracked 文件补丁**（动态，需随 upstream 重打）
- **B 类：3 个插件 / 技能目录**（静态，原样复制，不打补丁）

> 注：`package-lock.json` **不是** Ikaros 补丁——它直接取 upstream 版本（Ikaros 未改）。
> `config.yaml` 是本地运行配置（指向 Ikaros `:8080` 本地 LLM + deepseek），不属于补丁、不进提交、每次更新后保留即可。

## 2. 应用协议（3-way delta 重放，两步 + 兜底）
核心思路：不再 `git cherry-pick` 一个"重根提交"（父被钉死在旧 upstream，上游一推进即爆炸），而是以 **§0 的 `base`（= 当前 upstream tip）为起点、`§0 的 Ikaros 提交` 为终点**，每次重打实时生成 `base -> ikaros` 的 diff，用 **`git apply --3way`** 重放到**当前 upstream 工作树**。`--3way` 以 base 的 blob 作共同祖先，自动并入 upstream 自身改动，**只在 Ikaros 与 upstream 改了同一行时才冲突**——冲突面最小、最稳。

**A 类逐文件重放**（关键稳定性细节）：9 个 A 类文件**不合并成一个 patch**——单 patch 是原子的，任一文件失败会连累其它文件全部不落地。改为**逐文件** `git apply --3way`，单文件失败仅该文件告警、其余正常落地。**patch 必须以字节喂给 stdin**（`input=patch.encode("utf-8")`），否则 Windows 上 text 模式会把 `\n` 翻成 `\r\n`，破坏 unified diff 的 context 行导致匹配失败。

**marker 校验（与 commit 哈希解耦）**：`derive_markers(base, ikaros)` 从 `base->ikaros` diff 自动抽取每个 A 类文件的"签名行"（def/class/import/f-string/merge 等）；B 类用代表文件 + 关键词 marker。`markers_missing()` 只要 marker 在**工作树**即证明补丁落地，不依赖历史 commit，避免"重打新提交后误报缺失"。

### 2.1 确定性路径（`--apply`：完整更新）
1. `fetch_upstream()`（支持 `IKAROS_GIT_MIRROR` 镜像加速，避免直连 GitHub 卡死）→ 已基于 target 且补丁就位则跳过。
2. 打备份 tag → `git checkout -B main` → `git reset --hard <target>`（新 upstream，旧补丁由备份 tag 保留）。
3. `derive_markers` + `apply_patch_delta(base, ikaros)`（逐文件 3-way 重放到新 upstream）。
4. 有冲突标记（`scan_conflicts`）→ 升级 §2.3 LLM 兜底；无冲突则 `markers_missing` 复查。
5. markers 全在 → `finalize()`：跑 §4 验证 → **不提交**（保持 runtime/hermes-agent 纯 upstream 历史）→ `update_spec_pointers` 把 §0 改成（新 upstream tip, 原 overlay 源提交 `ikaros_commit`）→ `refresh_patch_source` 同步 `patches/hermes/` → **重建 TUI bundle**（`rebuild_tui()` 刷新 `ui-tui/dist/entry.js`，配合 `HERMES_TUI_DIR` 预构建路径，best-effort 非阻断）→ overlay 作为未提交工作树保留，hermes 直接运行它。

### 2.2 轻量路径（`--light-patch`：启动预检 / 检查补丁）
- **不 fetch / 不 reset**，仅当 `markers_missing` 才把 Ikaros delta 3-way 重放到**当前 HEAD**（专为"更新把补丁冲掉"的常见场景设计）。
- 冲突或重放后仍缺失 → `git reset --hard HEAD` 回滚、**不阻塞启动**（未打补丁的 hermes 仍可运行，仅缺 Ikaros 集成）；返回告警，建议手动跑 `--apply`。
- 干净 → 在当前 HEAD 保留未提交 working-tree overlay（**不提交**，runtime/hermes-agent 历史保持纯 upstream），返回成功。

### 2.3 兜底路径（LLM / 人工）
- 触发条件：3-way 冲突（Ikaros 与 upstream 真改同一行）或 markers 缺失（upstream 接口变了，旧 diff 不兼容）。
- 派一个**受约束的子 agent**（不是真起 hermes-agent 产品）指向 `runtime/hermes-agent`，喂入 §7 提示词模板 + §5 补丁意图。模型只允许改 §3 allowlist 文件，按意图在新代码上重实现。
- 完成后 `--finalize`：仅 `ensure_b_class` 确保 B 类（**不**用旧源覆盖 A 类）→ `finalize()` 验证 + 更新 §0（不提交）+ 刷新源。
- 验证（§4）任一失败 → 回滚备份 tag，报告失败。

## 3. 允许改动范围（allowlist，硬约束）
- 文件（A 类，9 个，与引擎 `A_CLASS_FILES` 严格一致）：`cron/scheduler.py`、`hermes_cli/web_server.py`、`plugins/context_engine/__init__.py`、`scripts/run_tests.sh`、`scripts/run_tests_parallel.py`、`tests/cron/test_scheduler.py`、`agent/conversation_loop.py`、`gateway/platforms/api_server.py`、`tools/mcp_tool.py`
- 目录（B 类，1 个）：`skills/creative/tldraw-skill/`
- **除以上外，任何文件都不得改动。** LLM 兜底时必须显式声明此 allowlist，防止模型"热心"重写其他代码。
- ⚠️ **08-04 起：ikaros_v5 不再属于仓库内补丁**——上下文引擎与记忆提供方已外置为 Hermes 用户插件（`$HERMES_HOME/plugins/ikaros_v5/`，源在 `patches/hermes/plugins/ikaros_v5/`），由 `ensure_external_plugins()` 部署，hermes 更新不影响。详见 §6。

## 4. 验证清单（两步都必须过）
- [ ] `runtime/hermes-agent` 工作树含**预期的 Ikaros overlay 改动**（9 个 A 类 `M ` + 1 个 B 类 `A `，以及允许的 `config.yaml` 等本地文件）；**不应**出现 `git status` 完全干净——overlay 不提交是正确状态。非 allowlist 的散落残留（如 `venv.broken`、`skills/mlops/*`、`optional-skills/*`、`website/...`、`err.txt` 等）若出现才是异常。
- [ ] hermes 可 import：`python -c "import hermes_cli.web_server, plugins.context_engine"`
- [ ] 外置插件可用且 Dashboard 可发现（08-04 起替代旧的 `list_context_engine_names` 检查，后者只扫仓库内目录、外置后返回空）：`python -c "from hermes_cli.plugins_cmd import _discover_context_engines; assert any(n == 'ikaros_v5' for n, _ in _discover_context_engines())"`；记忆提供方同理经 `load_memory_provider('ikaros_v5')` 非 None 且 `is_available()` 为 True
- [ ] `cron/scheduler.py` 的 `_cron_session_id` 为 `f"cron_{job_id}"`（固定，非带时间戳）
- [ ] `scripts/run_tests.sh` 同时探测 `bin/activate`（MSYS）与 `Scripts/activate`（Windows）
- [ ] 至少 `py_compile` 全过：`python -m compileall -q cron hermes_cli plugins scripts tests`
- [ ] 行尾：重放后 `git diff` 无意外 CRLF/LF 翻转（hermes `.gitattributes` 强制 CRLF）
- [ ] TUI bundle 新鲜度（**信息项，非阻断**）：`ui-tui/dist/entry.js` 存在且与当前 `ui-tui` 源码一致。`hermes update` 把 `ui-tui` 切到新 upstream 后，旧的 `entry.js` 会变成**陈旧 bundle**——若 upstream 改了 TUI 接口，陈旧 bundle 会让 9119 chat 运行时行为异常。补丁 `finalize` 会自动 `rebuild_tui()` 刷新；也可手动 `python bin/hermes-update-and-patch.py --rebuild-tui`。该 bundle 经 9100 面板的 `HERMES_TUI_DIR` 预构建路径被 Hermes 直接加载（跳过 `npm install`，避免沙箱删除闸门导致的 `Chat unavailable: 1`）。

## 5. 逐补丁细节（A 类：tracked 文件）

### 5.1 `plugins/context_engine/__init__.py`
- **意图**：新增 `list_context_engine_names()`，在**不 import 引擎**的前提下目录扫描 `plugins/context_engine/`，返回可用引擎名列表。供 Dashboard schema 动态列出 `context.engine` 可选项。⚠️ 08-04 起 ikaros_v5 已外置（§6b），该函数不再返回它——Dashboard 枚举改走 `plugins_cmd._discover_context_engines`（upstream 实现，含插件注册的引擎）。此补丁保留（Ikaros Dashboard 集成的一部分，无调用方、无害）。
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

### 5.7 `tools/mcp_tool.py`
- **意图（2026-08-02）**：MCP 配置 `${IKAROS_*}` 占位符**从 HERMES_HOME 结构自推导**，去掉对启动入口注入的依赖，并防止 `.env` 写死盘符覆盖动态值——项目换盘符后 MCP 零配置跟随。
- **实现**：新增模块级函数 `_inject_ikaros_root_paths()`，并在 `_load_mcp_config()` 中 `load_hermes_dotenv()` 之后、`_filter_suspicious_mcp_servers` 循环之前调用。
- **锚点**：`_inject_ikaros_root_paths` 定义在 `_load_mcp_config` 之前；调用插在 `load_hermes_dotenv()` 的 `except Exception: pass` 块之后。
- **逻辑**：布局守卫（`HERMES_HOME` 必须是 `<root>/data/hermes-agent` 且 `<root>/runtime/node/node.exe` 存在）→ 推导 `IKAROS_ROOT/IKAROS_RUNTIME/IKAROS_NODE/IKAROS_PYTHON/IKAROS_MEMORY/IKAROS_HERMES_HOME` → `os.environ[k] = v` **强制覆盖**（防 `.env` 旧盘符污染）。
- **约束**：守卫失败（非 Ikaros 结构）必须静默返回，不影响 hermes 原生部署。

## 6. B 类：静态插件目录（原样复制，不打补丁）
以下目录是 Ikaros 自有资产，**不随 upstream 变化**，每次更新后由引擎的 `ensure_b_class()` 自动补（代表文件存在且含 marker 即跳过，否则从 `patches/hermes/` 复制并 LF 归一；LLM 兜底也无需修改内容）：
- `skills/creative/tldraw-skill/` — tldraw 白板绘图技能（`tldraw-skill` v1.2.1，生成 `.tldr` 并导出 PNG/SVG）。

## 6b. 外置插件：ikaros_v5（2026-08-04 起，零侵入）

**架构**：ikaros_v5 上下文引擎 + 记忆提供方不再放进 `runtime/hermes-agent` 仓库，而是作为 **Hermes 用户插件**外置到运行时数据区：

- **运行时位置**：`data/hermes-agent/plugins/ikaros_v5/`（= `$HERMES_HOME/plugins/`，gitignore 的数据区；hermes 更新 `reset --hard` 不触碰）。
- **规范源**：`patches/hermes/plugins/ikaros_v5/`（Ikaros 主仓 git 跟踪），更新脚本 `ensure_external_plugins()` 幂等部署（代表文件含 marker 即跳过）。
- **发现机制（两条原生链路，均非补丁）**：
  - 上下文引擎：`plugin.yaml`（`kind: standalone` 显式声明，避开 memory-provider auto-coerce）+ `register(ctx)` 调 `ctx.register_context_engine()` → `agent/agent_init.py` 加载链路第 3 步 `get_plugin_context_engine()` 命中；**必须**在 config.yaml `plugins.enabled` 列表（通用插件系统 opt-in）。
  - 记忆提供方：memory 系统原生扫描 `$HERMES_HOME/plugins/`（`plugins/memory/__init__.py` 的 user 目录发现），`load_memory_provider("ikaros_v5")` 直接可用。
- **激活配置**（data/hermes-agent/config.yaml）：`context.engine: ikaros_v5`、`memory.provider: ikaros_v5`、`plugins.enabled: [ikaros_v5]`。
- **文件结构**：`__init__.py`（register 双兼容：`hasattr(ctx, ...)` 同时适配 PluginContext 与 _ProviderCollector）、`context_engine.py`（继承内置 ContextCompressor + `__deepcopy__` 支持插件单例 deepcopy）、`memory_provider.py`（`_resolve_root` 兜底 `parents[4]`，因外置路径变浅一级）、`plugin.yaml`。
- **验证**：更新脚本 `_IKAROS_V5_RUNTIME_CHECK` 覆盖 memory provider（load + is_available + initialize 载入 V5）、context engine（get_plugin_context_engine）、Dashboard 枚举（`_discover_context_engines` / `_discover_memory_providers` 均含 ikaros_v5）。

## 7. LLM 兜底提示词模板（第②步喂料）
> 将本模板 + §5 逐补丁意图 + §0 当前 upstream 提交 + 允许改动范围（§3）一起发给受约束子 agent。
> 模型须先用 `git diff <upstream> HEAD -- <allowlist 文件>` 看清当前状态，再按意图重实现，**只允许改 allowlist 文件**。

```
你是要把一组「Ikaros 对 hermes 的定制补丁」移植到 hermes 的新版 upstream 代码上。
仓库：E:\Ikaros\runtime\hermes-agent。当前 upstream 基线（base）：<填 §0 的 upstream 提交>；
当前 Ikaros 补丁提交：<填 §0 的 Ikaros 提交>。

【流程背景】本次是 3-way delta 重放的兜底路径：确定性脚本已用
`git apply --3way` 把 base->ikaros 的 diff 重放到新 upstream，但以下文件冲突或
marker 缺失（upstream 接口确实变了）。请你**按意图重实现**这些文件，而不是照搬旧 diff。

【硬约束】只允许修改以下文件/目录，其他任何文件都不要碰：
- A 类（9 个）：cron/scheduler.py, hermes_cli/web_server.py, plugins/context_engine/__init__.py,
  scripts/run_tests.sh, scripts/run_tests_parallel.py, tests/cron/test_scheduler.py,
  agent/conversation_loop.py, gateway/platforms/api_server.py, tools/mcp_tool.py
- B 类（1 个，已存在且正确就不要动）：skills/creative/tldraw-skill/
（若这些目录已存在且内容正确，不要改动它们，只确保存在。）
⚠️ ikaros_v5 已外置为 Hermes 用户插件（§6b），**不属于仓库内补丁**——不要创建
plugins/context_engine/ikaros_v5/ 或 plugins/memory/ikaros_v5/；它们由
`ensure_external_plugins()` 部署到 $HERMES_HOME/plugins/ikaros_v5/。

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
7. agent/conversation_loop.py：推理（thinking）透出钩子——仅当模型给出真实 reasoning 时才上抛。
8. gateway/platforms/api_server.py：reasoning 事件回调 _on_reasoning()，在 reasoning.available 时透出预览。

【代码风格】保持与周围代码一致；hermes .gitattributes 强制 CRLF，新增 .py 文件用 CRLF。
不要用英文旁白/续写污染代码；只做最小必要改动。

【修复流程（冲突 / marker 缺失时，你该怎么做）】
1. 先看清现状（在 E:\Ikaros\runtime\hermes-agent 里）：
   - `git status --porcelain` 看哪些文件被改/有冲突；`git log --oneline -3` 看 HEAD。
   - 找冲突标记：`grep -rn "^<<<<<<< " .`（3-way 留下的，须手动解决）。
   - 看当前 diff：`git diff <base 提交> HEAD -- <有问题的 A 类文件>`（base=§0 upstream 提交）。
2. 理解 3-way：ours=当前 upstream 工作树、base=§0 upstream 提交、theirs=§0 Ikaros 提交。
   冲突只在 Ikaros 与 upstream 改了**同一行**。解决原则：
   - 保留 upstream 的结构性代码（不要删 upstream 新增的独立函数，如 _timezone_options）。
   - 把 Ikaros 的"签名行"（见下方【落地判据】）重新加回对应位置，使功能恢复。
   - 实在对不上时，按 §5 意图在新代码上等价重写，但**只改 allowlist 文件**。
3. 每改完一个 A 类文件，确认它含【落地判据】里自己的签名行；B 类目录确保存在且含 marker。
4. 别碰 allowlist（§3）外文件；新增 .py 用 CRLF；不要英文旁白污染代码。
5. 验证 + 收尾：直接跑 `python E:\Ikaros\bin\hermes-update-and-patch.py --finalize`
   （自动跑 §4 验证 → **不提交**（overlay 保留为未提交工作树）→ 更新 §0 指针 → 刷新 patches/hermes/）。
   ⚠️ 不要自己 `git commit`/`git push`；--finalize 会处理收尾（不提交），且**绝不 push**。
6. 若某块后补定制（推理透出 / token_compressor）被上游覆盖冲掉，参考
   E:\Ikaros\tmp\hermes-reasoning-patches\ 与 E:\Ikaros\tmp\hermes-tokencompressor-patches\ 里的
   reapply 脚本重贴（这些目录是手动备份，可能不存在；不存在则按 §5 重做）。
7. 卡死无法修复：`git reset --hard <backup_tag>`（backup tag 名在
   tmp/hermes-patch-state.json 的 backup_tag 字段）回滚到更新前，并向调用方报告失败，
   不要让仓库停在半完成态。

【落地判据 marker（改完必须全部命中；确切列表由引擎动态注入到提示词，下表为当前样例）】
- A 类文件签名行（须出现在文件内）：
  · plugins/context_engine/__init__.py → def list_context_engine_names() / Safe to call at module import time
  · hermes_cli/web_server.py → def _context_engine_options() / Directory-scan only (no engine imports)
  · cron/scheduler.py → from tools.environments.local import _sanitize_subprocess_env / _cron_session_id = f"cron_{job_id}"
  · scripts/run_tests.sh → if "$PYTHON_CANDIDATE" -c 'import pytest' 2>/dev/null; then
  · scripts/run_tests_parallel.py → print(f"[ERROR] Failed to load json durations file! {e}")
  · tests/cron/test_scheduler.py → def test_disabled_servers_are_not_added / def test_no_mcp_config_adds_nothing
  · agent/conversation_loop.py → # Surface the model's TRUE reasoning (thinking) only when it
  · gateway/platforms/api_server.py → def _on_reasoning(...) / if event_type == "reasoning.available" and preview:
- B 类目录代表文件 + marker（目录须存在且代表文件含 marker）：
  · skills/creative/tldraw-skill/    → SKILL.md 含 tldraw
- 外置插件（§6b，由 ensure_external_plugins() 部署，不在此清单）：
  · $HERMES_HOME/plugins/ikaros_v5/  → __init__.py 含 class IkarosV5ContextEngine / class IkarosV5MemoryProvider

【完成后自检】
- python -c "import hermes_cli.web_server, plugins.context_engine" 通过
- Dashboard 枚举含外置 ikaros_v5：from hermes_cli.plugins_cmd import _discover_context_engines; assert any(n == 'ikaros_v5' for n, _ in _discover_context_engines())
- python -m compileall -q cron hermes_cli plugins scripts tests 通过
- git status 仅显示本次预期改动（无散落文件，无 ikaros_v5 目录）
全部通过后再告知完成。
```

## 8. 安全 / 幂等
- 更新前先确保 `ikaros-patches-backup` 分支存在（失败可用 `git checkout ikaros-patches-backup -- .` 整体恢复 overlay），或 `git stash` 当前未提交改动。
- **绝不**裸跑 `llama-server.exe`、**绝不**自动 `push`、**绝不**往 `runtime/hermes-agent` 历史提交 Ikaros 补丁（overlay 保持未提交）、**绝不**碰 allowlist 外文件。
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
  - **更新并打补丁** → `POST /api/hermes/update`：调用 `bin/hermes-update-and-patch.py --apply`（fetch + reset + 3-way delta 重放 + LLM 兜底 + 验证 + 更新 §0，overlay 不提交），返回脚本输出尾部。可能耗时数十秒～数分钟。

### 9.2 9119 启动预检
- `start_component_hermes_dashboard()` 在 spawn Hermes 之前调用 `ensure_hermes_patch_applied()`：
  - 补丁已就位（marker 全在）→ 直接启动。
  - 缺失 → **调用 `bin/hermes-update-and-patch.py --light-patch`**（轻量、**不 fetch / 不 reset**，仅当 markers 缺失时把 Ikaros delta 3-way 重放到当前 HEAD（overlay 不提交）），成功即启动。
  - 轻量补丁冲突（upstream 大改）→ `git reset --hard HEAD` 回滚并告警，**但仍继续启动**（未打补丁的 hermes 仍可运行，仅缺 Ikaros 集成）；后续由人工跑「更新并打补丁」走 LLM 兜底。
- 预检**绝不阻塞** 9119 启动；完整 fetch/reset/LLM 兜底只在手动按钮触发。
- **TUI bundle 新鲜度**：`hermes update` 后 `finalize` 会自动 `rebuild_tui()` 刷新 `ui-tui/dist/entry.js`（best-effort，失败仅告警不阻断）。9100 面板通过 `HERMES_TUI_DIR` 指向该 bundle 走 Hermes 官方「预构建路径」，跳过 `npm install` 以避免 `Chat unavailable: 1`。若你**不经面板**直接 `hermes dashboard`，须自行 `set HERMES_TUI_DIR=E:\Ikaros\runtime\hermes-agent\ui-tui`；独立刷新命令：`python bin/hermes-update-and-patch.py --rebuild-tui`。

### 9.3 补丁检测判据（重要）
- **落地判据用 marker，不用 commit 哈希**：`markers_missing(derive_markers(base, ikaros))` —— 只要 9 个 A 类文件的签名行 + 1 个 B 类的代表 marker 在工作树即证明补丁就位；外置 ikaros_v5 插件由 `ensure_external_plugins()` 单独部署并按 §6b 验证（`_IKAROS_V5_RUNTIME_CHECK`）。与"重打新提交 / 新 HEAD"解耦，永不误报「缺失」。
- 是否存在补丁提交历史用 `patch_present_via_grep()`（`git log --grep "apply Ikaros integration patches" HEAD`），仅作辅助展示。
- 状态接口：`GET /api/hermes/status` 返回 `{head, upstream_tip, patch_applied, dirty, detail}`，供前端轮询。

### 9.4 安全约束（延续 §8）
- 启动预检不 fetch / 不 reset / 不 push，只做最小 3-way 补丁。
- 完整 `hermes-update-and-patch.py --apply`（含 fetch/reset）仅由手动按钮触发，且脚本内**不提交、不实现 push**（`runtime/hermes-agent` 历史保持纯 upstream；overlay 不提交是正确状态，回滚用 `ikaros-patches-backup` 分支）。
- 任何路径都不碰 allowlist（§3）外文件。

## 10. 给其他智能体的修复手册（Agent Repair Guide）
> 当自动 3-way 重放无法干净落地（冲突或 marker 缺失），本工程会写出一份提示词让一个受约束子 agent 修复。**本节能让任何智能体（含未来不同的模型 / agent）独立上手，不必读源码。** 引擎生成提示词时也会把 §7 模板 + 动态 marker 判据一起喂给模型。

### 10.1 何时需要你出手
- 运行 `bin/hermes-update-and-patch.py --apply` 后停在「需要人工 / agent 步骤」，且 `tmp/hermes-patch-state.json` 的 `mode == "need-llm"`。
- 或直接看到脚本日志报 `3-way 重放冲突于：...` 或 `markers 缺失：...`。
- 启动预检（`--light-patch`）冲突不会卡启动，但面板会报「补丁缺失」，此时可手动跑 `--apply` 走兜底。

### 10.2 诊断三连（先看清再动手）
```bash
cd E:/Ikaros/runtime/hermes-agent
git status --porcelain                       # 哪些文件被改 / 有冲突
git log --oneline -3                         # 当前 HEAD
grep -rn "^<<<<<<< " .                       # 3-way 留下的冲突标记
git diff <base 提交> HEAD -- <问题文件>      # base = §0 upstream 提交
```
- 若 `git status` 出现 `config.yaml` 之外的散落文件（如 `venv.broken`、`skills/mlops/*`），那是历史残留，**不要动**——它们不在 allowlist，也不影响补丁。

### 10.3 修复原则（3-way 心智模型）
- ours = 当前 upstream 工作树，base = §0 upstream 提交，theirs = §0 Ikaros 提交。
- 冲突只在 Ikaros 与 upstream 改了**同一行**。解决：保留 upstream 结构 + 把 Ikaros 签名行加回；**绝不删 upstream 新增的独立函数**（如 `_timezone_options`），两个函数都留。
- 只看 §0 的 base / ikaros 两个提交哈希，不要凭记忆照抄旧 diff。

### 10.4 落地判据（改完必须全部命中）
- 运行以下命令打印**权威**清单（与引擎 `markers_missing` 完全一致）：
  ```bash
  cd E:/Ikaros && python -c "import importlib.util; s=importlib.util.spec_from_file_location('h','bin/hermes-update-and-patch.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); t=open('docs/hermes-ikaros-patches.md',encoding='utf-8').read(); b,i=m.parse_spec_pointers(t); mk=m.derive_markers(b,i); print('MISSING:', m.markers_missing(mk))"
  ```
  输出 `MISSING: []` = 全部命中；否则按缺的项逐文件补签名行 / 补 B 类目录。
- 签名行与 marker 的样例见 §7【落地判据 marker】；**以 `derive_markers(base, ikaros)` 实际输出为准**（引擎生成提示词时会动态注入到【落地判据 marker】段）。

### 10.5 收尾（不要自己 commit / push —— 2026-08-05 起脚本也不再 commit）
```bash
python E:/Ikaros/bin/hermes-update-and-patch.py --finalize
```
- 它会：跑 §4 验证 → **不提交**（runtime/hermes-agent 历史保持纯 upstream）→ 更新 §0 指针（Upstream tip = 本次 upstream；Ikaros 补丁提交 = overlay 源 `ikaros_commit`，永不指向 HEAD）→ 刷新 `patches/hermes/` 源文件 → 清理状态文件。overlay 作为未提交工作树保留，hermes 直接运行它。
- 回滚 overlay：`git checkout ikaros-patches-backup -- .`（整体恢复已知良好 Ikaros 工作树）；或 `git reset --hard ikaros-patches-backup` 回到带 Ikaros 提交的状态。
- **绝不 push**（runtime/hermes-agent 不再含 Ikaros 提交，push 无意义且会污染 `origin/main`）。

### 10.6 常见坑与回滚
- 后补定制被冲掉（推理透出 / token_compressor）：用 `tmp/hermes-reasoning-patches/`、`tmp/hermes-tokencompressor-patches/` 的 reapply 脚本重贴（目录可能不存在，不存在就按 §5 重做）。
- 卡死：`git reset --hard <backup_tag>`，backup tag 名在 `tmp/hermes-patch-state.json` 的 `backup_tag` 字段；回滚后向调用方报告，别留半完成态。
- 行尾：hermes `.gitattributes` 对 `*.sh` 强制 LF，新增 `.py` 用 CRLF，别翻来翻去造成脏树。
- 禁忌：别碰 allowlist（§3）外文件；别裸跑 llama-server；别自动 push。
