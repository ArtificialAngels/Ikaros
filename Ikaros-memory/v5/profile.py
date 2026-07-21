# 详细说明见 docs/scripts/Ikaros-memory/v5/profile.md
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
    """写一条画像记忆到 v5.db (供 cloud_chat._self_review 调用). 返 memory id."""
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
