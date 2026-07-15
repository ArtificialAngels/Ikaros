"""
v5.memory_retrieval — 记忆多路检索 (R3, P1)

三路融合 (spec 2.3):
  ① FTS5 关键词搜索  (权重 fts_weight=0.3)
  ② ChromaDB 向量语义 (权重 vector_weight=0.7)
  ③ 时间范围过滤      (可选, 由调用方解析时间指代后传入)

融合分 = 向量分×0.7 + FTS5分×0.3
  → 时间衰减: ×(1 - time_decay_per_day × days_since_creation)
  → 类型 boost: 情感×1.2 / 事实×1.1 / 对话×0.8
  → 截取 top_k, 过滤 min_fused_score
  → exclude: 跳过与已知文本(用户原话/历史)重叠的记忆

纯规则融合, 不调 LLM. 所有阈值见 preprocess_config.yaml.
失败隔离: 任意一路异常静默跳过, 不抛给调用方.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger("ikaros.v5.memory_retrieval")

# 检索结果短 TTL 缓存 (哥哥优化项): 同 query(含 top_k/time_range/exclude) 20s 内直接返回,
# 跳过 embedding + chroma 全程. 聊天里"继续/好的/然后呢"等高频短句命中率高, 体感明显.
_RET_CACHE: dict = {}
_RET_CACHE_LOCK = threading.Lock()


def _cache_cfg() -> dict:
    try:
        from v5 import preprocess_config as pc
        return pc.cfg().get("cache", {})
    except Exception:
        return {}


def _retrieve_ttl() -> float:
    try:
        c = _cache_cfg()
        if not c.get("retrieve_ttl_enabled", True):
            return 0.0
        return float(c.get("retrieve_ttl_seconds", 20))
    except Exception:
        return 20.0


def _defaults() -> dict:
    return {
        "vector_weight": 0.7, "fts_weight": 0.3,
        "time_decay_per_day": 0.05, "min_fused_score": 0.6, "top_k": 5,
        "type_boost": {"emotion": 1.2, "fact": 1.1, "conversation": 0.8, "default": 1.0},
    }


def retrieve(
    query: str,
    *,
    top_k: int | None = None,
    time_range: tuple[float, float] | None = None,
    exclude: list[str] | None = None,
    min_weight: float = 0.0,
) -> list[dict]:
    """三路融合检索, 返按 fused_score 降序的 list[dict].

    返回字段: id, content, type, weight, tags, created, pad_p, pad_a, source, score
    """
    if not query or not query.strip():
        return []

    # 检索结果短 TTL 缓存: 同 query 短期内重复命中直接返回 (跳过 embedding + chroma)
    ttl = _retrieve_ttl()
    cache_key = (query, top_k, time_range, tuple(exclude or []))
    if ttl > 0:
        with _RET_CACHE_LOCK:
            hit = _RET_CACHE.get(cache_key)
            if hit is not None and (time.time() - hit[0]) < ttl:
                return hit[1]

    # 阈值 (fail-open)
    try:
        from v5 import preprocess_config as pc
        mr = pc.cfg()["memory_retrieval"]
    except Exception:
        mr = _defaults()
    vw = float(mr["vector_weight"])
    fw = float(mr["fts_weight"])
    decay = float(mr["time_decay_per_day"])
    min_fused = float(mr["min_fused_score"])
    tk = int(top_k or mr["top_k"])
    boosts = mr["type_boost"]

    # ── ① FTS5 关键词 ──
    fts_list: list = []
    try:
        from v5 import store
        fts_list = store.search(query, top_k=max(tk * 2, 6), min_weight=min_weight)
    except Exception as e:
        logger.debug("FTS5 search failed: %s", e)

    # ── ② 向量语义 ──
    vec_list: list = []
    try:
        from v5.search import get_vector_index
        vec_list = get_vector_index().search(query, top_k=max(tk * 2, 6))
    except Exception as e:
        logger.debug("vector search failed: %s", e)

    # ── ③ 时间范围 ──
    time_list: list = []
    if time_range:
        try:
            from v5 import store
            start, end = time_range
            time_list = store.search_by_time_range(start, end, limit=max(tk * 2, 6))
        except Exception as e:
            logger.debug("time-range search failed: %s", e)

    # ── 去重合并 (按 id) ──
    merged: dict[str, dict] = {}

    def _add(mid, content, mtype, weight, created, pad_p, pad_a, source, raw):
        key = str(mid)
        if key in merged:
            # 同一记忆多路命中 → 累加分量 (0.7 向量分量 + 0.3 FTS5 分量 = 融合分)
            merged[key]["raw"] += raw
            return
        merged[key] = {
            "id": key, "content": content, "type": mtype, "weight": weight,
            "tags": "", "created": created, "pad_p": pad_p, "pad_a": pad_a,
            "source": source, "raw": raw,
        }

    for i, m in enumerate(fts_list):
        _add(m.id, m.content, m.type, m.weight, m.created,
             getattr(m, "pad_p", 0.0), getattr(m, "pad_a", 0.0), "fts", fw * (1.0 / (i + 1)))
    for r in vec_list:
        _add(r.get("id"), r.get("content", ""), r.get("type", "fact"),
             r.get("weight", 0.5), r.get("created", 0.0),
             r.get("pad_p", 0.0), r.get("pad_a", 0.0), "vec", vw * float(r.get("score", 0.0)))
    for m in time_list:
        # 时间指代命中是用户明确信号, 给强初始分确保过 min_fused 阈值
        _add(m.id, m.content, m.type, m.weight, m.created,
             getattr(m, "pad_p", 0.0), getattr(m, "pad_a", 0.0), "time", 1.0)

    # ── 融合分: 时间衰减 + 类型 boost ──
    now = time.time()
    excl = [e for e in (exclude or []) if e]
    out: list[dict] = []
    for item in merged.values():
        fused = float(item["raw"])
        # 时间衰减: 仅作用于 fts/vec 来源 (spec: 衰减不能太激进, 旧偏好仍有效)
        # 时间指代命中 (source=='time') 本身是用户明确信号, 不叠加衰减
        if item["source"] != "time" and item["created"]:
            days = (now - float(item["created"])) / 86400.0
            if days > 0:
                fused *= max(0.2, 1.0 - decay * days)  # 下限 0.2, 不归零
        b = boosts.get(item["type"], boosts.get("default", 1.0))
        fused *= b
        item["score"] = fused
        # 去重已知信息 (子串重叠)
        if excl:
            for ex in excl:
                if ex and (ex in item["content"] or item["content"] in ex):
                    item["score"] = -1.0
                    break
        out.append(item)

    out = [x for x in out if x["score"] >= min_fused]
    out.sort(key=lambda x: -x["score"])
    result = out[:tk]
    if ttl > 0:
        with _RET_CACHE_LOCK:
            _RET_CACHE[cache_key] = (time.time(), result)
            # 防膨胀: 超过 200 条清最旧 50 条
            if len(_RET_CACHE) > 200:
                oldest = sorted(_RET_CACHE.items(), key=lambda kv: kv[1][0])[:50]
                for k, _ in oldest:
                    _RET_CACHE.pop(k, None)
    return result


if __name__ == "__main__":
    import json
    for q in ["哥哥喜欢简洁", "CUDA 升级"]:
        print(f"## {q}")
        print(json.dumps(retrieve(q), ensure_ascii=False, indent=2)[:800])
