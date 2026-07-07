"""
v4.tests.test_store — V4 store 单元测试

V3 store 没单测, V4 强制覆盖.
"""

from __future__ import annotations

import sys
from pathlib import Path

V4_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V4_ROOT.parent))


def _fresh_db(tmp_path: Path):
    """让 v4.store 写到 tmp_path 而不是默认 Ikaros-memory/data/v4/.

    V4 关键: 必须 close 当前 thread 连接, 否则下个 conn() 复用旧连接, 数据写到旧 db.
    V3 也有这 bug (module-level _conn 跨 test 污染).
    V4 fix: thread-local _tls.c, close() 重置.

    注意: 必须改 V4_DATA_DIR / V4_DB_PATH / close() 三件套:
      - V4_DATA_DIR: conn() 内部 mkdir 用
      - V4_DB_PATH: conn() 内部 sqlite3.connect 用
      - close(): 重置 thread-local _tls.c
    """
    import v4.store as store_mod
    fresh_dir = tmp_path / "v4_data"
    fresh_dir.mkdir(parents=True, exist_ok=True)
    # 同时改模块级 V4_DATA_DIR (不是 _V4_DATA_DIR!) 和 V4_DB_PATH
    store_mod.V4_DATA_DIR = fresh_dir
    store_mod.V4_DB_PATH = fresh_dir / "v4.db"
    store_mod.close()
    return store_mod


def test_store_creates_db(tmp_path: Path):
    mod = _fresh_db(tmp_path)
    mid = mod.store("hello v4", type="fact", weight=0.7)
    assert isinstance(mid, int) and mid > 0
    assert mod.V4_DB_PATH.exists()
    assert mod.V4_DB_PATH.stat().st_size > 0


def test_store_get_roundtrip(tmp_path: Path):
    mod = _fresh_db(tmp_path)
    mid = mod.store("roundtrip test", type="lesson", weight=0.8, tags="test,v4")
    m = mod.get(mid)
    assert m is not None
    assert m.id == mid
    assert m.content == "roundtrip test"
    assert m.type == "lesson"
    assert m.weight == 0.8
    assert "test" in m.tags
    assert m.short_term is True
    assert m.long_term is False


def test_store_clamps_weight(tmp_path: Path):
    """weight 应 clamp 到 [0, 1]."""
    mod = _fresh_db(tmp_path)
    m_high = mod.store("over 1", weight=2.0)
    m_low = mod.store("under 0", weight=-0.5)
    assert mod.get(m_high).weight == 1.0
    assert mod.get(m_low).weight == 0.0


def test_search_finds_recent(tmp_path: Path):
    mod = _fresh_db(tmp_path)
    mod.store("哥哥喜欢 terse 中文", type="preference", weight=0.9, tags="v4,test")
    mod.store("chromaDB 在 portable-python", type="fact", weight=0.7, tags="v4,test")
    mod.store("v4 phase 2 跑通", type="fact", weight=0.8, tags="v4,phase2")

    hits = mod.search("v4", top_k=5)
    assert len(hits) >= 2
    contents = {h.content for h in hits}
    assert any("phase 2" in c for c in contents)
    assert any("chromaDB" in c for c in contents)


def test_search_min_weight_filter(tmp_path: Path):
    mod = _fresh_db(tmp_path)
    mod.store("high weight", weight=0.9)
    mod.store("low weight", weight=0.3)
    hits = mod.search("weight", top_k=10, min_weight=0.5)
    contents = {h.content for h in hits}
    assert "high weight" in contents
    assert "low weight" not in contents


def test_access_increments_weight(tmp_path: Path):
    mod = _fresh_db(tmp_path)
    mid = mod.store("access test", weight=0.5)
    m0 = mod.get(mid)
    mod.access(mid)
    m1 = mod.get(mid)
    assert m1.access_count == m0.access_count + 1
    assert m1.weight == pytest_approx_or_leq(m0.weight + 0.05)
    assert m1.last_accessed >= m0.last_accessed


def pytest_approx_or_leq(expected: float):
    """helper: weight 加完后应 <= 1.0, 这里放宽到 >= expected."""
    return expected  # for assertEqual compat


def test_delete_returns_bool(tmp_path: Path):
    mod = _fresh_db(tmp_path)
    mid = mod.store("delete me", weight=0.5)
    assert mod.delete(mid) is True
    assert mod.delete(mid) is False  # 第二次删, 找不到
    assert mod.get(mid) is None


def test_list_all_with_type_filter(tmp_path: Path):
    mod = _fresh_db(tmp_path)
    mod.store("a1", type="fact")
    mod.store("a2", type="fact")
    mod.store("b1", type="lesson")
    facts = mod.list_all(limit=10, type_filter="fact")
    assert len(facts) == 2
    lessons = mod.list_all(limit=10, type_filter="lesson")
    assert len(lessons) == 1


def test_stats_shape(tmp_path: Path):
    mod = _fresh_db(tmp_path)
    mod.store("s1", type="fact", weight=0.7)
    mod.store("s2", type="fact", weight=0.9)
    s = mod.stats()
    assert s["total"] == 2
    assert "by_type" in s
    assert "fact" in s["by_type"]
    assert s["by_type"]["fact"]["count"] == 2


# ─── runner ─────────────────────────────────────────────────────

def _run_all_tests():
    import inspect
    import os
    os.makedirs("./.tmp_test_v4", exist_ok=True)
    tests = [
        (name, fn)
        for name, fn in globals().items()
        if name.startswith("test_") and callable(fn)
    ]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            sig = inspect.signature(fn)
            if "tmp_path" in sig.parameters:
                # 传 Path 对象, 不是字符串
                tp = Path("./.tmp_test_v4") / name
                fn(tmp_path=tp)  # type: ignore
            else:
                fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception as e:
            failed.append((name, e))
            print(f"  FAIL  {name}: {e}")
    print(f"\n{passed}/{len(tests)} tests passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    import os
    os.makedirs("./.tmp_test_v4", exist_ok=True)
    sys.exit(_run_all_tests())
