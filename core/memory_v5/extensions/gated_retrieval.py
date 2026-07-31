"""
gated_retrieval.py — V5 分层检索门控 (骨架 / EXPERIMENTAL)
===========================================================

问题背景
--------
V5 现状是 `memory_retrieval.retrieve()` 三路融合后 **直接相似注入** top_k=5,
缺 TencentDB Agent Memory 那种 "默认注高层、按需下钻低层" 的严格分层门控 ——
默认 token 效率不如它, 也易把无关旧记忆塞进上下文。

设计(对齐上一轮结论)
---------------------
  高层 (high, 永远注入, 极廉价且高度相关"我是谁"):
    - self_model.get_self_prompt()           (人格/好奇/活力, 来自 self_model.json)
    - 最近 distill/reflect 记忆 (type in distilled/reflect/identity/axiom) top 3
  低层 (low, 条件注入):
    - 仅当 query 实质化(非寒暄/短句) 且 预算仍有余量时, 才跑 retrieve() 拉具体事实/对话
    - 用 min_fused 门控, 命中不足则跳过, 省 token

这等于给 V5 加上 TencentDB 的 "默认注高层、按需下钻" 能力, 复用现有
reflect/distill 管线(已就位) 与 self_model, 不推翻架构。

接入点(详见同目录 EXTENSIONS.md)
---------------------------------
  替换 hermes 插件 `on_pre_compress` 里的 `self._v5_search(query_text, top_k=5)`
  调用, 或直接作为 memory-context 组装入口。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("ikaros.v5.ext.gated_retrieval")

_HIGH_LAYER_TYPES = ("distilled", "reflect", "identity", "axiom")


# ─── 高层: 自我模型 + 高层记忆 ──────────────────────────────────

def _char_x() -> float:
    try:
        from memory_v5 import preprocess_config as pc
        return float(pc.cfg().get("token_budget", {}).get("char_x", 1.0))
    except Exception:
        return 1.0


def _est(text: str) -> int:
    return max(1, int(len(text or "") * _char_x()))


def _self_model_prompt(max_tokens: int = 400) -> str:
    try:
        from memory_v5.self_model import SelfModel
        sm = SelfModel.load()
        text = sm.get_self_prompt()
        if len(text) > max_tokens:
            text = text[:max_tokens].rstrip() + "…"
        return text
    except Exception as exc:
        logger.debug("gated_retrieval: self_model load failed (%s)", exc)
        return ""


def _high_layer_memories(top_n: int = 3) -> list[dict]:
    try:
        from memory_v5 import store
        with store.conn() as c:
            rows = c.execute(
                "SELECT id, content, type, weight FROM memory "
                "WHERE type IN ('distilled','reflect','identity','axiom') "
                "ORDER BY weight DESC, created DESC LIMIT ?",
                (top_n,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("gated_retrieval: high-layer query failed (%s)", exc)
        return []


# ─── 下钻判定 ───────────────────────────────────────────────────

def _is_substantive(query: str) -> bool:
    """判断 query 是否值得下钻低层检索(过滤寒暄/极短句)。"""
    q = (query or "").strip()
    if len(q) < 4:
        return False
    smalltalk = {"你好", "在吗", "你是谁", "继续", "好的", "然后呢", "嗯", "哦",
                 "ok", "okay", "哈哈", "哎", "诶"}
    if q.lower() in smalltalk:
        return False
    return True


# ─── 门控主入口 ─────────────────────────────────────────────────

def gated_retrieve(
    query: str,
    *,
    top_k: int = 5,
    high_layer: bool = True,
    high_max_tokens: int = 500,
    low_budget_tokens: int = 700,
    drill_min_fused: float = 0.3,
) -> dict:
    """分层门控检索。

    Returns:
        {
          "high_layer": str,           # 已组装的高层上下文(自我模型 + 高层记忆)
          "low_memories": list[dict],  # 条件注入的低层记忆(空=本次不下钻)
          "drilled": bool,             # 是否真的跑了低层检索
          "total_tokens_est": int,
        }
    """
    high_parts: list[str] = []
    if high_layer:
        sm = _self_model_prompt(max_tokens=high_max_tokens)
        if sm:
            high_parts.append("[Ikaros 自我模型]\n" + sm)
        for m in _high_layer_memories(top_n=3):
            high_parts.append(f"  [{m.get('type')}] {m.get('content')}")

    high_text = "\n".join(high_parts)
    high_tokens = _est(high_text)

    drilled = False
    low_memories: list[dict] = []
    if _is_substantive(query) and high_tokens < low_budget_tokens:
        try:
            from memory_v5.memory_retrieval import retrieve
            results = retrieve(query, top_k=top_k)
            low_memories = [r for r in results
                            if float(r.get("score", 0) or 0) >= drill_min_fused]
            drilled = bool(low_memories)
        except Exception as exc:
            logger.debug("gated_retrieval: low-layer retrieve failed (%s)", exc)

    return {
        "high_layer": high_text,
        "low_memories": low_memories,
        "drilled": drilled,
        "total_tokens_est": high_tokens
        + sum(_est(m.get("content", "") or "") for m in low_memories),
    }
