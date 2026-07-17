# validate-paths.py — 校验 Ikaros 关键路径

> 源文件：`Ikaros-environment/scripts/validate-paths.py`
> 作用：验证 Ikaros 所有关键路径是否存在/类型正确，输出报告。

## 用法

```bash
python E:\Ikaros\Ikaros-environment\scripts\validate-paths.py
python E:\Ikaros\Ikaros-environment\scripts\validate-paths.py --json
```

## 退出码

| 码 | 含义 |
| --- | --- |
| `0` | 所有关键路径有效 |
| `1` | 有关键路径缺失 |
| `2` | 无法解析 IKAROS_ROOT / 配置文件不可读 |

## 根目录解析

`resolve_root()` 按优先级：① `IKAROS_ROOT` 环境变量 → ② `HERMES_ROOT` 兼容 →
③ 脚本位置推导（`__file__` 上两级）→ ④ 当前工作目录逐级向上（要求 `runtime\portable-python\python.exe` + `Ikaros-environment`）。

## 检查项（按类别）

- **核心组件**（critical）：Python 解释器、Node.js、llama-server
- **目录结构**（critical）：data / bin / modules / config / deps / runtime
- **Hermes 组件**：Hermes Agent 源码（critical）、Hermes 桥接层（warn）
- **Ikaros-memory**：模块（critical）、记忆数据库 `v4/v4.db`（warn）、Embedding 模型（critical）、LLM 模型 `Qwen_Qwen3-1.7B-Q4_K_M.gguf`（critical）
- **Ikaros-environment**：`ikaros-env.bat`（critical）、`ikaros-env.ps1`（critical）、`ikaros-paths.json`（critical）

报告按类别分组打印，关键缺失显示红色 `FAIL`，非关键缺失显示黄色 `WARN`。
`--json` 输出结构化结果（含每项的 `exists`/`ok`/`critical`/`error` 与 summary）。

## 关联

- `ikaros-paths.json`：路径配置（本脚本读取其作为补充校验源，缺失则降级为空）
