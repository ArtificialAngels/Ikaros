"""vector_search.py — ChromaDB 向量搜索 (语义检索 v3 记忆)

用 :8587 nomic-embed-text 生成 embedding, ChromaDB 做 ANN 搜索.
与 v3 的 FTS5 文本搜索互补: FTS5 擅长精确关键词, 向量搜索擅长语义相似.

架构:
  v3.db (FTS5 文本搜索) ← 关键词匹配
  ChromaDB (向量搜索)    ← 语义相似
  search() 做 2 路融合 → 返回最佳结果

用法:
  from vector_search import VectorIndex
  idx = VectorIndex()
  idx.sync_from_v3()                        # 全量同步 v3 → chroma
  results = idx.search("哥哥喜欢什么", top_k=5)
  idx.add("memory_id_1", "哥哥喜欢红烧肉", type="fact")

数据位置: Ikaros-memory/data/chroma/ (持久化)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ikaros.memory.vector")

_HERE = Path(__file__).resolve().parent
_CHROMA_DIR = _HERE / "data" / "chroma"
_EMBED_URL = "http://127.0.0.1:8587/v1/embeddings"
_EMBED_MODEL = "nomic-embed-text-v1.5-q4"
_EMBED_TIMEOUT = 10


def _get_embedding(text: str) -> list[float] | None:
    """调 :8587 embedding 服务获取向量."""
    body = json.dumps({
        "model": _EMBED_MODEL,
        "input": text[:500],  # 截断避免超长
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            _EMBED_URL, data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_EMBED_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        embeddings = data.get("data", [])
        if embeddings:
            return embeddings[0]["embedding"]
        return None
    except Exception as e:
        logger.debug("embedding failed: %s", e)
        return None


def _get_embeddings_batch(texts: list[str]) -> list[list[float] | None]:
    """批量获取 embedding (单条调用, 简单可靠)."""
    results = []
    for text in texts:
        results.append(_get_embedding(text))
    return results


class VectorIndex:
    """ChromaDB 向量索引, 与 v3.db 同步."""

    def __init__(self, persist_dir: str | Path | None = None):
        import chromadb
        self._persist_dir = str(persist_dir or _CHROMA_DIR)
        Path(self._persist_dir).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self._persist_dir)
        self._collection = self._client.get_or_create_collection(
            name="ikaros_v3",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("VectorIndex: %d vectors in %s",
                     self._collection.count(), self._persist_dir)

    def add(self, memory_id: str, content: str, *,
            type: str = "fact", tags: str = "", weight: float = 0.6) -> bool:
        """添加/更新一条记忆向量."""
        embedding = _get_embedding(content)
        if embedding is None:
            return False
        try:
            self._collection.upsert(
                ids=[memory_id],
                documents=[content],
                embeddings=[embedding],
                metadatas=[{"type": type, "tags": tags, "weight": weight}],
            )
            return True
        except Exception as e:
            logger.warning("vector add failed: %s", e)
            return False

    def delete(self, memory_id: str) -> bool:
        """删除一条记忆向量."""
        try:
            self._collection.delete(ids=[memory_id])
            return True
        except Exception:
            return False

    def search(self, query: str, top_k: int = 5,
               min_weight: float = 0.0) -> list[dict]:
        """语义搜索. 返回 [{id, content, type, weight, distance}]."""
        embedding = _get_embedding(query)
        if embedding is None:
            logger.warning("search: embedding failed for '%s'", query[:30])
            return []
        try:
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=min(top_k * 2, self._collection.count() or 1),
                include=["documents", "metadatas", "distances"],
            )
            if not results or not results["ids"] or not results["ids"][0]:
                return []

            items = []
            for i, mid in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                dist = results["distances"][0][i] if results["distances"] else 1.0
                weight = meta.get("weight", 0.6)
                if weight < min_weight:
                    continue
                items.append({
                    "id": mid,
                    "content": results["documents"][0][i],
                    "type": meta.get("type", "fact"),
                    "weight": weight,
                    "distance": dist,
                })
                if len(items) >= top_k:
                    break
            return items
        except Exception as e:
            logger.warning("vector search failed: %s", e)
            return []

    def sync_from_v3(self) -> int:
        """从 v3.db 全量同步到 ChromaDB. 返回同步条数."""
        # 导入 v3 模块
        sys.path.insert(0, str(_HERE))
        import importlib
        v3 = importlib.import_module("ikaros-memory-v3")

        with v3.conn() as c:
            rows = c.execute(
                "SELECT id, content, type, tags, weight FROM memory "
                "WHERE type NOT IN ('conversation') "  # 跳过原始对话
                "ORDER BY weight DESC"
            ).fetchall()

        if not rows:
            logger.info("sync: no memories to sync")
            return 0

        # 检查哪些已存在 (避免重复 embedding)
        existing_ids = set(self._collection.get()["ids"])
        new_rows = [r for r in rows if str(r["id"]) not in existing_ids]

        if not new_rows:
            logger.info("sync: all %d memories already indexed", len(rows))
            return 0

        logger.info("sync: %d new memories to embed (%d total)", len(new_rows), len(rows))
        synced = 0
        for row in new_rows:
            mid = str(row["id"])
            content = row["content"]
            embedding = _get_embedding(content)
            if embedding is None:
                continue
            try:
                self._collection.upsert(
                    ids=[mid],
                    documents=[content],
                    embeddings=[embedding],
                    metadatas=[{
                        "type": row["type"],
                        "tags": row["tags"] or "",
                        "weight": row["weight"],
                    }],
                )
                synced += 1
            except Exception as e:
                logger.debug("sync upsert %s failed: %s", mid, e)

        logger.info("sync: %d/%d memories synced to ChromaDB", synced, len(new_rows))
        return synced

    def stats(self) -> dict:
        """返回索引统计."""
        return {
            "total_vectors": self._collection.count(),
            "persist_dir": self._persist_dir,
            "embed_model": _EMBED_MODEL,
            "embed_url": _EMBED_URL,
        }


def fused_search(query: str, top_k: int = 5) -> list[dict]:
    """2 路融合搜索: FTS5 (关键词) + ChromaDB (语义) → 合并去重.

    策略:
      1. v3.search() → 关键词结果 (权重 0.6)
      2. VectorIndex.search() → 语义结果 (权重 0.4)
      3. 合并, 按综合分排序, 返 top_k
    """
    sys.path.insert(0, str(_HERE))
    import importlib
    v3 = importlib.import_module("ikaros-memory-v3")

    # 1. FTS5 关键词搜索
    fts_results = []
    try:
        fts_results = v3.search(query, top_k=top_k, min_weight=0.2)
    except Exception:
        pass

    # 2. 向量语义搜索
    vec_results = []
    try:
        idx = VectorIndex()
        vec_results = idx.search(query, top_k=top_k, min_weight=0.2)
    except Exception:
        pass

    # 3. 融合: 用 id 去重, 关键词结果优先
    seen_ids = set()
    merged = []

    # 先加关键词结果 (score = weight * 0.6)
    for r in fts_results:
        rid = str(r.get("id", ""))
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        merged.append({
            "id": r.get("id"),
            "content": r["content"],
            "type": r.get("type"),
            "weight": r.get("weight", 0.5),
            "score": r.get("weight", 0.5) * 0.6,
            "source": "fts5",
        })

    # 再加语义结果 (score = (1 - distance) * 0.4)
    for r in vec_results:
        rid = str(r.get("id", ""))
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        dist = r.get("distance", 0.5)
        merged.append({
            "id": r.get("id"),
            "content": r["content"],
            "type": r.get("type"),
            "weight": r.get("weight", 0.5),
            "score": (1.0 - dist) * 0.4 + r.get("weight", 0.5) * 0.2,
            "source": "vector",
        })

    # 按 score 降序
    merged.sort(key=lambda x: x["score"], reverse=True)
    return merged[:top_k]


# ─── CLI ───

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    parser = argparse.ArgumentParser(description="Vector search for v3 memory")
    parser.add_argument("--sync", action="store_true", help="Sync v3 → ChromaDB")
    parser.add_argument("--search", type=str, help="Search query")
    parser.add_argument("--fused", type=str, help="Fused search (FTS5 + vector)")
    parser.add_argument("--stats", action="store_true", help="Show stats")
    args = parser.parse_args()

    idx = VectorIndex()

    if args.sync:
        n = idx.sync_from_v3()
        print(f"Synced: {n} memories")
    elif args.search:
        results = idx.search(args.search, top_k=5)
        for r in results:
            print(f"  [{r['id']}] dist={r['distance']:.3f} w={r['weight']:.2f} | {r['content'][:60]}")
    elif args.fused:
        results = fused_search(args.fused, top_k=5)
        for r in results:
            print(f"  [{r['id']}] score={r['score']:.3f} src={r['source']} | {r['content'][:60]}")
    elif args.stats:
        print(json.dumps(idx.stats(), indent=2))
    else:
        parser.print_help()
