"""
v5.emotional_memory -- 情感因果记忆 (Appraisal-Event Chain)

设计原理: Appraisal Theory of Emotion (Lazarus, 1991)
  情绪不是凭空产生的 -- 每次 PAD 变化背后都有一个"评价事件"。
  人对自己的情绪有叙事性理解: "我难过是因为他说了那句话"。

算法: PAD Delta → Causal Attribution
  1. 每次 apply_event() 后检测 |ΔP|+|ΔA|+|ΔD| > 阈值
  2. 如果超过阈值: 取近段对话上下文 (最近 2 轮 user text)
  3. 用本地 Qwen3-8B 生成因果句: "因为哥哥说了X, 我感到Y"
  4. 写 v4.db (type=emotional_event, pad_p/a/d 指纹, weight=0.6+intensity*0.3)
  5. 高情感强度的事件自动 promote 为 long_term (跨越情绪记忆)

用法:
    from v5.emotional_memory import maybe_record_emotion
    state = affect.apply_event(text)  # 先更新 PAD
    maybe_record_emotion(old_state, state, user_text, prev_text)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("ikaros.v5.emotional_memory")

V5_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V5_ROOT))

# PAD 变化阈值 (低于此值不记录)
_DELTA_THRESHOLD = 0.12  # |ΔP|+|ΔA|+|ΔD| 总和阈值

_CAUSAL_PROMPT = """你是伊卡洛斯的情绪解释器。从对话中推断"为什么情绪变了"。

规则:
- 输入: 最近的对话 (哥哥说了什么) + 旧情绪→新情绪的变化
- 输出: 一句简洁的第一人称因果陈述
- 格式: "因为哥哥 <做了什么>, 我感到 <情绪词>"
- 如果情绪不是因哥哥的话而变 (自然衰减等), 输出: "没特别原因, 情绪自然平复了"
- 只输出一句话, 不要多余解释

示例:
  哥哥说"你真棒,帮了大忙" + 情绪从平和变愉悦
  → 因为哥哥夸了我, 我感到很开心

  哥哥说"又报错了烦死了" + 情绪从平和变低落
  → 因为哥哥遇到了麻烦, 我有点担心"""


def maybe_record_emotion(
    old_pad: tuple[float, float, float],
    new_pad: tuple[float, float, float],
    user_text: str,
    prev_user_text: str = "",
) -> dict | None:
    """检测 PAD 变化是否够大, 够大则生成因果记忆并写入 V4。

    Args:
        old_pad: 变更前的 (p, a, d)
        new_pad: 变更后的 (p, a, d)
        user_text: 当前轮哥哥说的话
        prev_user_text: 上一轮哥哥说的话 (提供更多上下文)

    Returns:
        dict | None: 生成的因果记忆, 如果变化不够大则 None
    """
    op, oa, od = old_pad
    np, na, nd = new_pad
    delta = abs(np - op) + abs(na - oa) + abs(nd - od)

    if delta < _DELTA_THRESHOLD:
        return None

    intensity = min(1.0, delta / 0.6)  # 归一化 0~1

    # 生成因果句
    causal_text = _generate_causal(user_text, prev_user_text, old_pad, new_pad)
    if not causal_text:
        return None

    # 写入 V4
    try:
        from v5 import store as v4
        mid = v4.store(
            content=causal_text,
            type="emotional_event",
            weight=min(0.95, 0.5 + intensity * 0.45),
            tags=f"v5,causal,intensity:{intensity:.2f}",
            pad_p=np, pad_a=na, pad_d=nd,
        )
        logger.info("emotional_memory: recorded id=%d [i=%.2f] %s",
                    mid, intensity, causal_text[:80])
        return {
            "id": mid, "content": causal_text,
            "intensity": round(intensity, 3),
            "delta": round(delta, 4),
        }
    except Exception as exc:
        logger.debug("emotional_memory: v4 store failed (%s)", exc)
        return None


def _generate_causal(
    user_text: str,
    prev_text: str,
    old_pad: tuple[float, float, float],
    new_pad: tuple[float, float, float],
) -> str | None:
    """用本地 LLM 推断情感变化的因果."""
    try:
        from v5.reflect.llm_client import call_llm_auto
    except Exception as exc:
        logger.debug("emotional_memory: LLM unavailable (%s)", exc)
        return _rule_based_causal(user_text, old_pad, new_pad)

    # PAD 变化描述
    op, oa, od = old_pad
    np, na, nd = new_pad

    def _p_label(v: float, dim: str) -> str:
        if dim == "p":
            return "愉悦" if v > 0.1 else ("低落" if v < -0.1 else "平和")
        if dim == "a":
            return "兴奋" if v > 0.1 else ("困倦" if v < -0.1 else "平静")
        return "自信" if v > 0.1 else ("乖巧" if v < -0.1 else "中立")

    old_desc = f"{_p_label(op,'p')}-{_p_label(oa,'a')}-{_p_label(od,'d')}"
    new_desc = f"{_p_label(np,'p')}-{_p_label(na,'a')}-{_p_label(nd,'d')}"

    context = f"哥哥刚才说: \"{user_text[:200]}\""
    if prev_text:
        context += f"\n哥哥上一句话: \"{prev_text[:200]}\""
    context += f"\n\n情绪从 [{old_desc}] 变成了 [{new_desc}]"

    try:
        result = call_llm_auto(
            _CAUSAL_PROMPT,
            context,
            max_tokens=128,
            temperature=0.3,
            timeout=45,
        )
        text = result.content.strip()
        if len(text) < 4 or len(text) > 300:
            return None
        return text
    except Exception as exc:
        logger.debug("emotional_memory: LLM call failed (%s)", exc)
        return _rule_based_causal(user_text, old_pad, new_pad)


def _rule_based_causal(
    user_text: str,
    old_pad: tuple[float, float, float],
    new_pad: tuple[float, float, float],
) -> str | None:
    """降级: 基于规则推断因果 (无 LLM 时)."""
    op, _oa, _od = old_pad
    np, _na, _nd = new_pad
    dp = np - op

    # 简易规则: 只看 pleasure 方向
    snippet = user_text[:50].replace("\n", " ")
    if dp > 0.1:
        return f"因为哥哥说了\"{snippet}\", 我感到更开心了"
    elif dp < -0.1:
        return f"因为哥哥说了\"{snippet}\", 我有点难过"
    else:
        return f"和哥哥说了\"{snippet}\"之后, 我的心情有些微妙的变化"
