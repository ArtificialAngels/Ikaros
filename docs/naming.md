# Ikaros 命名字典 (Naming Dictionary)

> 项目历史上出现过多种大小写 / 缩写混用。本文档统一命名，避免文档与代码漂移。
> 检查实现：`python docs/lint.py` 会扫描 `core/v5` 残留旧名（应已重命名为 `core/memory_v5`）、已删文件 `think.py`、已删端口 `:7870`/`:7871`。（`:8642` Hermes API 网关已重新启用，见 §5，不再列入删除端口。）

## 1. 品牌 vs 代码标识符

| 概念 | 品牌写法 | 代码标识符（目录/包/变量） | 说明 |
|------|----------|---------------------------|------|
| 项目 | **Ikaros** | `ikaros`（路径/脚本前缀） | 品牌首字母大写；文件系统用小写 `ikaros-*` |
| 桌面宠 / 前端 | **N.E.K.O**（品牌，带点） | `neko`（目录 `core/neko/`、包、变量） | 品牌 **N.E.K.O**；代码一律小写 `neko` |
| 灵魂核心 | V5（版本代号） | `memory_v5`（包）/ `v5`（db + MCP 工具前缀） | 见 §3 |
| 基础设施/Agent 框架 | Hermes | `hermes`（目录 `core/hermes/`、变量、provider） | 品牌 **Hermes**；代码小写 `hermes` |

## 2. 大小写铁律

1. **代码标识符一律小写 + 下划线**：`neko`、`memory_v5`、`hermes`、`ikaros`。
   - 目录：`core/neko/`、`core/memory_v5/`、`core/hermes/`、`core/control-panel/`。
   - Python 包：`import memory_v5`、`import hermes`。（旧 `import v5` 已废弃。）
2. **品牌用规定大小写**：
   - 项目名写 **Ikaros**（文档/UI 文案）。
   - 前端品牌写 **N.E.K.O**（带点，文档/UI 文案）。
   - 基础设施品牌写 **Hermes**（文档/UI 文案）。
3. **环境变量用 UPPER_SNAKE**：`IKAROS_ROOT`、`IKAROS_MEMORY`、`IKAROS_PYTHON`、`HERMES_ROOT`（兼容旧变量）。

## 3. V5 / memory_v5 / v5 的精确区分（极易混淆）

- **`memory_v5`** = Python **包名**（旧 `v5`）。`import memory_v5`；`sys.path` 须含 `E:/Ikaros/core`。
- **`v5`** 作为 **数据库文件名** = `v5.db`，**保持不变**（对外契约，勿重命名）。
- **`v5_*`** = **40 个 MCP 工具前缀**，如 `v5_search`、`v5_store`，**保持不变**（对外契约，勿重命名）。
- 数据目录：`core/memory_v5/data/v5/`（目录里的 `v5` 是历史数据布局，随 db 契约保留）。

> 简记：**包是新名 `memory_v5`，db 与工具前缀是旧名 `v5`**——这是刻意保留的兼容性契约。

## 4. 桌面壳 vs 前端服务

- `core/control-panel/` = **Electron 桌面壳**（Desktop Shell），拉起面板 `:9100` 与各组件。
- `core/neko/` = **前端服务**（Frontend Service），FastAPI + React；其可执行文件 **`N.E.K.O.exe`** 即 neko 壳。
- 二者职责不同，文档中勿混用"桌面壳 / 前端"。

## 5. 常见错误写法（应避免）

> 本表为「错误 → 正确」映射示例（2026-07-26 重命名后）：左列为过时写法，右列为当前写法。

| 错误 | 正确 |
|------|------|
| `import v5` | `import memory_v5` |
| `core/v5/` | `core/memory_v5/` |
| `:8642` Hermes API 网关 | 在用（`bin/hermes-api-server.py`，dashboard + chat-tree 复用），勿删除 |
| `:7870` `:7871` 语音桥 | 已删除，勿引用 |
| 文档写 "Neko" 当品牌 | 品牌写 **N.E.K.O**，代码写 `neko` |
| 环境变量 `ikaros_root` | `IKAROS_ROOT` |
