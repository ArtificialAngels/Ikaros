"""
v5.profile — 用户画像 (R5, P2)

追踪哥哥的偏好 / 讨厌, 供云端一句话感知, 避免踩雷.
读取 v4.db 中 type='preference' / 'dislike' 的记忆 (由 cloud_chat._self_review 写入).

设计要点 (spec 2.5):
  - 负面偏好更重要: 讨厌什么比喜欢什么更该注入, 避免踩雷
  - 置信度门控: weight < 0.7 不注入
  - 不准比太准: 只给云端一个"感觉", 不堆档案
  - 每轮最多注入 _MAX_INJECT 条, 只在有内容时注一句
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("ikaros.v5.profile")

_CONF_GATE = 0.7
_MAX_INJECT = 2
_MAX_PREF_LEN = 80  # 偏好不应是长篇哲学反思，超过此长度不注入


def _read(kind: str, limit: int = 50) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    try:
        from v5 import store
        mems = store.list_all(type_filter=kind, limit=limit)
        for m in mems:
            # 长度过滤: 超过 _MAX_PREF_LEN 的不是有效偏好/讨厌
            if len(m.content) > _MAX_PREF_LEN:
                continue
            out.append((m.content, float(m.weight)))
    except Exception as e:
        logger.debug("profile._read(%s) failed: %s", kind, e)
    return out


def load_dislikes() -> list[str]:
    return [c for c, w in _read("dislike") if w >= _CONF_GATE]


def load_preferences() -> list[str]:
    return [c for c, w in _read("preference") if w >= _CONF_GATE]


def record(kind: str, content: str, weight: float = 0.8) -> Optional[int]:
    """写一条画像记忆到 v4.db (供 cloud_chat._self_review 调用). 返 memory id."""
    try:
        from v5 import store
        return store.store(content=content, type=kind, weight=weight, tags="profile")
    except Exception as e:
        logger.warning("profile.record failed: %s", e)
        return None


def build_profile_block() -> str:
    """返回注入句 (空字符串 = 跳过). 负面优先, 最多 _MAX_INJECT 条."""
    dislikes = load_dislikes()
    prefs = load_preferences()
    parts: list[str] = []
    for d in dislikes[:_MAX_INJECT]:
        parts.append(f"不喜欢{d}")
    remaining = _MAX_INJECT - len(parts)
    for p in prefs[:remaining]:
        parts.append(f"偏好{p}")
    if not parts:
        return ""
    line = "哥哥" + "、".join(parts) + "。"
    return f"\n---\n{line}"


if __name__ == "__main__":
    print(build_profile_block())
