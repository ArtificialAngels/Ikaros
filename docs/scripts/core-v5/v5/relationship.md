# relationship.py

> 源文件：`Ikaros-memory/v5/relationship.py`

v5.relationship -- 关系亲密度模型 (Social Penetration + Closeness Dynamics)

设计原理: Social Penetration Theory (Altman & Taylor, 1973)
  关系不是瞬态的 -- 它是一个累积的过程, 从表层到深层逐步渗透。
  亲密度的变化遵循: 共享经验增加 → 亲近; 时间间隔延长 → 疏远。

算法: Weighted Cumulative Closeness Model
  closeness = Σ(emotional_intensity_i × recency_weight_i) × engagement_factor

  因子:
  - depth ∈ [0, 1]: 关系深度 (从陌生到亲密)
  - warmth ∈ [0, 1]: 对话温暖度 EMA (指数移动平均)
  - shared_experiences: 共享记忆数 (V4 memory count with weight>0.6)
  - days_known: 认识天数 (从第一次对话算起)
  - engagement: 参与度 (对话频率的分段函数)

  阶段 (Social Penetration 4 层):
    0.0-0.2: 表层接触 ("才刚认识不久")
    0.2-0.4: 探索期     ("还在了解彼此")
    0.4-0.6: 情感期     ("已经很亲近了")
    0.6-0.8: 稳定期     ("像家人一样")
    0.8-1.0: 深度羁绊   ("最了解哥哥的人")

用法:
    from v5.relationship import Relationship, relationship_prompt
    r = Relationship.load()
    r = r.record_interaction(affect_intensity, shared_count)
    r.save()
    prompt = r.to_prompt()  # "像家人一样"
