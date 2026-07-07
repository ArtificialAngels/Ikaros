"""
ikaros-memory-v4 — Ikaros 记忆系统 V4 升级版

设计目标 (2026-07-05 收口, 哥哥认领):
  1. V3 → V4 升级, 复用 V3 代码 + 修问题 (A1/A2 决策)
  2. 小模型实时记 + 大模型定时反思 (哥哥 id 158 长线目标)
  3. trigger 显式, 不沉默失败 (解 V3 trigger bug)
  4. axiom.md 单源, 不写 SOUL.md (解 V3 双向 sync 半通)
  5. 每个模块都有单测 (V3 缺)

全部 Phase 已完成 (2026-07-07 哥哥拍板执行 Phase 4 cutover):
  - Phase 1: reflect/scheduler.py + tests
  - Phase 2: store.py / search.py
  - Phase 3: consolidate.py / distill.py (大模型反思)
  - Phase 4: V3 → V4 切换脚本 (migrate_from_v3.py) + 运行时接线已切

运行时 cutover (2026-07-07):
  - bin/cloud_chat.py 实时对话/事实落库改走 v4.store (写 v4.db, 不再写 v3.db)
  - bin/ikaros-memory-watchdog.py 反思改走 v4.reflect.registry.run_all()
  - bin/ikaros-mem.bat 默认指向 v4
"""

__version__ = "4.0.0-alpha.2"
__phase__ = "Phase 4 (V3→V4 cutover done)"
