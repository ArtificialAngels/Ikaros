"""
bridge/intent_router.py — 轻量意图识别模块

Three-layer intent classification for chat completions:
  Layer 1 (this module): keyword/regex rules (millisecond latency)
  Layer 2: (reserved for ML classifier)
  Layer 3: implicit — let the LLM handle naturally, don't infer from response

Thread-safe, stateless, no external dependencies.
"""

import re
from typing import Final


class IntentRouter:
    """Stateless intent classifier using keyword and regex matching.

    Thread-safe — no mutable instance state. All data is module-level.
    """

    # Task-action keywords — if ANY of these appear, it's a task
    _TASK_KEYWORDS: Final[list[str]] = [
        "帮我", "创建", "做一下", "跑", "查一下",
        "计算", "分析", "汇总", "生成", "写一份",
        "安排", "建个", "帮我去",
    ]

    # Single action characters specifically called out as task triggers
    _TASK_CHARS: Final[set[str]] = {"做", "跑"}

    # Chat/greeting / casual topic keywords
    _CHAT_KEYWORDS: Final[list[str]] = [
        "你好", "早安", "晚安", "哈哈", "吃",
        "睡", "笑", "开心", "好吗", "怎么样",
        "你觉得", "想", "喜欢",
    ]

    # Question indicators (Chinese + general interrogatives)
    _QUESTION_RE: Final[re.Pattern] = re.compile(
        r"[？?吗嘛吧呢]|怎么|什么|哪|谁|啥|何|"
        r"是否|有没有|能不能|会不会|要不要"
    )

    @classmethod
    def classify(cls, text: str) -> str:
        """Classify user input as ``"task"``, ``"chat"``, or ``"ambiguous"``.

        Args:
            text: Raw user input string (Chinese or mixed language).

        Returns:
            One of ``"task"``, ``"chat"``, ``"ambiguous"``.
        """
        if not text or not text.strip():
            return "ambiguous"

        stripped = text.strip()

        # ---- Rule 1: Task keywords take priority ----
        for kw in cls._TASK_KEYWORDS:
            if kw in stripped:
                return "task"
        for ch in cls._TASK_CHARS:
            if ch in stripped:
                return "task"

        # ---- Rule 2: Chat/greeting keywords ----
        for kw in cls._CHAT_KEYWORDS:
            if kw in stripped:
                return "chat"

        # ---- Rule 3: Question without action words → chat (LLM decides) ----
        if cls._QUESTION_RE.search(stripped):
            return "chat"

        # ---- Rule 4: No match → ambiguous (Layer 3) ----
        return "ambiguous"
