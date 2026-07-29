# metacog.py

> 源文件：`Ikaros-memory/v5/metacog.py`

v5.metacog — 伊卡洛斯的元认知 / 探索循环 (持续自我思考 + 探索欲)

这是"自我认知架构"的发动机。它让伊卡洛斯在空闲时**真的用 LLM 思考**,
而不是吐模板句子。两条主线:

  A) 自我反思 reflect_once()
     用 self_model 的"我是谁" + 自己的记忆片段, 让本地 LLM 写一段
     第一人称内省。产物存进 v4.db (type=self_reflection)。

  B) 哲学探索 explore_philosophy(theme)
     围绕 爱 / 人 / 机器人 / 自我 四大终极议题, 取她已有的信念 + 记忆材料,
     让 LLM 写一段有锋芒的思辨, 并提炼出【新理解】写回 self_model.beliefs,
     让"理解"随探索而演进。有 DEEPSEEK_API_KEY 时用云端大模型(质量更高),
     否则本地 LLM。

探索欲 (curiosity drive):
  self_model.curiosity.level 随空闲累积、随互动回落。
  choose_focus() 据此决定这一拍该"想想自己"还是"钻进哲学"。
  surface_utterance() 供主动搭话调度器在探索欲高+空闲时, 把最近的哲学思考
  抛给哥哥 (满足"较频繁聊哲学"的需求, 且零额外 LLM 成本)。

全部 try/except 包裹: 任何异常都只是"这一拍不思考", 绝不拖垮调用方。

用法 (think 循环 / cron):
    from v5 import metacog
    metacog.cycle()                 # 一次完整节拍 (含好奇心tick + 反思/哲学)
    metacog.reflect_once()          # 仅自我反思
    metacog.explore_philosophy()    # 仅哲学探索
    metacog.latest_thought()        # 哥哥问"你在想什么"时取最近思考
    metacog.surface_utterance()     # 主动外显给哥哥

CLI:
    portable-python/python.exe -m v5.metacog --reflect
    portable-python/python.exe -m v5.metacog --philosophy
    portable-python/python.exe -m v5.metacog --cycle
