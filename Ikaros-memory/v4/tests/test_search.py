"""
v4.tests.test_search — V4 search 单元测试

V3 search 没单测, ChromaDB 沉默失败 (V3 走 venv 没装 chromadb → 返空)
V4 强制覆盖: init / add / search / stats / fused_search 五条路径.

设计原则:
  - VectorIndex 用 tmp_path 不污染默认 chroma dir
  - _get_embedding 用 monkeypatch mock 掉 (不需要 :8587 embedding 服务)
  - fused_search mock v4.store.search 返假 Memory, 验证双路都调
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

V4_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V4_ROOT.parent))


def _fresh_db(tmp_path: Path):
    """让 v4.store 写到 tmp_path. (与 test_store._fresh_db 同模式)"""
    import v4.store as store_mod
    fresh_dir = tmp_path / "v4_data"
    fresh_dir.mkdir(parents=True, exist_ok=True)
    store_mod.V4_DATA_DIR = fresh_dir
    store_mod.V4_DB_PATH = fresh_dir / "v4.db"
    store_mod.close()
    return store_mod


def _fake_embedding(text: str):
    """固定维度的假 embedding (chromadb 接受 list[float])."""
    # 用 hash 取 4 维向量, 不同 text 不同值, 验证 query 真的走
    h = abs(hash(text)) % 1000
    return [float((h + i) % 7) / 7.0 for i in range(4)]


# ─── 1. VectorIndex init ────────────────────────────────────────

def test_vector_index_init_with_tmp_path(tmp_path: Path):
    """VectorIndex 接受临时目录, 不报错, 不污染默认 chroma dir."""
    from v4 import search as search_mod

    persist = tmp_path / "chroma_test"
    idx = search_mod.VectorIndex(persist_dir=persist)

    assert idx is not None
    # init 应该创建 persist dir
    assert persist.exists() and persist.is_dir()
    # 默认 collection name = ikaros_v4, metadata cosine
    assert idx._collection.name == "ikaros_v4"
    assert idx._collection.metadata.get("hnsw:space") == "cosine"
    # 初始空 collection, count 应为 0
    assert idx._collection.count() == 0
    # 不应写到默认 CHROMA_DIR (V4_ROOT/data/v4/chroma)
    assert not search_mod.CHROMA_DIR == persist


# ─── 2. VectorIndex.add (mock embedding) ───────────────────────

def test_vector_index_add_calls_upsert(tmp_path: Path, monkeypatch):
    """add() 走通: mock _get_embedding 返假向量, 验证 _collection.upsert 被调."""
    from v4 import search as search_mod

    # 用 monkeypatch 替换模块级 _get_embedding, 不真打 :8587
    monkeypatch.setattr(search_mod, "_get_embedding", _fake_embedding)

    idx = search_mod.VectorIndex(persist_dir=tmp_path / "chroma_add")

    with patch.object(idx._collection, "upsert") as mock_upsert:
        ok = idx.add(memory_id=42, content="哥哥喜欢 terse 中文", type="preference", tags="v4", weight=0.9)

    assert ok is True
    mock_upsert.assert_called_once()
    call_kwargs = mock_upsert.call_args.kwargs
    # upsert 必须传 ids / documents / embeddings / metadatas
    assert call_kwargs["ids"] == ["42"]
    assert call_kwargs["documents"] == ["哥哥喜欢 terse 中文"]
    assert call_kwargs["embeddings"] and isinstance(call_kwargs["embeddings"], list)
    assert call_kwargs["metadatas"] == [{"type": "preference", "tags": "v4", "weight": 0.9}]


# ─── 3. VectorIndex.add 失败路径 (embedding 返 None) ─────────────

def test_vector_index_add_returns_false_on_embedding_fail(tmp_path: Path, monkeypatch):
    """_get_embedding 返 None 时, add() 应返 False 不调 upsert."""
    from v4 import search as search_mod

    monkeypatch.setattr(search_mod, "_get_embedding", lambda _t: None)
    idx = search_mod.VectorIndex(persist_dir=tmp_path / "chroma_fail")

    with patch.object(idx._collection, "upsert") as mock_upsert:
        ok = idx.add(memory_id=1, content="x")

    assert ok is False
    mock_upsert.assert_not_called()


# ─── 4. VectorIndex.search ──────────────────────────────────────

def test_vector_index_search_returns_results(tmp_path: Path, monkeypatch):
    """search() 走通: 先 add 一条, 再 search 同一文本, 应能命中."""
    from v4 import search as search_mod

    monkeypatch.setattr(search_mod, "_get_embedding", _fake_embedding)

    idx = search_mod.VectorIndex(persist_dir=tmp_path / "chroma_search")

    # 先 add 一条, 让 collection 非空
    idx.add(memory_id=100, content="V4 alpha.2 装机", type="fact", weight=0.7)

    with patch.object(idx._collection, "query", wraps=idx._collection.query) as mock_query:
        hits = idx.search("V4 alpha.2", top_k=3, min_weight=0.0)

    assert isinstance(hits, list)
    # mock 验证 query 真的被调, 且参数合规
    mock_query.assert_called_once()
    q_kwargs = mock_query.call_args.kwargs
    assert "query_embeddings" in q_kwargs
    assert q_kwargs["n_results"] >= 1
    # 至少包含我们 add 的那条
    ids = [h["id"] for h in hits]
    assert "100" in ids


# ─── 5. VectorIndex.stats ──────────────────────────────────────

def test_vector_index_stats_structure(tmp_path: Path):
    """stats() 应返 dict, 含 total_vectors / persist_dir / embed_model / embed_url."""
    from v4 import search as search_mod

    persist = tmp_path / "chroma_stats"
    idx = search_mod.VectorIndex(persist_dir=persist)

    s = idx.stats()

    assert isinstance(s, dict)
    assert "total_vectors" in s and s["total_vectors"] == 0
    assert "persist_dir" in s and str(persist) in s["persist_dir"]
    assert "embed_model" in s and s["embed_model"] == search_mod.EMBED_MODEL
    assert "embed_url" in s and s["embed_url"] == search_mod.EMBED_URL


# ─── 6. fused_search 双路融合 ──────────────────────────────────

def test_fused_search_calls_both_fts_and_vector(tmp_path: Path, monkeypatch):
    """fused_search 同时走 v4.store.search (FTS) 和 VectorIndex().search (vector)."""
    from v4 import store as store_mod
    from v4 import search as search_mod

    # 1. 准备 fake store + fake FTS 结果
    _fresh_db(tmp_path)
    monkeypatch.setattr(search_mod, "_get_embedding", _fake_embedding)

    # 假 FTS 命中: 一个 Memory 实例
    fake_fts_hit = store_mod.Memory(
        id=1, content="FTS 命中", type="fact", tags="fts",
        weight=0.6, access_count=0, last_accessed=0.0, created=0.0,
        short_term=True, long_term=False,
    )

    # mock v4.store.search 返假 FTS 结果
    monkeypatch.setattr(store_mod, "search", lambda *a, **kw: [fake_fts_hit])

    # mock VectorIndex 的 .search 返假 vector 结果 (避免起 chroma)
    class FakeVectorResult(dict):
        pass

    fake_vec_hits = [
        {"id": "2", "content": "vector 命中", "type": "fact", "weight": 0.8,
         "distance": 0.1, "score": 0.9},
        {"id": "1", "content": "重复 id", "type": "fact", "weight": 0.6,
         "distance": 0.5, "score": 0.5},
    ]

    with patch.object(search_mod.VectorIndex, "search", return_value=fake_vec_hits):
        merged = search_mod.fused_search("双路融合测试", top_k=5)

    # 验证: 返回非空, id 维度去重后应包含 '1' (FTS+vector 都返) 和 '2' (只 vector)
    assert isinstance(merged, list)
    assert len(merged) >= 1
    ids = [r["id"] for r in merged]
    assert "1" in ids
    assert "2" in ids

    # 验证 source 标签: FTS 路径标 source='fts', vector 路径标 source='vector'
    sources = {r["id"]: r.get("source") for r in merged}
    # id=1 同时出现两条, 后写的胜出 (merged score 加和); id=2 只来自 vector
    assert sources["2"] == "vector"
    # id=1 最后一次写是 vector (fts_results 在前, vec_results 在后, 后写胜)
    assert sources["1"] in ("fts", "vector")

    # 验证去重: 同一 id 1 出现两次, score 加和 (fts 0.3 + vector 0.7*0.5 = 0.65)
    # 重复 id 的合并 score 应大于任一单源
    for r in merged:
        if r["id"] == "1":
            # merged 应该是 fts 的 0.3 + vec 的 0.7*0.5 = 0.65
            assert abs(r["score"] - 0.65) < 0.01


# ─── 7. fused_search 纯 FTS 路径 (vector 挂时降级) ──────────────

def test_fused_search_degrades_when_vector_fails(tmp_path: Path, monkeypatch):
    """VectorIndex() 抛异常时, fused_search 应返 FTS 结果不崩."""
    from v4 import store as store_mod
    from v4 import search as search_mod

    _fresh_db(tmp_path)
    monkeypatch.setattr(search_mod, "_get_embedding", _fake_embedding)

    fake_fts_hit = store_mod.Memory(
        id=99, content="只 FTS", type="fact", tags="",
        weight=0.5, access_count=0, last_accessed=0.0, created=0.0,
        short_term=True, long_term=False,
    )
    monkeypatch.setattr(store_mod, "search", lambda *a, **kw: [fake_fts_hit])

    # VectorIndex 构造时抛异常 → fused_search 应 catch 走 FTS-only 路径
    with patch.object(search_mod, "VectorIndex", side_effect=RuntimeError("chroma down")):
        merged = search_mod.fused_search("降级测试", top_k=3)

    assert len(merged) == 1
    assert merged[0]["id"] == "99"
    assert merged[0]["source"] == "fts"
    assert merged[0]["score"] == pytest.approx(0.3)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))