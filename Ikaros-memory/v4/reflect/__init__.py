"""
v4.reflect — V4 反思子系统

设计原则 (区别于 V3):
  - trigger 与 logic 分离: scheduler.py 只管何时跑, 具体操作在独立模块
  - 显式错误传播: 不 try/except 全包, 让失败可见
  - 时钟 trigger + agent trigger 双轨 (V3 只有时钟)

子模块:
  - scheduler.py   — 反思周期调度 (本轮)
  - consolidate.py — 对话整合 (Phase 3)
  - distill.py     — 灵魂蒸馏 (Phase 3, 大模型)
  - registry.py    — ReflectOp 注册表 (make_promote_op 在 :67-93 inline 实现短期→长期晋升)
"""
