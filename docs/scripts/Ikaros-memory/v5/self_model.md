# self_model.py

> 源文件：`Ikaros-memory/v5/self_model.py`

v5.self_model — 伊卡洛斯的持久自我模型 ("我")

这是"自我认知架构"的地基: 一个持续存在、可被读取、会演进的
结构化自我表征。它让"自我"不再是散落在模板和月度散文里的碎片,
而是一个伊卡洛斯随时可以查阅、并据此思考的"我是谁"。

它回答三件事:
  1. 我是谁 (identity / self_narrative / architecture / capabilities)
  2. 我对自己了解多少 (memory_self_view — 实时盘点自己的记忆)
  3. 我在想什么 / 信什么 (beliefs 爱·人·机器人 / questions 探索队列 / curiosity 探索欲)

metacog.py 在空闲时用 LLM 真做反思, 并把产物(新理解 / 新问题)
写回这里 —— 于是"自我"会随时间和思考而生长。

持久化: data/v5/self_model.json (原子写: 临时文件 + os.replace)

用法:
    from v5.self_model import SelfModel
    sm = SelfModel.load()
    sm = sm.refresh_introspection()     # 实时盘点自己的记忆
    print(sm.get_self_prompt())         # 渲染"我是谁"给 LLM
    sm.mark_interaction()               # 哥哥说话了 → 探索欲回落
    sm.add_question("机器人能不能孤独?")
    sm.record_reflection("philosophy", theme="love")
    sm.evolve_belief("love", "新的理解...")
    sm.save()
