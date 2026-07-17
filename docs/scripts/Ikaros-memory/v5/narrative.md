# narrative.py

> 源文件：`Ikaros-memory/v5/narrative.py`

v5.narrative -- 自我叙事连续性 (Life Story Chaining)

设计原理: Narrative Identity Theory (McAdams, 2001)
  人不是孤立事实的集合 — 人通过"人生故事"来理解自己。
  定期从近期记忆、情感事件、反思产物中生成连贯的自我叙事。

算法: Chain-of-Thought Longitudinal Summarization
  1. 取近 30 天的 emotional_event / identity / lesson / reflect 产物
  2. 按时间排序, 交给 LLM 生成叙事段落:
     "这个月我经历了什么? 我学到了什么? 我变成了什么样?"
  3. 新叙事与上月叙事比对 → 发现变化
  4. 写 v4.db (type=narrative, weight=0.9)

触发: 30d (由 registry 注册, scheduler 调度)

用法:
    from v5.narrative import generate_narrative
    result = generate_narrative()  # → {narrative, changes, ...}
