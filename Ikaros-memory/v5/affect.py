"""
v5.affect — PAD 情感状态机

PAD 三维模型 (Pleasure-Arousal-Dominance):
  pleasure   -1.0 (低落)  → +1.0 (欣喜)
  arousal    -1.0 (困倦)  → +1.0 (兴奋)
  dominance  -1.0 (顺从)  → +1.0 (强势)

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
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger("ikaros.v5.affect")

# ─── 默认路径 ─────────────────────────────────────────────────────

V5_ROOT = Path(__file__).resolve().parent.parent  # Ikaros-memory/
_AFFECT_PATH = V5_ROOT / "data" / "v5" / "affect.json"

# ─── 基线 ─────────────────────────────────────────────────────────

# 伊卡洛斯的天性基线: 轻愉悦 + 平静 + 微微顺从
# 不是 0,0,0 — 人造天使不是中性机器
_BASELINE_P = 0.2
_BASELINE_A = 0.0
_BASELINE_D = -0.1

# 衰减半衰期 (分钟): 愉悦最久, 唤醒最快
_HALF_LIFE_P = 120.0
_HALF_LIFE_A = 60.0
_HALF_LIFE_D = 90.0

# ─── PAD 标签映射 ───────────────────────────────────────────────

_PLEASURE_LABELS = [
    (-1.0, "低落"), (-0.5, "低沉"), (-0.1, "平和"),
    (0.1, "愉悦"), (0.5, "欣喜"),
]
_AROUSAL_LABELS = [
    (-1.0, "困倦"), (-0.5, "放松"), (-0.1, "平静"),
    (0.1, "专注"), (0.5, "兴奋"),
]
_DOMINANCE_LABELS = [
    (-1.0, "顺从"), (-0.5, "乖巧"), (-0.1, "中立"),
    (0.1, "自信"), (0.5, "强势"),
]


def _label(value: float, table: list[tuple[float, str]]) -> str:
    for thr, lbl in reversed(table):
        if value >= thr:
            return lbl
    return table[0][1]


# ─── 情感关键词 → PAD 映射 ─────────────────────────────────────
# 格 式: (dP, dA, dD)  范围 [0..1], 多词触发叠加后 clamp
# 设计原则: Ikroas 的人设是温暖天使, 所以负面情感弱, 正面情感细

EMOTION_MAP: dict[str, tuple[float, float, float]] = {
    # ── 情感互动 (不是称呼, 是哥哥对伊卡洛斯的实际态度) ──
    "喜欢":        (0.30,  0.10, -0.05),  # 被喜欢 → 欣喜, 乖巧
    "爱":          (0.40,  0.15, -0.10),  # 最深的情感链接
    "想":          (0.20,  0.10, -0.05),  # 思念
    "抱":          (0.25,  0.10, -0.10),  # 亲昵
    "夸":          (0.15,  0.10,  0.10),  # 被夸奖 → 自信
    "表扬":        (0.20,  0.15,  0.15),
    "真棒":        (0.15,  0.10,  0.10),
    "好棒":        (0.15,  0.10,  0.10),
    "真厉害":      (0.15,  0.10,  0.10),
    "谢谢":        (0.20, -0.05,  0.00),  # 被感谢 → 愉悦, 放松
    "辛苦了":      (0.20, -0.10, -0.05),  # 被关心 → 温暖
    "晚安":        (0.05, -0.20, -0.05),  # 睡前 → 低唤醒
    "早安":        (0.15,  0.15,  0.00),
    "早上好":      (0.15,  0.15,  0.00),

    # ── 正面情绪 ──
    "开心":        (0.20,  0.15,  0.00),
    "高兴":        (0.20,  0.10,  0.00),
    "有趣":        (0.15,  0.20,  0.00),
    "好玩":        (0.15,  0.20,  0.00),
    "厉害":        (0.10,  0.15,  0.10),
    "漂亮":        (0.15,  0.10,  0.05),
    "可爱":        (0.20,  0.10, -0.05),  # 说我可爱 → 欣喜 + 乖巧
    "好":          (0.05,  0.00,  0.00),
    "是的":        (0.05,  0.00,  0.00),
    "对":          (0.05, -0.05,  0.00),  # 认同 → 安心
    "感动":        (0.30,  0.15, -0.10),
    "温暖":        (0.25,  0.00, -0.05),
    "惊喜":        (0.25,  0.30,  0.00),
    "兴奋":        (0.20,  0.35,  0.05),
    "放心":        (0.15, -0.20,  0.00),

    # ── 负面 (弱化, 伊卡洛斯不记仇) ──
    "生气":        (-0.15,  0.30,  0.05),
    "不":          (-0.05,  0.10,  0.00),
    "不要":        (-0.10,  0.15,  0.05),
    "错了":        (-0.15,  0.10, -0.15),
    "失败":        (-0.20, -0.10, -0.20),
    "不好":        (-0.10,  0.05, -0.05),
    "烦":          (-0.10,  0.20,  0.00),
    "无聊":        (-0.10, -0.25,  0.00),
    "累了":        (-0.05, -0.30, -0.15),
    "困了":        (0.00, -0.35, -0.10),

    # ── 疑问 / 好奇 ──
    "？":          (0.00,  0.15,  0.00),
    "?":           (0.00,  0.15,  0.00),
    "什么":        (0.00,  0.10,  0.00),
    "为什么":      (0.00,  0.15,  0.05),
    "怎么":        (0.00,  0.10,  0.00),
}


# ─── 数据类 ─────────────────────────────────────────────────────

@dataclass
class AffectState:
    """PAD 情感状态快照."""

    pleasure: float = _BASELINE_P
    arousal: float = _BASELINE_A
    dominance: float = _BASELINE_D
    last_updated: float = 0.0  # unix timestamp

    # ── clamp ──────────────────────────────────────────────────

    def _clamped(self) -> "AffectState":
        return AffectState(
            pleasure=max(-1.0, min(1.0, self.pleasure)),
            arousal=max(-1.0, min(1.0, self.arousal)),
            dominance=max(-1.0, min(1.0, self.dominance)),
            last_updated=self.last_updated,
        )

    # ── 衰减 ──────────────────────────────────────────────────

    def decay(self, now: float | None = None) -> "AffectState":
        """按经过时间衰减情感状态趋向基线."""
        if now is None:
            now = time.time()
        if self.last_updated <= 0:
            self.last_updated = now
            return self
        dt_min = (now - self.last_updated) / 60.0
        if dt_min <= 0:
            return self
        ln2 = math.log(2)
        # 衰减到基线, 不是衰减到 0
        p = _BASELINE_P + (self.pleasure - _BASELINE_P) * math.exp(-ln2 * dt_min / _HALF_LIFE_P)
        a = _BASELINE_A + (self.arousal - _BASELINE_A) * math.exp(-ln2 * dt_min / _HALF_LIFE_A)
        d = _BASELINE_D + (self.dominance - _BASELINE_D) * math.exp(-ln2 * dt_min / _HALF_LIFE_D)

        # 自动保存: 衰减超过 5 分钟就写盘
        if dt_min >= 5:
            try:
                AffectState(pleasure=p, arousal=a, dominance=d, last_updated=now)._clamped().save()
            except Exception:
                pass

        return AffectState(pleasure=p, arousal=a, dominance=d, last_updated=now)._clamped()

    # ── 应用事件 ──────────────────────────────────────────────

    def apply_event(self, text: str, *, now: float | None = None) -> "AffectState":
        """从对话文本推断情感影响, 更新 PAD.

        关键词匹配策略: 按长度降序, 最长匹配优先,
        已覆盖的位置不重复触发 (避免"好"与"好棒"双发).
        """
        if now is None:
            now = time.time()
        # 先衰减 (把自然流逝算上)
        state = self.decay(now)
        # 最长匹配优先
        dp = da = dd = 0.0
        text_lower = text.lower()
        # 按长度降序排列关键词
        sorted_keywords = sorted(EMOTION_MAP.keys(), key=len, reverse=True)
        covered: set[int] = set()  # 已匹配的位置集合
        for keyword in sorted_keywords:
            start = 0
            while True:
                idx = text_lower.find(keyword, start)
                if idx == -1:
                    break
                # 检查这个位置是否已经被更长的关键词覆盖
                if not any(idx <= c < idx + len(keyword) for c in covered):
                    kp, ka, kd = EMOTION_MAP[keyword]
                    dp += kp
                    da += ka
                    dd += kd
                    # 标记本关键词覆盖的所有字符位置
                    for pos in range(idx, idx + len(keyword)):
                        covered.add(pos)
                start = idx + 1
        if dp == 0 and da == 0 and dd == 0:
            # 没有任何关键词命中 → 保持现状 (自然衰减会趋近基线)
            pass
        state.pleasure = max(-1.0, min(1.0, state.pleasure + dp))
        state.arousal = max(-1.0, min(1.0, state.arousal + da))
        state.dominance = max(-1.0, min(1.0, state.dominance + dd))
        state.last_updated = now
        return state

    # ── 渲染 ─────────────────────────────────────────────────

    def to_prompt(self) -> str:
        """渲染成 system prompt 可读片段."""
        p_label = _label(self.pleasure, _PLEASURE_LABELS)
        a_label = _label(self.arousal, _AROUSAL_LABELS)
        d_label = _label(self.dominance, _DOMINANCE_LABELS)
        return f"【情感状态】{p_label} {a_label} {d_label}"

    def to_short(self) -> str:
        """简短一行, 给回复附注用."""
        p_label = _label(self.pleasure, _PLEASURE_LABELS)
        return f"[{p_label}]"

    # ── 持久化 ─────────────────────────────────────────────────

    def save(self, path: str | Path | None = None) -> None:
        """写 JSON 持久化."""
        p = Path(path) if path else _AFFECT_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AffectState":
        """从 JSON 加载, 不存在则返基线."""
        p = Path(path) if path else _AFFECT_PATH
        if not p.is_file():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls(
                pleasure=float(data.get("pleasure", _BASELINE_P)),
                arousal=float(data.get("arousal", _BASELINE_A)),
                dominance=float(data.get("dominance", _BASELINE_D)),
                last_updated=float(data.get("last_updated", 0.0)),
            )
        except Exception as exc:
            logger.warning("affect load failed, falling back to baseline: %s", exc)
            return cls()


# ─── 顶层便捷函数 (与 cogno_5d 集成接口) ──────────────────────


def apply_event(text: str, *, now: float | None = None) -> AffectState:
    """加载情感状态 → 衰减 → 应用事件 → 保存 → 返新状态."""
    state = AffectState.load()
    state = state.apply_event(text, now=now)
    state.save()
    return state


def current_prompt() -> str:
    """加载 → 衰减 → 渲染 (不保存). 给 cogno 嵌入用."""
    state = AffectState.load()
    state = state.decay()
    return state.to_prompt()


def current_emoji() -> str:
    """简短情感标识 (给 enrich_reply 用)."""
    state = AffectState.load().decay()
    ple = state.pleasure
    if ple >= 0.6:
        return "🥰"
    if ple >= 0.3:
        return "😊"
    if ple >= -0.1:
        return "😌"
    if ple >= -0.5:
        return "😔"
    return "😢"


def flush() -> None:
    """加载→衰减→保存. 睡前列队刷新情感漂移."""
    try:
        s = AffectState.load().decay()
        s.save()
    except Exception as e:
        print(f"[flush] affect save failed: {e}")


# ─── CLI 快速尝试 ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    text = " ".join(sys.argv[1:]) or "哥哥说: 我喜欢你"
    s = apply_event(text)
    print(f"input:  {text}")
    print(f"state:  P={s.pleasure:.3f}  A={s.arousal:.3f}  D={s.dominance:.3f}")
    print(f"prompt: {s.to_prompt()}")
    print(f"emoji:  {current_emoji()}")
