# store.py

> 源文件：`Ikaros-memory/v5/store.py`

v5.store — V5 记忆存储层 (代码于 2026-07-12 由 v4/ 迁入 v5/, 数据目录仍为 data/v4/)

设计目标:
  - API 与 V3 store() 兼容 (Phase 4 切换期平滑过渡)
  - 显式错误: 不像 V3 内部 try/except 吞错
  - 写回缓存 (复用 V3 思路, 但 V4 子目录)
  - 短/长期 memory flag (与 V3 一致)

V3 参考: ikaros-memory-v3.py 第 1-30 行 API 表面

## 内联注释摘录

# V4 状态文件: Ikaros-memory/data/v4/v4.db (与 V3 v3.db 并列)
# 注意: scheduler.py 用的是 data/v4/reflect_state.json, 这里用 v4.db
# 两者都在 data/v4/ 下, 但文件名不同 (一个 db, 一个 json)
# V4 db 位置: Ikaros-memory/data/v4/v4.db (与 V3 v3.db 并列, 不污染)
# 注意: __file__ = Ikaros-memory/v4/store.py
#   parent = Ikaros-memory/v4/
#   parent.parent = Ikaros-memory/  ← 这就是 V4_ROOT
#   V4_ROOT / "data" / "v4" = Ikaros-memory/data/v4/

# V4 改进: _conn 不再是模块级全局, 改用 thread-local.
# 原因: 模块级 _conn 在测试间会污染 (前一个测试关不掉).
# V3 模块级 _conn 是历史包袱, V4 干净.

