# Hermes 更新为何不会冲掉 Ikaros 集成（ikaros_v5 记忆提供方 + 压缩引擎协作）

> 适用范围：`core/hermes` 通过 `bin/hermes-update-and-patch.py` 走正规面板 / 脚本更新时。
> 关联规范：`docs/hermes-ikaros-patches.md`（补丁 spec，source of truth）。
> 记录背景：用户在 2026-08-03/04 连续更新 hermes 后，担忧「更新会把记忆提供方 + 压缩引擎这套集成冲掉」。本文给出确定性结论与机制佐证，推翻此前「更新有被清风险」的粗略判断。

## 0. 结论

**不会冲掉。** 记忆提供方 `ikaros_v5`（及其与 Hermes 压缩引擎协作的 `on_pre_compress` 链路）在正规更新流程下两层都安全：

1. 配置指向（`config.yaml` 里的 `memory.provider: ikaros_v5` / `context.engine: ikaros_v5`）位于独立目录，git 操作碰不到；
2. 插件实体（`core/hermes/plugins/*/ikaros_v5/` 目录）由更新脚本的 B 类目录重放机制 + 自带硬验证保证不被真丢。

## 1. 担忧来源：更新后 git 状态「看起来像被冲掉」

每次更新 `core/hermes` 指向新版 upstream 后，执行 `git status` 会看到：

- HEAD 从原 Ikaros 集成提交（如 `e939c80`）跳到上游新提交（如 `f5be9236e`）；
- `plugins/context_engine/ikaros_v5/`、`plugins/memory/ikaros_v5/`、`skills/creative/tldraw-skill/` 三个目录显示为未跟踪 `??`。

**这不是被冲掉，而是它们本来就不进 git 版本库。** `ikaros_v5` 等属于补丁 spec 的「B 类目录」（见 `docs/hermes-ikaros-patches.md` §3），从不纳入 hermes 提交，始终以未跟踪文件存在于磁盘。更新把 `core/hermes` 指向 upstream 后，这些目录不在 upstream 内，于是 git 把它们显示为未跟踪——但只要磁盘上文件还在，Hermes 通过目录自动发现机制（`core/hermes/plugins/context_engine/__init__.py` 扫描 `<name>/__init__.py` + `plugin.yaml`，不依赖硬编码 `import`）仍会自动加载它们。

实测验证：执行 `from plugins.context_engine import discover_context_engines; discover_context_engines()`，`ikaros_v5` 在列表且 `available=True`；`plugins/memory/ikaros_v5/IkarosV5MemoryProvider` 也在。集成一直正常接在 V5 上。

## 2. 配置层：config.yaml 绝对安全

`memory.provider: ikaros_v5` / `context.engine: ikaros_v5` 写在 `E:/Ikaros/data/hermes-agent/config.yaml`。

关键事实（`docs/hermes-ikaros-patches.md` §1 明确）：

- `config.yaml` 是**本地运行配置**，**不属于补丁、不进 hermes 提交**；
- 它的目录 `data/hermes-agent/` **独立于 `core/hermes` 子仓库**（挂在 `data/` 下）。

hermes 更新全程只操作 `core/hermes/` 内的 `git reset/checkout/clean`，**路径上根本碰不到 `data/hermes-agent/`**。因此更新永远不会修改 provider 配置值，记忆提供方始终指向 `ikaros_v5`。

## 3. 插件层：B 类目录重放 + 硬验证兜底

`ikaros_v5` 目录归属补丁脚本的「B 类目录」，更新脚本 `bin/hermes-update-and-patch.py` 专门保障它们不被真丢：

### 3.1 补丁源在主仓，不在 core/hermes 内

`PATCH_SOURCE_DIR = IKAROS_ROOT / "patches" / "hermes"`（脚本约 L72）。`patches/hermes/plugins/{context_engine,memory}/ikaros_v5/` 由 Ikaros 主仓库 git 跟踪，**完整源常驻**。spec §0.5 明确：源不放 `core/hermes/` 下，正是为了防止 hermes `git reset --hard` / `git clean` 误删。

### 3.2 幂等重放（ensure_b_class）

`ensure_b_class(d)`（脚本约 L388）：若磁盘代表文件已存在且含 marker（如 `class IkarosV5MemoryProvider`）即跳过；否则从 `patches/hermes/` 复制回来（`_copytree_lf`，LF 归一，约 L402）。**即便某次更新真的清掉了 B 类目录，下一轮 `--apply` 也会重建。**

### 3.3 自带硬验证（verify_ikaros_v5_runtime）

脚本约 L502–628 有一段**硬验证**：在 Hermes 子进程里实际执行

- `discover_memory_providers()` 能发现 `ikaros_v5`；
- 其 `available` 为 `True`；
- 进一步 `IkarosV5MemoryProvider.initialize("patch-verify")` **真载入 V5**。

**验证不过则直接拦停更新报错**（spec §4 验证清单的一部分），绝不会带着断裂的集成继续往下走。这等于把「更新后 ikaros_v5 还能不能用」作为发布门禁。

## 4. 唯一真实风险边界

只有当**绕过** `bin/hermes-update-and-patch.py`、手动在 `core/hermes` 内执行 `git clean -fdx`（带 `-x` 清忽略文件）或手动删除 `ikaros_v5` 目录时，集成才会真丢。走正规面板更新（面板 `run_hermes_update_and_patch` 调的就是该脚本，且已修复 env 注入根因——见 commit `065c9f0`）不会。

## 5. 相关代码 / 文档索引

| 位置 | 说明 |
|------|------|
| `docs/hermes-ikaros-patches.md` §0.5 / §1 / §2 / §3 / §4 | 补丁规范；config.yaml 不进提交、B 类目录清单、验证清单（行号随版本变动，以章节标题 / 函数名为准） |
| `bin/hermes-update-and-patch.py` `PATCH_SOURCE_DIR` | `= IKAROS_ROOT/"patches"/"hermes"` |
| `bin/hermes-update-and-patch.py` `B_CLASS_DIRS` | 含 `plugins/context_engine/ikaros_v5`、`plugins/memory/ikaros_v5`、`skills/creative/tldraw-skill` |
| `bin/hermes-update-and-patch.py` `ensure_b_class()` | 幂等重放 B 类目录（代表文件含 marker 则跳过，否则从 `patches/hermes/` 复制） |
| `bin/hermes-update-and-patch.py` `finalize()` / `rollback_to_backup()` | 其间 `git clean -fd -- B_CLASS_DIRS` 删除未跟踪 B 类，但下一轮 `--apply` 会重建 |
| `bin/hermes-update-and-patch.py` `verify_ikaros_v5_runtime()` | 子进程硬验证 ikaros_v5 可发现 + available + 实际载入 V5 |
| `data/hermes-agent/config.yaml` | `memory.provider` / `context.engine` = `ikaros_v5`（独立目录，更新碰不到） |
| `core/hermes/plugins/context_engine/__init__.py` | 目录自动发现机制（不依赖硬编码 import） |
| commit `065c9f0` | 修复面板更新未传 `HERMES_HOME` 致运行数据误写 C 盘的根因（与集成存亡无关，但属同期 hermes 更新健壮性修复） |
