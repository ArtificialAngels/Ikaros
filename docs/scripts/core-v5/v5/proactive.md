# proactive.py

> 源文件：`Ikaros-memory/v5/proactive.py`

v5.proactive — 自主搭话决策引擎 (Self-Determined Speech V5.1)

设计原则:
  - 无固定计时器 — 由她自主判断是否说话
  - 条件门控: idle时间/无任务/好奇度/情感状态/活动监测
  - 空闲优化: 哥哥离开时思考休息, 监测继续
  - 借鉴 Neuro: Signals 共享状态 + Prompter 门控循环模式

决策逻辑 (每 5min metacog 节拍调用):
  1. 检查上次对话距今时间 (> 5min)
  2. 检查无待执行任务
  3. 检查好奇度 (> 0.4)
  4. 检查 PAD arousal (> -0.3, 不能太困)
  5. 检查哥哥不在 "away" (> 5min idle)
  6. 检查孤独感 (> 0.2, 有一定倾诉欲)
  7. 条件全满足 → 调 surface_utterance 获取哲思 → 主动说话
