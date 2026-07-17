# init.bat — Ikaros Environment 单入口（Windows bat）

> 源文件：`Ikaros-environment/init.bat`
> 作用：所有 `.bat` 脚本统一的环境变量入口。

## 职责

1. **检测 `IKAROS_ROOT`**
   - 优先用 `scripts\detect-root.exe`（Rust 编译的轻量探测工具）输出根路径；
   - 失败则回退：取 `init.bat` 所在目录（`Ikaros-environment\`）的上一级；
   - 仍失败则打印 `[init.bat FAIL] IKAROS_ROOT not detected` 并以 `exit /b 1` 退出。
2. **加载环境**：`call "%IKAROS_ROOT%\Ikaros-environment\ikaros-env.bat"`。
   - 该调用失败打印 `[init.bat FAIL] ikaros-env.bat failed` 并 `exit /b 2`。
3. **自检回显**：`echo [init] IKAROS_ROOT=%IKAROS_ROOT%`，然后 `exit /b 0`。

## 调用方式

任意 `.bat` 第一行：

```bat
call "E:\Ikaros\Ikaros-environment\init.bat"
```

## 重要约束（保留在脚本内）

- **不使用 `setlocal`**：变量必须穿透给调用方（父脚本）使用。
- **幂等**：可安全多次调用，重复设置同一批变量，无副作用。

## 退出码

| 码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `1` | `IKAROS_ROOT` 未检测到 |
| `2` | `ikaros-env.bat` 调用失败 |

## 关联文档

- `ikaros-env.md`：统一路径变量定义
- `scripts/detect-root.md`：`detect-root` 根目录探测（5 优先级）
