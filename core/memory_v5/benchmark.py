"""检索质量基准 (golden-query eval) — V5.7 (2026-08-14).

在隔离环境 (temp DB + 确定性 mock 向量) 测 memory_retrieval 检索质量:
Hit@k / MRR / Precision@k + 复合得分。mock 向量用 token 重叠近似语义相似度,
零 :8587 依赖, 可离线复现。

运行: python -m memory_v5.benchmark   (需 sys.path 含 core/)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

# 自举: benchmark.py 在 core/memory_v5/ 下, 需 core/ 在 sys.path
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))


def _tokens(text: str) -> set:
    from memory_v5.skill_store import _tokens
    return set(_tokens(text))


class MockVectorIndex:
    """token 重叠近似语义相似度 (Jaccard-like), 代替 :8587 嵌入。"""

    def __init__(self, memories: list[dict]):
        self._memories = memories

    def search(self, query, top_k=5, min_weight=0.0):
        qt = _tokens(query)
        if not qt:
            return []
        scored = []
        for m in self._memories:
            overlap = len(qt & _tokens(m["content"]))
            if overlap == 0:
                continue
            sim = min(1.0, overlap / max(1, len(qt)))
            scored.append({
                "id": str(m["id"]), "content": m["content"],
                "type": m["type"], "weight": m["weight"], "score": sim,
            })
        scored.sort(key=lambda x: -x["score"])
        return scored[:top_k]


# ── 语料: 覆盖偏好/事实/项目坑·决策·约定/时效更替/决策 ──
CORPUS: list[dict] = [
    {"content": "哥哥喜欢喝美式咖啡", "type": "preference", "weight": 0.8, "tags": ""},
    {"content": "哥哥偏好简洁直接的沟通方式", "type": "preference", "weight": 0.8, "tags": ""},
    {"content": "伊卡洛斯的本地 LLM 是 Phi-4-mini 模型", "type": "fact", "weight": 0.7, "tags": ""},
    {"content": "向量索引用 bge-m3 做 1024 维嵌入", "type": "fact", "weight": 0.7, "tags": ""},
    {"content": "检索结果有 20 秒短缓存", "type": "fact", "weight": 0.6, "tags": ""},
    {"content": "choma 向量索引在多进程并发写时报 hnsw compactor 冲突", "type": "lesson", "weight": 0.7,
     "tags": "v5_domain:project,v5_project:ikaros,v5_kind:pitfall"},
    {"content": "choma 向量写前加跨进程文件锁解决 compactor 冲突", "type": "decision", "weight": 0.8,
     "tags": "v5_domain:project,v5_project:ikaros,v5_kind:decision"},
    {"content": "llama-server 必须经看门狗启动避免 SIGSEGV", "type": "fact", "weight": 0.7,
     "tags": "v5_domain:project,v5_project:ikaros,v5_kind:convention"},
    {"content": "反思管线 LLM 生成类 op 已停用避免白烧 API", "type": "decision", "weight": 0.7, "tags": ""},
    {"content": "用户住在上海", "type": "fact", "weight": 0.8, "tags": ""},
    {"content": "用户住在北京", "type": "fact", "weight": 0.8, "tags": "", "_valid_to_past": True},
]

# ── 金标查询: (query, 期望命中的内容子串) ──
QUERIES: list[tuple[str, str]] = [
    ("哥哥喜欢喝什么咖啡", "哥哥喜欢喝美式咖啡"),
    ("本地 LLM 用的是什么模型", "Phi-4-mini"),
    ("向量索引并发写为什么报错", "compactor 冲突"),
    ("向量索引并发写冲突怎么解决", "文件锁解决"),
    ("用户现在住在哪个城市", "用户住在上海"),
    ("llama-server 为什么不能裸跑", "看门狗启动"),
    ("反思管线哪些 op 停用了", "LLM 生成类 op 已停用"),
    ("检索有没有缓存", "20 秒短缓存"),
    ("哥哥喜欢什么样的沟通", "简洁直接"),
    ("向量嵌入用的什么模型", "bge-m3"),
]


def setup_temp_db(corpus: list[dict]) -> list[dict]:
    """建临时库 + 插入语料, 返回带 id 的记忆列表 (mock 向量用)。"""
    import memory_v5.store as store
    import memory_v5.entity_graph as eg
    from memory_v5.extensions.temporal_graph import apply_migration

    tmp = tempfile.mkdtemp(prefix="bench_")
    db = Path(os.path.join(tmp, "v5.db"))
    store.V5_DB_PATH = db
    eg.EG_DB_PATH = db  # 隔离实体图 (避免命中真实 eg)
    store.conn()
    apply_migration()

    memories = []
    for m in corpus:
        with store.conn() as c:
            cur = c.execute(
                "INSERT INTO memory (content, type, tags, weight) VALUES (?, ?, ?, ?)",
                (m["content"], m["type"], m["tags"], m["weight"]),
            )
            c.commit()
            mid = int(cur.lastrowid)
        if m.get("_valid_to_past"):
            with store.conn() as c:
                c.execute("UPDATE memory SET valid_to = ? WHERE id = ?",
                          (time.time() - 100, mid))
                c.commit()
        memories.append({"id": mid, "content": m["content"], "type": m["type"],
                         "weight": m["weight"], "tags": m["tags"]})
    return memories


def run_benchmark(real: bool = False):
    import memory_v5.search as search_mod
    from memory_v5.memory_retrieval import unified_retrieve

    memories = setup_temp_db(CORPUS)
    if real:
        # 真实 bge-m3 嵌入 (:8587), 建隔离 Chroma 集合
        from memory_v5.search import VectorIndex
        tmp_chroma = Path(tempfile.mkdtemp(prefix="bench_chroma_"))
        idx = VectorIndex(tmp_chroma)
        for m in memories:
            idx.add(m["id"], m["content"], type=m["type"],
                    tags=m["tags"], weight=m["weight"])
        search_mod.get_vector_index = lambda *a, **k: idx
    else:
        # mock 向量: token 重叠近似语义 (隔离 :8587)
        search_mod.get_vector_index = lambda *a, **k: MockVectorIndex(memories)

    hits = {1: 0, 3: 0, 5: 0}
    mrr = 0.0
    precision5 = 0.0
    details = []
    for q, gold in QUERIES:
        results = unified_retrieve(q, scope="auto", top_k=5)
        rank = None
        for i, r in enumerate(results, start=1):
            c = r.get("content", "")
            if gold in c or c in gold:
                rank = i
                break
        if rank is not None:
            for k in (1, 3, 5):
                if rank <= k:
                    hits[k] += 1
            mrr += 1.0 / rank
        precision5 += (1.0 if rank is not None and rank <= 5 else 0.0) / 1.0
        details.append((q, rank, [r.get("content", "")[:24] for r in results[:3]]))

    n = len(QUERIES)
    metrics = {
        "n_queries": n,
        "hit@1": round(hits[1] / n, 3),
        "hit@3": round(hits[3] / n, 3),
        "hit@5": round(hits[5] / n, 3),
        "mrr": round(mrr / n, 3),
        "precision@5": round(precision5 / n, 3),
    }
    # 复合得分 0-100: 加权 hit@1/hit@3/hit@5/mrr
    composite = 100 * (0.35 * metrics["hit@1"] + 0.25 * metrics["hit@3"]
                       + 0.15 * metrics["hit@5"] + 0.25 * metrics["mrr"])
    metrics["composite"] = round(composite, 1)
    return metrics, details


def main():
    real = "--real" in sys.argv
    metrics, details = run_benchmark(real=real)
    mode = "真实 bge-m3 嵌入 (:8587)" if real else "mock token 重叠 (离线)"
    print("=" * 60)
    print(f"Ikaros V5 记忆检索质量基准 (golden-query, 隔离环境, {mode})")
    print("=" * 60)
    for k in ("n_queries", "hit@1", "hit@3", "hit@5", "mrr", "precision@5", "composite"):
        print(f"  {k:<12}: {metrics[k]}")
    print("-" * 60)
    print("逐条 (query → gold rank | top3 内容预览):")
    for q, rank, preview in details:
        rank_s = f"rank={rank}" if rank else "MISS"
        print(f"  [{rank_s}] {q}")
        for p in preview:
            print(f"        - {p}")
    return metrics


if __name__ == "__main__":
    main()
