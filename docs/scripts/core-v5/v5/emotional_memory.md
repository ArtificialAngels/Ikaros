# emotional_memory.py

> 源文件：`Ikaros-memory/v5/emotional_memory.py`

v5.emotional_memory -- 情感因果记忆 (Appraisal-Event Chain)

设计原理: Appraisal Theory of Emotion (Lazarus, 1991)
  情绪不是凭空产生的 -- 每次 PAD 变化背后都有一个"评价事件"。
  人对自己的情绪有叙事性理解: "我难过是因为他说了那句话"。

算法: PAD Delta → Causal Attribution
  1. 每次 apply_event() 后检测 |ΔP|+|ΔA|+|ΔD| > 阈值
  2. 如果超过阈值: 取近段对话上下文 (最近 2 轮 user text)
  3. 用本地 LLM 生成因果句: "因为哥哥说了X, 我感到Y"
  4. 写 v4.db (type=emotional_event, pad_p/a/d 指纹, weight=0.6+intensity*0.3)
  5. 高情感强度的事件自动 promote 为 long_term (跨越情绪记忆)

用法:
    from v5.emotional_memory import maybe_record_emotion
    state = affect.apply_event(text)  # 先更新 PAD
    maybe_record_emotion(old_state, state, user_text, prev_text)

## 内联注释摘录

# ─────────────────────────────────────────────────────────────
# V5.2 R6 情感引擎增强 (spec 2.6)
#
# 在现有 PAD 因果记忆之上新增:
#   1. 情感标签自动打标  label_emotion / maybe_label_emotion
#   2. 情感记忆检索      search_by_emotion
#   3. 情感对比注入      build_emotion_diff_block
#
# 全部本地算 / 本地 LLM, 失败静默降级 (不阻塞主流程).
# ─────────────────────────────────────────────────────────────

