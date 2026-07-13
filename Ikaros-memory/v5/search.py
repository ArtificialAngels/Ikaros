"""
v4.search — V4 语义搜索层 (ChromaDB)

设计目标:
  - API 与 V3 vector_search.py 兼容
  - 显式依赖: chromadb 1.5+ (portable-python 已装, 见 ikaros-paths.json)
  - 不在 import 时崩溃 (V3 沉默失败根因)

V3 沉默失败根因 (2026-07-05 复盘):
  V3 走默认 `python` (hermes-agent venv), 该 venv 没装 chromadb
  → import 死, search 返空
  V4 修: 走 portable-python (有 chromadb 1.5.2) + 文档明示
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ikaros.memory.v4.search")

V4_ROOT = Path(__file__).resolve().parent.parent
V4_DATA_DIR = V4_ROOT / "data" / "v4"
CHROMA_DIR = V4_DATA_DIR / "chroma"

# Embedding 服务: 同 V3, 走 :8587 nomic-embed-text
# V3 注释 (vector_search.py:36-43) 2026-07-05 fix: 用 /embedding singular path
EMBED_URL = os.environ.get("IKAROS_EMBED_URL", "http://127.0.0.1:8587/embedding")
EMBED_MODEL = os.environ.get("IKAROS_EMBED_MODEL", "nomic-embed-text-v1.5-q4")
EMBED_TIMEOUT = 10
USER_AGENT = "ikaros-vector-search-v4/1.0 (curl-compatible)"


def _get_embedding(text: str) -> Optional[list[float]]:
    """调 :8587 embedding 服务.

    V3 → V4 改进:
      - 走相对路径 (urllib 走 absolute URI 触发 404, V3 注释记录)
      - 显式 User-Agent (V3 注释记录 urllib UA 被拒)
      - 失败时显式 log + 返 None, 不 swallow
    """
    import http.client
    from urllib.parse import urlparse

    body = json.dumps({"content": text[:500]}).encode("utf-8")
    try:
        u = urlparse(EMBED_URL)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=EMBED_TIMEOUT)
        conn.request("POST", u.path or "/", body=body, headers={
            "Content-Type": "application/json",
            "Host": u.netloc,
            "User-Agent": USER_AGENT,
        })
        resp = conn.getresponse()
        if resp.status != 200:
            logger.warning("embed HTTP %d for '%s...'", resp.status, text[:30])
            return None
        data = json.loads(resp.read().decode("utf-8"))
        # :8587 实测返回 list: [{"index":0,"embedding":[[...]]}]
        # 也兼容 dict 形状 {"embedding":[[...]]} / {"data":[{"embedding":[...]}]}
        return _extract_vector(data)
    except Exception as e:
        logger.warning("embedding failed: %s", e)
        return None


def _extract_vector(data) -> Optional[list[float]]:
    """从 :8587 各种响应形状中提取单条向量 (list[float]).

    已观测形状:
      - list:  [{"index":0, "embedding":[[...]]}]   (llama-server /embedding 实测)
      - dict:  {"embedding": [[...]]} 或 {"embedding": [...]}
      - dict:  {"data": [{"embedding": [...]}]}      (OpenAI 风格)
    """
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                emb = item.get("embedding")
            elif isinstance(item, list):
                emb = item
            else:
                continue
            vec = _coerce_vector(emb)
            if vec is not None:
                return vec
        return None
    if isinstance(data, dict):
        if "embedding" in data:
            return _coerce_vector(data["embedding"])
        if "data" in data and isinstance(data["data"], list) and data["data"]:
            inner = data["data"][0]
            if isinstance(inner, dict) and "embedding" in inner:
                return _coerce_vector(inner["embedding"])
    return None


def _coerce_vector(emb) -> Optional[list[float]]:
    """embedding 字段可能是 [[...]] (list 套单条) 或 [...]; 展平成 list[float]."""
    if isinstance(emb, list) and emb:
        if isinstance(emb[0], list):
            cand = emb[0]
            if cand and isinstance(cand[0], (int, float)):
                return [float(x) for x in cand]
        elif isinstance(emb[0], (int, float)):
            return [float(x) for x in emb]
    return None


class VectorIndex:
    """V4 ChromaDB 向量索引, 与 v4.store 同步.

    V3 → V4 改进:
      - import chromadb 移到 __init__ (不是模块级), 失败显式
      - 路径走 V4 子目录 (与 V3 隔离)
    """

    def __init__(self, persist_dir: Path | None = None):
        try:
            import chromadb
        except ImportError as e:
            raise ImportError(
                "chromadb not installed. Run via ikaros-mem.bat (uses portable-python) "
                "or: E:\\Ikaros\\portable-python\\python.exe -m pip install chromadb"
            ) from e
        self._persist_dir = Path(persist_dir or CHROMA_DIR)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._persist_dir))
        self._collection = self._client.get_or_create_collection(
            name="ikaros_v4",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("VectorIndex V4: %d vectors in %s",
                    self._collection.count(), self._persist_dir)

    def add(self, memory_id: int, content: str, *,
            type: str = "fact", tags: str = "", weight: float = 0.6) -> bool:
        """添加/更新一条记忆向量."""
        embedding = _get_embedding(content)
        if embedding is None:
            return False
        try:
            self._collection.upsert(
                ids=[str(memory_id)],
                documents=[content],
                embeddings=[embedding],
                metadatas=[{"type": type, "tags": tags, "weight": weight}],
            )
            return True
        except Exception as e:
            logger.warning("vector add failed: %s", e)
            return False

    def search(self, query: str, top_k: int = 5,
               min_weight: float = 0.0) -> list[dict]:
        """语义搜索, 返 [{id, content, type, weight, score}]."""
        embedding = _get_embedding(query)
        if embedding is None:
            logger.warning("search: embedding failed for '%s...'", query[:30])
            return []
        try:
            n = max(1, min(top_k * 2, self._collection.count() or 1))
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=n,
                include=["documents", "metadatas", "distances"],
            )
            if not results or not results.get("ids") or not results["ids"][0]:
                return []
            items = []
            for i, mid in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                dist = results["distances"][0][i] if results.get("distances") else 1.0
                weight = float(meta.get("weight", 0.6))
                if weight < min_weight:
                    continue
                score = max(0.0, min(1.0, 1.0 - float(dist)))
                items.append({
                    "id": mid,
                    "content": results["documents"][0][i],
                    "type": meta.get("type", "fact"),
                    "weight": weight,
                    "distance": float(dist),
                    "score": score,
                })
                if len(items) >= top_k:
                    break
            return items
        except Exception as e:
            logger.warning("vector search failed: %s", e)
            return []

    def stats(self) -> dict:
        return {
            "total_vectors": self._collection.count(),
            "persist_dir": str(self._persist_dir),
            "embed_model": EMBED_MODEL,
            "embed_url": EMBED_URL,
        }


def fused_search(query: str, top_k: int = 5) -> list[dict]:
    """双路融合: FTS5 (关键词) + ChromaDB (语义) → 合并去重.

    V3 → V4: FTS5 走 v4.store, 向量走 v4.search.
    """
    # v4 包在 Ikaros-memory/ 下; 插入 Ikaros-memory 而非其父目录
    sys.path.insert(0, str(V4_ROOT))
    from v5 import store  # noqa: F401

    # 1. FTS5 关键词搜索
    fts_hits = store.search(query, top_k=top_k, min_weight=0.2)
    fts_results = [{
        "id": str(m.id), "content": m.content, "type": m.type,
        "weight": m.weight, "score": 0.3 * (1.0 / (i + 1)), "source": "fts",
    } for i, m in enumerate(fts_hits)]

    # 2. 向量语义搜索
    vec_results: list[dict] = []
    try:
        idx = VectorIndex()
        vec_results = idx.search(query, top_k=top_k)
        for r in vec_results:
            r["score"] = 0.7 * r.get("score", 0)
            r["source"] = "vector"
    except Exception as e:
        logger.warning("vector search skipped: %s", e)

    # 3. 合并去重 (id 维度)
    seen: dict[str, dict] = {}
    for r in fts_results + vec_results:
        if r["id"] not in seen:
            seen[r["id"]] = r
        else:
            seen[r["id"]]["score"] += r.get("score", 0)

    merged = sorted(seen.values(), key=lambda x: -x.get("score", 0))
    return merged[:top_k]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    import sys
    if len(sys.argv) < 3 or sys.argv[1] != "search":
        print("Usage: python v4/search.py search <query>")
        sys.exit(1)
    q = " ".join(sys.argv[2:])
    print(json.dumps(fused_search(q), indent=2, ensure_ascii=False))
