# init.ps1 — Ikaros Environment 单入口（PowerShell）

> 源文件：`Ikaros-environment/init.ps1`
> 作用：任意 ps1 脚本第一行 dot-source 本文件，自动获得 `$env:IKAROS_*` 与 PATH/PYTHONPATH 等效层。
> 用法：`. "E:\Ikaros\core\env\init.ps1"`

## 实现要点

- **`return` 在 script-scope 是 no-op**（只会 break 当前语句）。因此把整段逻辑包进
  `function Invoke-IkarosInit { ... }`，`return` 才能真正从外层调用 short-circuit。
- 用 `trap { ...; return }` 捕获异常，打印红色 `[init.ps1 FAIL]`，并安全返回。
- 用 `if (-not $env:IKAROS_INIT_DONE) { Invoke-IkarosInit }` 守卫，**幂等**：只初始化一次。

## 三个步骤

1. **检测 IKAROS_ROOT**
   - 先尝试 `scripts\detect-root.ps1` 输出；
   - 失败回退：脚本位置 `/..` 推导；
   - 仍失败打印 `[init.ps1 FAIL] IKAROS_ROOT not detected` 并返回。
2. **dot-source `ikaros-env.ps1`**（核心 11 步，见 `ikaros-env-ps1.md`）。
   - 失败打印 `[init.ps1 FAIL] ikaros-env.ps1 failed: ...` 并返回。
3. **自检标记**：置 `$env:IKAROS_INIT_DONE='1'`，并回显
   `IKAROS_ROOT` / `python` / `node` 三行 `[Ikaros init.ps1 OK]`。

## 关联文档

- `ikaros-env-ps1.md`：核心 11 步路径配置
- `scripts/detect-root.md`：根目录 5 优先级探测
