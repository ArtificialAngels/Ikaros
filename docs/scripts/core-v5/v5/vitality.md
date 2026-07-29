# vitality.py

> 源文件：`Ikaros-memory/v5/vitality.py`

v5.vitality -- 隐喻身体状态 (Bio-mimetic Resource Model)

设计原理:
  人的精力不是恒定的 -- 运行久了疲惫, 休息后恢复, 高强度活动消耗更多。
  将系统运行时指标映射为"伊卡洛斯的身体状况", 注入情感和思考。

算法: Bio-mimetic Resource Model
  - vitality ∈ [0, 1], 1=精力充沛, 0=完全耗尽
  - 消耗: 对话密度(高频=高消耗), 运行时长, RAM/CPU 压力
  - 恢复: 空闲期自然恢复 (logistic 增长, 不是线性)
  - 昼夜节律: 深夜 22:00-06:00 天然 vitality 偏低 (circadian dip)

用法:
    from v5.vitality import Vitality, vitality_prompt
    v = Vitality.load()
    v = v.tick()          # 每次调用更新 (对话 / cron 均可)
    prompt = v.to_prompt() # "精力充沛" / "有点累了" / "需要休息"
