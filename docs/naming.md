# Ikaros 命名字典 (Naming Dictionary)

> 项目历史上出现过多种大小写 / 缩写混用。本文档统一命名，避免文档与代码漂移。
> 检查实现：`python docs/lint.py` 会扫描 `core/v5` 残留旧名（应已重命名为 `core/memory_v5`）、已删文件 `think.py`、已删端口 `:7870`/`:7871`。

## 1. 品牌 vs 代码标识符

| 概念 | 品牌写法 | 代码标识符（目录/包/变量） | 说明 |
|------|----------|---------------------------|------|
| 项目 | **Ikaros** | `ikaros`（路径/脚本前缀） | 品牌首字母大写；文件系统用小写 `ikaros-*` |
| ~~桌面宠 / 前端~~ | ~~**N.E.K.O**~~ | ~~`neko`~~ | ⚠️ **已退役 2026-08-18**（`apps/neko/` 已删） |
| 灵魂核心 | V5（版本代号） | `memory_v5`（包）/ `v5`（db + MCP 工具前缀） | 见 §3 |
| 工作引擎 (2026-08-18 起) | DeepSeek Harness | `dsh`（目录 `runtime/dsh/`、变量 `IKAROS_DSH_*`） | 品牌 **dsh**；代码小写 `dsh` |
| ~~旧底座~~ | ~~**Hermes**~~ | ~~`hermes-*`~~ | ⚠️ **已退役 2026-08-18**（gateway/bridge/dashboard 全删） |

## 2. 大小写铁律

1. **代码标识符一律小写 + 下划线**：`memory_v5`、`dsh`、`ikaros`。
   - 目录：`core/memory_v5/`、`core/ikaros-dsh/`。
   - Python 包：`import memory_v5`。（旧 `import v5` 已废弃；hermes/neko 已退役。）
2. **品牌用规定大小写**：
   - 项目名写 **Ikaros**（文档/UI 文案）。
   - 工作引擎写 **dsh**（文档/UI 文案）。
3. **环境变量用 UPPER_SNAKE**：`IKAROS_ROOT`、`IKAROS_MEMORY`、`IKAROS_PYTHON`、`IKAROS_DSH_*`。

## 3. V5 / memory_v5 / v5 的精确区分（极易混淆）

- **`memory_v5`** = Python **包名**（旧 `v5`）。`import memory_v5`；`sys.path` 须含 `E:/Ikaros/core`。
- **`v5`** 作为 **数据库文件名** = `v5.db`，**保持不变**（对外契约，勿重命名）。
- **`v5_*`** = **48 个 MCP 工具前缀**（V5.5 起，含 skill 系列），如 `v5_search`、`v5_store`，**保持不变**（对外契约，勿重命名）。
- 数据目录：`core/memory_v5/data/v5/`（目录里的 `v5` 是历史数据布局，随 db 契约保留）。

> 简记：**包是新名 `memory_v5`，db 与工具前缀是旧名 `v5`**——这是刻意保留的兼容性契约。

## 4. ~~桌面壳 vs 前端服务~~ — ⚠️ 已退役 2026-08-18

`core/control-panel/`（Electron 桌面壳）与 `apps/neko/`（前端服务）均已退役删除。表现层 = dsh web :3080 + 对话树 :48920。

## 5. 常见错误写法（应避免）

> 本表为「错误 → 正确」映射示例（2026-07-26 重命名后）：左列为过时写法，右列为当前写法。

| 错误 | 正确 |
|------|------|
| `import v5` | `import memory_v5` |
| `core/v5/` | `core/memory_v5/` |
| `:8642` Hermes API 网关 | **已退役 (2026-08-18)**，勿引用；工作引擎 = dsh `:3080` |
| `:7870` `:7871` 语音桥 | 已删除，勿引用 |
| `:8080` 本地 LLM | **已退役 (2026-08-18)**，勿引用；LLM = 云端 DeepSeek |
| `:9100` 控制面板 | **已退役 (2026-08-18)**，勿引用 |
| 环境变量 `ikaros_root` | `IKAROS_ROOT` |
