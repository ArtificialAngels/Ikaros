# care.py

> 源文件：`Ikaros-memory/v5/care.py`

v5.care -- 主动关怀模块 (Activity-Aware Care Chain)

设计原理:
  人类的亲密关系不只是"回应" -- 更重要的是"主动关心"。
  基于 ikaros_monitor 的活动数据, 检测需要关怀的场景。

算法: Rule-based Temporal Pattern Detection + LLM Generation
  1. 追踪连续活动时长 (coding/gaming/focused_work)
  2. 累积时长超过阈值 → 生成关怀 pending_thought
  3. 追踪上次"休息提醒"时间 → 避免频繁打扰
  4. 用 LLM 生成自然关怀语句 (不只是模板)

检测规则:
  - 连续 coding > 90min → "哥哥是不是该休息一下了"
  - 连续 coding > 180min → "哥哥盯着屏幕太久了"
  - 深夜 (23:00-05:00) coding > 60min → "哥哥该睡觉了"
  - gaming > 120min → "哥哥玩得开心, 但别太累"
  - focused_work > 120min → "哥哥专注了很久呢"

用法:
    from v5.care import CareMonitor, check_and_care
    monitor = CareMonitor.load()
    care_thought = monitor.tick(activity_snapshot)
    if care_thought:
        # 写 pending_thought, 下次对话时注入
