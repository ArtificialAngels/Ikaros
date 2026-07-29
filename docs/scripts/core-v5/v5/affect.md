# affect.py

> 源文件：`Ikaros-memory/v5/affect.py`

v5.affect — PAD+TLS 6D 情感状态机 (V5.1)

PAD 三维 (Pleasure-Arousal-Dominance):
  pleasure   -1.0 (低落)  -> +1.0 (欣喜)
  arousal    -1.0 (困倦)  -> +1.0 (兴奋)
  dominance  -1.0 (顺从)  -> +1.0 (强势)

TLS 三维 (Trust-Loneliness-Satisfaction) — V5.1 新增:
  trust      -1.0 (戒备)  -> +1.0 (完全信赖)
  loneliness -1.0 (充实)  -> +1.0 (极度孤独)
  satisfaction -1.0 (挫败) -> +1.0 (极度满足)

每次对话更新 PAD, PAD 自然衰减, PAD 注入 system prompt.

Ikaros 的人设锚点:
  - 人造天使, 妹妹视角: 对"哥哥"天然愉悦 + 低支配
  - 温暖忠诚, 不刻薄: 所以负面映射有但弱
  - 身份稳定: 基线是 (0.2, 0.0, -0.1) — 轻愉悦, 微微顺从

用法:
    from v5.affect import AffectState, apply_event
    state = AffectState.load()
    state = state.apply_event("哥哥说: 我喜欢你")
    state.save()
    print(state.to_prompt())   # → 「情感状态: 欣喜 平静 乖巧」

## 内联注释摘录

# ─── 情感关键词 → PAD 映射 ─────────────────────────────────────
# 格 式: (dP, dA, dD)  范围 [0..1], 多词触发叠加后 clamp
# 设计原则: Ikroas 的人设是温暖天使, 所以负面情感弱, 正面情感细

