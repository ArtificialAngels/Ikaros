"""
v4.tests.test_migrate — V3→V4 迁移测试 (mock V3 db, 真 V4 db)

覆盖:
  - 1:1 字段保持 (content, type, tags, weight, access_count, last_accessed, created)
  - 跳过低 weight (< threshold)
  - dedup 跳过已存在 content
  - V3 db 不被改 (read-only URI)
  - dry_run 不写入
  - 显式错误: V3 不存在
  - 自定义 threshold
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

V4_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V4_ROOT.parent))


# ─── helpers ──────────────────────────────────────────────────

def _make_mock_v3_db(path: Path, memories: list[dict]) -> Path:
    """建一个 mock V3 db, 含指定 memories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with sqlite3.connect(str(path)) as c:
        c.executescript("""
            CREATE TABLE memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'fact',
                tags TEXT DEFAULT '',
                weight REAL NOT NULL DEFAULT 0.6,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed REAL NOT NULL DEFAULT 0,
                created REAL NOT NULL DEFAULT (strftime('%s','now')),
                short_term INTEGER NOT NULL DEFAULT 1,
                long_term INTEGER NOT NULL DEFAULT 0
            );
        """)
        for m in memories:
            c.execute(
                "INSERT INTO memory (content, type, tags, weight, "
                "access_count, last_accessed, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    m["content"],
                    m.get("type", "fact"),
                    m.get("tags", ""),
                    m.get("weight", 0.6),
                    m.get("access_count", 0),
                    m.get("last_accessed", 0.0),
                    m.get("created", time.time()),
                ),
            )
        c.commit()
    return path


def _fresh_v4_db(tmp_path: Path):
    """让 v4.store 写到 tmp_path."""
    import v4.store as mod
    fresh_dir = tmp_path / "v4_data"
    fresh_dir.mkdir(parents=True, exist_ok=True)
    mod.V4_DATA_DIR = fresh_dir
    mod.V4_DB_PATH = fresh_dir / "v4.db"
    mod.close()
    return mod


# ─── 字段保持测试 ─────────────────────────────────────────────

def test_migrate_1_to_1_field_preservation(tmp_path):
    """所有字段 1:1 保持 (content/type/tags/weight/access_count/last_accessed/created)."""
    from v4.migrate_from_v3 import migrate

    v3_path = tmp_path / "v3.db"
    now = time.time()
    _make_mock_v3_db(v3_path, [
        {"content": "测试 1", "type": "fact", "tags": "test", "weight": 0.9,
         "access_count": 5, "last_accessed": now - 100, "created": now - 1000},
        {"content": "测试 2", "type": "lesson", "tags": "test,v3",
         "weight": 0.7, "access_count": 2, "last_accessed": now - 50, "created": now - 500},
    ])
    v4_path = tmp_path / "v4_data" / "v4.db"
    _fresh_v4_db(tmp_path)

    result = migrate(v3_path=v3_path, v4_path=v4_path, threshold=0.5)
    assert result["error"] is None
    assert result["inserted_to_v4"] == 2
    assert result["above_threshold"] == 2

    # 验 V4 db 内容 1:1
    import v4.store as mod
    mems = mod.list_all(limit=100)
    by_content = {m.content: m for m in mems}
    assert "测试 1" in by_content
    assert "测试 2" in by_content
    m1 = by_content["测试 1"]
    assert m1.type == "fact"
    assert m1.weight == 0.9
    assert m1.access_count == 5
    # tags 可能有 v4 前缀 (没有, 因为 migrate 不加)
    assert m1.tags == "test"
    # created 误差 < 1s
    assert abs(m1.created - (now - 1000)) < 1.0


# ─── threshold 测试 ──────────────────────────────────────────

def test_migrate_skips_below_threshold(tmp_path):
    """weight < threshold 跳过."""
    from v4.migrate_from_v3 import migrate

    v3_path = tmp_path / "v3.db"
    _make_mock_v3_db(v3_path, [
        {"content": "高质量", "weight": 0.9},
        {"content": "中等", "weight": 0.6},
        {"content": "低质量", "weight": 0.4},
        {"content": "低质量 2", "weight": 0.49},
    ])
    v4_path = tmp_path / "v4_data" / "v4.db"
    _fresh_v4_db(tmp_path)

    result = migrate(v3_path=v3_path, v4_path=v4_path, threshold=0.5)
    assert result["above_threshold"] == 2  # 0.9, 0.6
    assert result["skipped_low_weight"] == 2  # 0.4, 0.49
    assert result["inserted_to_v4"] == 2


def test_migrate_threshold_boundary(tmp_path):
    """weight == threshold 边界: 0.5 保留 (>=)."""
    from v4.migrate_from_v3 import migrate

    v3_path = tmp_path / "v3.db"
    _make_mock_v3_db(v3_path, [
        {"content": "边界 0.5", "weight": 0.5},
        {"content": "边界 0.49", "weight": 0.49},
    ])
    v4_path = tmp_path / "v4_data" / "v4.db"
    _fresh_v4_db(tmp_path)

    result = migrate(v3_path=v3_path, v4_path=v4_path, threshold=0.5)
    assert result["inserted_to_v4"] == 1  # 只 0.5
    assert result["skipped_low_weight"] == 1


# ─── dedup 测试 ──────────────────────────────────────────────

def test_migrate_dedup_skips_existing(tmp_path):
    """V4 已有同 content → 跳过."""
    from v4.migrate_from_v3 import migrate

    v3_path = tmp_path / "v3.db"
    _make_mock_v3_db(v3_path, [
        {"content": "重复 1", "weight": 0.8},
        {"content": "新内容", "weight": 0.8},
    ])
    v4_path = tmp_path / "v4_data" / "v4.db"
    _fresh_v4_db(tmp_path)
    # 先存 1 条到 V4
    import v4.store as mod
    mod.store("重复 1", type="fact", weight=0.7)

    result = migrate(v3_path=v3_path, v4_path=v4_path, threshold=0.5)
    # V3 两条都 above threshold, 但 V4 已有 "重复 1" → 跳过
    assert result["above_threshold"] == 2
    assert result["inserted_to_v4"] == 1  # 只 "新内容" 插入

    # V4 应有 2 条 (已有 1 + 新插 1)
    all_mems = mod.list_all(limit=100)
    contents = {m.content for m in all_mems}
    assert "重复 1" in contents
    assert "新内容" in contents
    assert len(all_mems) == 2


# ─── V3 db 只读测试 ────────────────────────────────────────

def test_migrate_does_not_modify_v3(tmp_path):
    """迁移后 V3 db 大小 / 行数 不变."""
    from v4.migrate_from_v3 import migrate

    v3_path = tmp_path / "v3.db"
    _make_mock_v3_db(v3_path, [
        {"content": f"test {i}", "weight": 0.7} for i in range(5)
    ])
    v3_size_before = v3_path.stat().st_size
    v4_path = tmp_path / "v4_data" / "v4.db"
    _fresh_v4_db(tmp_path)

    result = migrate(v3_path=v3_path, v4_path=v4_path, threshold=0.5)
    assert result["inserted_to_v4"] == 5

    # V3 db 大小应不变 (read-only URI)
    v3_size_after = v3_path.stat().st_size
    assert v3_size_after == v3_size_before

    # V3 行数应不变
    with sqlite3.connect(str(v3_path)) as c:
        n = c.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
    assert n == 5


# ─── dry-run 测试 ───────────────────────────────────────────

def test_migrate_dry_run_does_not_write(tmp_path):
    """dry-run 不写入 V4."""
    from v4.migrate_from_v3 import migrate

    v3_path = tmp_path / "v3.db"
    _make_mock_v3_db(v3_path, [
        {"content": "a", "weight": 0.9},
        {"content": "b", "weight": 0.7},
    ])
    v4_path = tmp_path / "v4_data" / "v4.db"
    _fresh_v4_db(tmp_path)

    result = migrate(v3_path=v3_path, v4_path=v4_path, threshold=0.5, dry_run=True)
    assert result["dry_run"] is True
    assert result["inserted_to_v4"] == 2  # 计入, 但实际不写

    # V4 db 应为空
    import v4.store as mod
    all_mems = mod.list_all(limit=100)
    assert len(all_mems) == 0


# ─── 错误处理测试 ───────────────────────────────────────────

def test_migrate_v3_not_found(tmp_path):
    """V3 db 不存在 → 显式 FileNotFoundError."""
    from v4.migrate_from_v3 import migrate

    v3_path = tmp_path / "nope.db"  # 不存在
    v4_path = tmp_path / "v4_data" / "v4.db"
    _fresh_v4_db(tmp_path)

    result = migrate(v3_path=v3_path, v4_path=v4_path, threshold=0.5)
    assert result["error"] is not None
    assert "not found" in result["error"].lower() or "V3" in result["error"]


# ─── 真实 V3 → V4 模拟 ──────────────────────────────────────

def test_migrate_realistic_v3_sample(tmp_path):
    """模拟真实 V3: 混合 8 种 type, 各种 weight."""
    from v4.migrate_from_v3 import migrate

    v3_path = tmp_path / "v3.db"
    sample = [
        {"content": "axiom 1", "type": "axiom", "weight": 0.7},
        {"content": "identity 1", "type": "identity", "weight": 0.9},
        {"content": "rule 1", "type": "rule", "weight": 0.8},
        {"content": "preference 1", "type": "preference", "weight": 0.9},
        {"content": "fact 1", "type": "fact", "weight": 0.8},
        {"content": "fact 2", "type": "fact", "weight": 0.6},
        {"content": "lesson 1", "type": "lesson", "weight": 0.7},
        {"content": "decision 1", "type": "decision", "weight": 0.7},
        {"content": "conversation 1", "type": "conversation", "weight": 0.5},
        {"content": "低 weight 跳过", "type": "fact", "weight": 0.3},
        {"content": "中低 weight 跳过", "type": "fact", "weight": 0.49},
    ]
    _make_mock_v3_db(v3_path, sample)
    v4_path = tmp_path / "v4_data" / "v4.db"
    _fresh_v4_db(tmp_path)

    result = migrate(v3_path=v3_path, v4_path=v4_path, threshold=0.5)
    assert result["v3_total"] == 11
    assert result["above_threshold"] == 9  # 跳过 0.3 和 0.49
    assert result["skipped_low_weight"] == 2
    assert result["inserted_to_v4"] == 9
    assert result["error"] is None

    # 验 V4 8 种 type 全有
    import v4.store as mod
    by_type = {}
    for m in mod.list_all(limit=100):
        by_type.setdefault(m.type, 0)
        by_type[m.type] += 1
    assert "axiom" in by_type
    assert "identity" in by_type
    assert "rule" in by_type
    assert "preference" in by_type
    assert "fact" in by_type
    assert "lesson" in by_type
    assert "decision" in by_type
    assert "conversation" in by_type


# ─── runner ─────────────────────────────────────────────────────

def _run_all_tests():
    import inspect
    import os
    os.makedirs("./.tmp_test_migrate", exist_ok=True)
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
                tp = Path("./.tmp_test_migrate") / name
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
    os.makedirs("./.tmp_test_migrate", exist_ok=True)
    sys.exit(_run_all_tests())
