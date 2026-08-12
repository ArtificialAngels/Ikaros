# Hermes 更新为何不会冲掉 Ikaros 集成（ikaros_v5 记忆提供方 + 压缩引擎协作）

> 适用范围：`runtime/hermes-agent` 通过 `bin/hermes-update-and-patch.py` 走正规面板 / 脚本更新时。
> 关联规范：`docs/hermes-ikaros-patches.md`（补丁 spec，source of truth）。
> 记录背景：用户在 2026-08-03/04 连续更新 hermes 后，担忧「更新会把记忆提供方 + 压缩引擎这套集成冲掉」。本文给出确定性结论与机制佐证，推翻此前「更新有被清风险」的粗略判断。

## 0. 结论

**不会冲掉。** 记忆提供方 `ikaros_v5`（及其与 Hermes 压缩引擎协作的 `on_pre_compress` 链路）在正规更新流程下两层都安全：

1. 配置指向（`config.yaml` 里的 `memory.provider: ikaros_v5` / `context.engine: ikaros_v5`）位于独立目录，git 操作碰不到；
2. 插件实体（`data/hermes-agent/plugins/ikaros_v5/`，**已外置为 Hermes 通用插件**，不在 `runtime/hermes-agent` 内）由更新脚本的 `ensure_external_plugins()` 幂等部署 + 自带硬验证保证不被真丢；`runtime/hermes-agent` 的 git 操作路径上根本碰不到它。

## 1. 现状：更新后 `runtime/hermes-agent` git 状态完全干净

自 2026-08-05 ikaros_v5 外置后，每次更新 `runtime/hermes-agent` 指向新版 upstream，`git status` **不再**出现任何 ikaros_v5 未跟踪目录——因为 ikaros_v5 实体已迁到 `data/hermes-agent/plugins/ikaros_v5/`（Hermes 数据区，gitignore），`runtime/hermes-agent` 内已无此目录，实测工作树无改动（相对上游干净）。

**这正是比 B 类重放更彻底的解耦**：ikaros_v5 活在 Hermes 数据区，经通用插件系统（`config.plugins.enabled: [ikaros_v5]` + 插件 `register()` 双注册）加载；`runtime/hermes-agent` 的任何 `reset/checkout/clean` 路径上都碰不到它。其余 7 个补丁仍走 B 类重放（§3.2），但与 ikaros_v5 逻辑解耦。

实测验证：外置插件经 `load_memory_provider("ikaros_v5")` + `discover_plugins()` 均被发现且 `available=True`，`IkarosV5MemoryProvider.initialize()` 真载入 V5（见 §3.3）。集成一直正常接在 V5 上。

**2026-08-05 活体验证（重启 hermes 栈后）**：Dashboard(9119) 活的 `/api/config/schema` 显示 `context.engine` options=`[compressor, ikaros_v5]`、`memory.provider` options 含 `ikaros_v5`，config 两项均设为选中；gateway(8642) `/health`=200 且 `/v1/models` 无 key 返 401（鉴权生效）。详见 `ikaros-as-hermes-agent-proposal.md` §5。

## 2. 配置层：config.yaml 绝对安全

`memory.provider: ikaros_v5` / `context.engine: ikaros_v5` 写在 `E:/Ikaros/data/hermes-agent/config.yaml`。

关键事实（`docs/hermes-ikaros-patches.md` §1 明确）：

- `config.yaml` 是**本地运行配置**，**不属于补丁、不进 hermes 提交**；
- 它的目录 `data/hermes-agent/` **独立于 `runtime/hermes-agent` 子仓库**（挂在 `data/` 下）。

hermes 更新全程只操作 `runtime/hermes-agent/` 内的 `git reset/checkout/clean`，**路径上根本碰不到 `data/hermes-agent/`**。因此更新永远不会修改 provider 配置值，记忆提供方始终指向 `ikaros_v5`。

## 3. 插件层：ikaros_v5 走 EXTERNAL_PLUGIN，其余补丁走 B 类重放

`ikaros_v5` 已于 2026-08-05 **外置**：它不再是 `runtime/hermes-agent` 内的 B 类目录，而是主仓源 `patches/hermes/plugins/ikaros_v5/` 经 `ensure_external_plugins()` 幂等部署到 `$HERMES_HOME/plugins/ikaros_v5/`（数据区，更新碰不到）。其余 7 个补丁仍走原 B 类重放机制（§3.2），两者解耦。更新脚本 `bin/hermes-update-and-patch.py` 专门保障它们不被真丢：

### 3.1 ikaros_v5 源在主仓（EXTERNAL_PLUGIN）

`EXTERNAL_PLUGIN_SRC = PATCH_SOURCE_DIR / "plugins" / "ikaros_v5"`（脚本约 L123），`EXTERNAL_PLUGIN_DST = IKAROS_ROOT / "data" / "hermes-agent" / "plugins" / "ikaros_v5"`（脚本约 L124）。`patches/hermes/plugins/ikaros_v5/`（`plugin.yaml` + `context_engine.py` + `memory_provider.py` + `__init__.py`）由 Ikaros 主仓库 git 跟踪，**完整源常驻**。源不放 `runtime/hermes-agent/` 下，正是为了防止 hermes `git reset --hard` / `git clean` 误删。

### 3.2 其余补丁：幂等重放（ensure_b_class）

`ensure_b_class(d)`（脚本约 L388）仍负责**其余 7 个补丁**（`web_server.py`/`run_tests.sh`/`cron/scheduler.py` 等，不含 ikaros_v5）：若磁盘代表文件已存在且含 marker 即跳过；否则从 `patches/hermes/` 复制回来（`_copytree_lf`，LF 归一，约 L402）。**即便某次更新真的清掉这些 B 类目录，下一轮 `--apply` 也会重建。**

### 3.3 自带硬验证（verify_ikaros_v5_runtime）

脚本约 L523–589 有一段**硬验证**：在 Hermes 子进程里实际执行（2026-08-05 改造为走两条原生发现链路）

- `load_memory_provider("ikaros_v5")`（memory 系统 user 目录扫描）能发现 ikaros_v5 且 `available=True`；
- `discover_plugins()` + `get_plugin_context_engine()`（通用插件系统）返回 ikaros_v5 且 `available=True`；
- Dashboard 枚举 `_discover_context_engines()` / `_discover_memory_providers()` 两条列表均含 ikaros_v5；
- 进一步 `IkarosV5MemoryProvider.initialize("patch-verify")` **真载入 V5**。

**验证不过则直接拦停更新报错**（spec §4 验证清单的一部分），绝不会带着断裂的集成继续往下走。这等于把「更新后 ikaros_v5 还能不能用」作为发布门禁。2026-08-05 实测 PASS。

## 4. 真实风险边界

- 只有当**绕过** `bin/hermes-update-and-patch.py`、手动在 `runtime/hermes-agent` 内执行 `git clean -fdx` 或手动删除目录时，其余 B 类补丁才会真丢（ikaros_v5 已不在 `runtime/hermes-agent`，不受此影响）。
- ikaros_v5（外置）的真丢条件：**手动删除 `data/hermes-agent/plugins/ikaros_v5/`** 且该删除发生在两次 `--apply` 之间、且主仓 `patches/hermes/plugins/ikaros_v5/` 源也丢失——三者同时发生才真丢；正常更新流程下 `ensure_external_plugins()` 保证重建。
- 走正规面板更新（面板 `run_hermes_update_and_patch` 调的就是该脚本，且已修复 env 注入根因——见 commit `065c9f0`）不会冲掉任何集成。

## 5. 相关代码 / 文档索引

| 位置 | 说明 |
|------|------|
| `docs/hermes-ikaros-patches.md` §0.5 / §1 / §2 / §3 / §4 | 补丁规范；config.yaml 不进提交、B 类目录清单、验证清单（行号随版本变动，以章节标题 / 函数名为准） |
| `bin/hermes-update-and-patch.py` `PATCH_SOURCE_DIR` | `= IKAROS_ROOT/"patches"/"hermes"` |
| `bin/hermes-update-and-patch.py` `EXTERNAL_PLUGIN_SRC` / `EXTERNAL_PLUGIN_DST` | `patches/hermes/plugins/ikaros_v5` → `data/hermes-agent/plugins/ikaros_v5`（ikaros_v5 外置部署源/目标） |
| `bin/hermes-update-and-patch.py` `B_CLASS_DIRS` | 含其余 7 个补丁（`web_server.py`/`run_tests.sh`/`cron/scheduler.py` 等，**不含** ikaros_v5，ikaros_v5 已转 EXTERNAL_PLUGIN） |
| `bin/hermes-update-and-patch.py` `ensure_b_class()` | 幂等重放**其余** B 类补丁（代表文件含 marker 则跳过，否则从 `patches/hermes/` 复制） |
| `bin/hermes-update-and-patch.py` `ensure_external_plugins()` | 幂等部署 ikaros_v5 到 `$HERMES_HOME/plugins/`（hermes 仓库外，更新不受影响） |
| `bin/hermes-update-and-patch.py` `finalize()` / `rollback_to_backup()` | 其间 `git clean -fd -- B_CLASS_DIRS` 删除未跟踪 B 类，但下一轮 `--apply` 会重建 |
| `bin/hermes-update-and-patch.py` `verify_ikaros_v5_runtime()` | 子进程硬验证 ikaros_v5 可发现 + available + 实际载入 V5 |
| `data/hermes-agent/config.yaml` | `memory.provider` / `context.engine` = `ikaros_v5`（独立目录，更新碰不到） |
| `runtime/hermes-agent/hermes_cli/plugins.py` | 通用插件系统发现（`discover_plugins` / `get_plugin_context_engine`，ikaros_v5 经 `plugins.enabled` + `register()` 注册） |
| commit `065c9f0` | 修复面板更新未传 `HERMES_HOME` 致运行数据误写 C 盘的根因（与集成存亡无关，但属同期 hermes 更新健壮性修复） |
