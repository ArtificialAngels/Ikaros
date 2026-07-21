# 详细说明见 docs/scripts/Ikaros-memory/v5/search.md

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ikaros.memory.v5.search")

# 内联说明见 docs/scripts/Ikaros-memory/v5/search.md（见“内联注释摘录”）
_EMBED_LOCK = threading.Lock()
_EMBED_CACHE: "OrderedDict[str, list[float]]" = OrderedDict()
_EMBED_CACHE_MAX = 512

_VI_LOCK = threading.Lock()
_VI: dict = {"instance": None, "dir": None, "ts": 0.0}


def _cache_cfg() -> dict:
    try:
        from v5 import preprocess_config as pc
        return pc.cfg().get("cache", {})
    except Exception:
        return {}


def _cache_enabled() -> bool:
    try:
        return bool(_cache_cfg().get("embedding_enabled", True))
    except Exception:
        return True

MEM_ROOT = Path(__file__).resolve().parent.parent
V5_DATA_DIR = MEM_ROOT / "data" / "v5"
CHROMA_DIR = V5_DATA_DIR / "chroma"

# Embedding 服务: 同 V3, 走 :8587 nomic-embed-text
# V3 注释 (vector_search.py:36-43) 2026-07-05 fix: 用 /embedding singular path
EMBED_URL = os.environ.get("IKAROS_EMBED_URL", "http://127.0.0.1:8587/embedding")
EMBED_MODEL = os.environ.get("IKAROS_EMBED_MODEL", "nomic-embed-text-v2-moe")
EMBED_TIMEOUT = 10
USER_AGENT = "ikaros-vector-search-v4/1.0 (curl-compatible)"


def _fetch_embedding(text: str, task: str = "query") -> Optional[list[float]]:
    """调 :8587 embedding 服务 (网络实现, 无缓存).

    V3 → V4 改进:
      - 走相对路径 (urllib 走 absolute URI 触发 404, V3 注释记录)
      - 显式 User-Agent (V3 注释记录 urllib UA 被拒)
      - 失败时显式 log + 返 None, 不 swallow

    nomic-embed-text-v2-moe 任务前缀 (2026-07-14):
      - task="query"    (语义搜索)  -> "search_query: "
      - task="document" (入库/重嵌) -> "search_document: "
      不加前缀会落到默认任务, 导致 query/document 向量空间不一致、召回失真.
    """
    import http.client
    from urllib.parse import urlparse

    prefix = "search_document: " if task == "document" else "search_query: "
    payload = (prefix + text)[:2000]
    body = json.dumps({"content": payload}).encode("utf-8")
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


def _get_embedding(text: str, task: str = "query") -> Optional[list[float]]:
    """带进程级 LRU 缓存的 embedding 入口 (key = task+text[:2000]).

    命中缓存 => 跳过 :8587 网络调用 (闲时省 ~60ms, 忙时省 ~1s, 冷启动省更多).
    缓存跨 session 共享 (watchdog 进程长驻); 容量上限防内存膨胀.
    """
    if not _cache_enabled():
        return _fetch_embedding(text, task)
    prefix = "search_document: " if task == "document" else "search_query: "
    key = (prefix + text)[:2000]
    with _EMBED_LOCK:
        if key in _EMBED_CACHE:
            _EMBED_CACHE.move_to_end(key)
            return _EMBED_CACHE[key]
    vec = _fetch_embedding(text, task)
    if vec is not None:
        cap = int(_cache_cfg().get("embedding_max", 512))
        with _EMBED_LOCK:
            _EMBED_CACHE[key] = vec
            _EMBED_CACHE.move_to_end(key)
            while len(_EMBED_CACHE) > cap:
                _EMBED_CACHE.popitem(last=False)
    return vec


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
                "or: E:\\Ikaros\\runtime\\portable-python\\python.exe -m pip install chromadb"
            ) from e
        self._persist_dir = Path(persist_dir or CHROMA_DIR)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._persist_dir))
        self._collection = self._client.get_or_create_collection(
            name="ikaros_v5",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("VectorIndex V5: %d vectors in %s",
                    self._collection.count(), self._persist_dir)

    def add(self, memory_id: int, content: str, *,
            type: str = "fact", tags: str = "", weight: float = 0.6) -> bool:
        """添加/更新一条记忆向量."""
        embedding = _get_embedding(content, task="document")
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
        embedding = _get_embedding(query, task="query")
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


def get_vector_index(persist_dir: Path | None = None, *, refresh: bool = False):
    """返回缓存的 VectorIndex 单例 (性能优化, 哥哥优化项).

    - 进程内复用同一个 chroma 客户端, 避免每轮 `VectorIndex()` 重开 (冷启动 850ms, 暖后 ~15ms).
    - "每轮覆写"语义: 每隔 vector_refresh_seconds 自动重开一次, 拾取反思循环等
      **其他进程**新增的记忆 (同进程内的 add 经同一客户端立即可见, 无需重开).
    - 配置 cache.vector_index_singleton=false 时退化为每次新建 (原行为).
    """
    cfg = _cache_cfg()
    if not cfg.get("vector_index_singleton", True):
        return VectorIndex(persist_dir)
    pdir = str(persist_dir or CHROMA_DIR)
    refresh_s = float(cfg.get("vector_refresh_seconds", 30))
    now = time.time()
    with _VI_LOCK:
        inst = _VI["instance"]
        if (inst is None or _VI["dir"] != pdir or refresh
                or (now - _VI["ts"]) > refresh_s):
            try:
                inst = VectorIndex(persist_dir)
                _VI["instance"] = inst
                _VI["dir"] = pdir
                _VI["ts"] = now
            except Exception:
                # 创建失败: 清空缓存, 交给调用方静默处理 (不缓存坏实例)
                _VI["instance"] = None
                _VI["dir"] = None
                raise
        return _VI["instance"]


def fused_search(query: str, top_k: int = 5) -> list[dict]:
    """双路融合: FTS5 (关键词) + ChromaDB (语义) → 合并去重.

    V3 → V4: FTS5 走 v4.store, 向量走 v4.search.
    """
    # v4 包在 Ikaros-memory/ 下; 插入 Ikaros-memory 而非其父目录
    sys.path.insert(0, str(MEM_ROOT))
    from v5 import store  # noqa: F401

    # 1. FTS5 keyword search
    fts_hits = store.search(query, top_k=top_k, min_weight=0.2)
    fts_results = [{
        "id": str(m.id), "content": m.content, "type": m.type,
        "weight": m.weight, "score": 0.3 * (1.0 / (i + 1)), "source": "fts",
        "pad_p": getattr(m, "pad_p", 0.0), "pad_a": getattr(m, "pad_a", 0.0),
    } for i, m in enumerate(fts_hits)]

    # 2. 向量语义搜索
    vec_results: list[dict] = []
    try:
        idx = get_vector_index()
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
