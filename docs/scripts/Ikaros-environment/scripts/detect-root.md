# detect-root.ps1 — 自动探测 IKAROS_ROOT

> 源文件：`Ikaros-environment/scripts/detect-root.ps1`
> 作用：多来源解析 Ikaros 项目根目录，输出根路径或抛错。
> 由 `init.ps1` 调用（`scripts\detect-root.ps1`）。

## 5 优先级（依次尝试）

1. **`IKAROS_ROOT` 环境变量**：存在且存在 `runtime\portable-python\python.exe` 即采用。
2. **`HERMES_ROOT` 环境变量**（旧兼容）：同上校验。
3. **从脚本位置推导**：脚本位于 `Ikaros-environment\scripts\`，根为父级的父级；校验 `runtime\portable-python\python.exe`。
4. **从当前工作目录向上查找**：逐级向上，要求同时存在 `runtime\portable-python\python.exe` + `hermes-agent` + `Ikaros-environment`。
5. **扫描盘符**：遍历所有文件系统盘符，查找 `<盘符>:\Ikaros\runtime\portable-python\python.exe`。

全部失败则 `throw "IKAROS_ROOT not found. Set IKAROS_ROOT env var."`。

## 关联：Rust 版 detect-root.exe

`init.bat` 使用的是编译后的 `Ikaros-environment\scripts\detect-root.exe`
（源码在 `Ikaros-environment\detect-root\src/main.rs`，Rust 实现，逻辑等价：
从 `init.bat` 位置推导根目录）。`.ps1` 版用于 PowerShell 入口链路。

## 关联文档

- `init.md` / `init-ps1.md`：两个入口如何使用本探测
