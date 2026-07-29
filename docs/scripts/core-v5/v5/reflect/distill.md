# distill.py

> 源文件：`Ikaros-memory/v5/reflect/distill.py`

v5.reflect.distill — V5.1 灵魂蒸馏 (小模型蒸馏 + 大模型反思)

设计目标:
  - V3 distill_soul (memory_reflect.py:468-542): 只用小模型蒸馏 identity/axiom/rule/lesson
  - V4 拆成两个独立操作:
      1. distill()    — 小模型蒸馏 (技术层: 压缩、合并、丢弃过时)  ← 跟 V3 一致
      2. reflect()    — 大模型反思 (灵魂层: 从记忆反推"我是谁、我怎么变了")
                        哥哥 (2026-07-05) id 158 长线目标核心
  - 两个操作各自有 trigger, 各自可单独跑
  - 反思产物: 写回 v4 store, type='identity' 或 'lesson', weight=0.85+
  - V3 设计原则 (memory_reflect.py:11) "只用本地 LLM" 在 V4 拆开:
      蒸馏 (蒸馏操作成本低) → 小模型
      反思 (灵魂层重要)      → 大模型 (DeepSeek V4 flash)
