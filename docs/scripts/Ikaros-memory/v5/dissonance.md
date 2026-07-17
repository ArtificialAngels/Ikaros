# dissonance.py

> 源文件：`Ikaros-memory/v5/dissonance.py`

v5.dissonance -- 认知失调检测 (Festinger, 1957)

设计原理: Cognitive Dissonance Theory
  当新信息与已有信念/记忆冲突时, 人会感到不适 (失调)。
  这种不适驱使人重新审视自己的记忆或更新信念。

算法: Semantic Conflict Detection via Vector Similarity + LLM NLI
  1. 新记忆 store 后 → fused_search 找语义相近的旧记忆 (top_k=5)
  2. 对每对 (new, old) 跑 LLM 做 NLI (Natural Language Inference):
     - "新信息是否与旧记忆矛盾?"
     - entailment → 一致 (不触发)
     - contradiction → 矛盾! 写 dissonance 记忆
     - neutral → 无关
  3. 检测到矛盾时:
     - 写 type=dissonance 记忆 (高 weight 0.8)
     - 影响 PAD: pleasure 微降 + arousal 微升 (惊讶困惑)
     - 为未来的 reflect 操作提供素材

用法:
    from v5.dissonance import detect_dissonance
    result = detect_dissonance(new_content, new_type)
    if result["conflicts"]:
        print(f"发现与 {len(result['conflicts'])} 条旧记忆矛盾")
