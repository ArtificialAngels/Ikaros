# bin/archive/ — 归档的 bin 工具

存放已被主路径替代、或对接已退役组件的 bin/ 工具。**不在启动器/启动脚本引用**，**不参与 AGENTS.md 红线校验**，**未在 docs/ARCHITECTURE.md 表格列出**。

## utility-2024/

2026-09-05 归档：3 个 0 外部引用的 Python 工具（每个文件头 self-described "Additive scaffolding"），保留 git history 追溯，**主路径不再调用**。

| 文件 | 行数 | 原用途 | 归档原因 |
|------|------|--------|---------|
| `proc.py` | 169 | Windows 进程管理（list python/node、kill by port/keyword） | 0 引用；启动器已自带 `ikaros ps` / `ikaros dsh stop` 等价能力 |
| `secret-scan.py` | 137 | 仓库密钥扫描（pre-commit 可挂） | 0 引用；`docs/lint.py` 已覆盖文档漂移检查，秘密扫描未在 pre-commit 配置 |
| `wb.py` | 128 | **WorkBuddy / CodeBuddy CLI 封装**（让 Ikaros / 子代理直接驱动 WorkBuddy） | 0 引用；**WorkBuddy 底座 2026-07-15 已整体退役删除**（AGENTS.md 段注），对接的服务不存在 |

## 恢复方式

如确需使用，恢复单文件即可（**主路径不会自动调用**）：

```bash
# 例: 恢复 proc.py
cp bin/archive/utility-2024/proc.py bin/proc.py
# 即可调用: python bin/proc.py ps
```
