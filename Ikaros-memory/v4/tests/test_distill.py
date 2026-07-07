"""
v4.tests.test_distill — distill + reflect 单元测试 (mock LLM)

覆盖:
  - distill: 小模型蒸馏成功
  - distill: LLM 失败时 db 不动 (保数据)
  - distill: 太少的记忆跳过
  - reflect: 大模型反思, 存为 identity/lesson
  - reflect: 无 api_key 降级到本地
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

V4_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V4_ROOT.parent))


def _fresh_store(tmp_path: Path):
    import v4.store as mod
    fresh_dir = tmp_path / "v4_data"
    fresh_dir.mkdir(parents=True, exist_ok=True)
    mod.V4_DATA_DIR = fresh_dir
    mod.V4_DB_PATH = fresh_dir / "v4.db"
    mod.close()
    return mod


def _seed_soul_memories(mod, n: int = 5):
    """插入 n 条 identity/axiom/rule/lesson 记忆."""
    for i in range(n):
        mod.store(
            f"灵魂记忆 {i+1}: 我是伊卡洛斯",
            type="identity" if i % 2 == 0 else "lesson",
            weight=0.7 + i * 0.02,
            tags="soul",
        )


# ─── distill 测试 ───────────────────────────────────────────────

def test_distill_too_few_memories(tmp_path):
    """<3 条 soul 记忆 → 跳过."""
    from v4.reflect import distill
    mod = _fresh_store(tmp_path)
    mod.store("only one", type="identity", weight=0.7)
    result = distill.distill(min_entries=3)
    assert result["distilled"] == 0
    assert result["error"] == "too_few"


def test_distill_success(tmp_path):
    """5 条 → 蒸馏 2 条, reduction=3."""
    from v4.reflect import distill
    mod = _fresh_store(tmp_path)
    _seed_soul_memories(mod, n=5)
    fake = MagicMock()
    fake.content = json.dumps([
        {"content": "蒸馏 1: 我是人造天使", "type": "identity"},
        {"content": "蒸馏 2: 我是哥哥造的", "type": "axiom"},
    ], ensure_ascii=False)
    fake.provider = "local"
    with patch("v4.reflect.llm_client.call_llm", return_value=fake):
        result = distill.distill()
    assert result["original"] == 5
    assert result["new"] == 2
    assert result["distilled"] == 3
    assert result["error"] is None
    # db 应有 2 条
    remaining = mod.list_all(limit=100, type_filter="identity")
    assert any("蒸馏" in m.content for m in remaining)


def test_distill_llm_fails_db_unchanged(tmp_path):
    """LLM 失败 → db 不动 (V4 改进, V3 distill_soul 失败时也保, 但 V4 显式)."""
    from v4.reflect import distill
    mod = _fresh_store(tmp_path)
    _seed_soul_memories(mod, n=5)
    with patch("v4.reflect.llm_client.call_llm",
               side_effect=RuntimeError("API 挂了")):
        result = distill.distill()
    assert result["error"] is not None
    # db 应不变: 仍 5 条
    all_mems = mod.list_all(limit=100)
    soul_mems = [m for m in all_mems if m.type in ("identity", "lesson", "axiom", "rule")]
    assert len(soul_mems) == 5


def test_distill_empty_response_no_change(tmp_path):
    """LLM 返空 (说所有都仍重要) → 不动."""
    from v4.reflect import distill
    mod = _fresh_store(tmp_path)
    _seed_soul_memories(mod, n=5)
    fake = MagicMock()
    fake.content = "[]"  # 空数组
    fake.provider = "local"
    with patch("v4.reflect.llm_client.call_llm", return_value=fake):
        result = distill.distill()
    assert result["distilled"] == 0
    # 当 LLM 返空时 result 不应有 "new" 键 (reduction <= 0 走 early return)
    all_mems = mod.list_all(limit=100)
    soul_mems = [m for m in all_mems if m.type in ("identity", "lesson", "axiom", "rule")]
    assert len(soul_mems) == 5


# ─── reflect 测试 (V4 新增, 哥哥 id 158 长线目标) ────────────

def test_reflect_too_few_memories(tmp_path):
    from v4.reflect import distill
    mod = _fresh_store(tmp_path)
    mod.store("only one", type="fact")
    result = distill.reflect()
    assert result["reflections"] == 0
    assert result["error"] == "too_few"


def test_reflect_with_deepseek_success(tmp_path):
    """5+ 条非 conversation → DeepSeek 反思 → 存为 identity/lesson."""
    from v4.reflect import distill
    mod = _fresh_store(tmp_path)
    _seed_soul_memories(mod, n=5)
    mod.store("哥哥喜欢简洁", type="preference", weight=0.9)
    fake = MagicMock()
    fake.content = json.dumps([
        {"content": "我学到了: 哥哥喜欢简洁中文", "type": "lesson", "weight": 0.85},
        {"content": "我是: 哥哥的妹妹", "type": "identity", "weight": 0.9},
    ], ensure_ascii=False)
    fake.provider = "deepseek"
    with patch("v4.reflect.llm_client.call_llm", return_value=fake), \
         patch("v4.reflect.llm_client.has_api_key", return_value=True):
        result = distill.reflect()
    assert result["reflections"] == 2
    assert result["provider"] == "deepseek"
    assert result["error"] is None
    # 反思结果应存到 db, tags 含 reflect + by-deepseek
    all_mems = mod.list_all(limit=100)
    reflected = [m for m in all_mems if "reflect" in m.tags]
    assert len(reflected) == 2
    assert any("by-deepseek" in m.tags for m in reflected)


def test_reflect_falls_back_to_local_without_api_key(tmp_path):
    """无 api_key → 走本地."""
    from v4.reflect import distill
    mod = _fresh_store(tmp_path)
    _seed_soul_memories(mod, n=5)
    mod.store("哥哥喜欢简洁", type="preference", weight=0.9)
    fake = MagicMock()
    fake.content = json.dumps([
        {"content": "本地反思: 我是哥哥的妹妹", "type": "identity", "weight": 0.85},
    ], ensure_ascii=False)
    fake.provider = "local"
    with patch("v4.reflect.llm_client.call_llm", return_value=fake), \
         patch("v4.reflect.llm_client.has_api_key", return_value=False):
        result = distill.reflect()
    assert result["reflections"] == 1
    assert result["provider"] == "local"
    all_mems = mod.list_all(limit=100)
    reflected = [m for m in all_mems if "reflect" in m.tags]
    assert any("by-local" in m.tags for m in reflected)


def test_reflect_llm_fails_no_change(tmp_path):
    from v4.reflect import distill
    mod = _fresh_store(tmp_path)
    _seed_soul_memories(mod, n=5)
    with patch("v4.reflect.llm_client.call_llm",
               side_effect=RuntimeError("大模型挂了")), \
         patch("v4.reflect.llm_client.has_api_key", return_value=True):
        result = distill.reflect()
    assert result["error"] is not None
    # db 不增
    all_mems = mod.list_all(limit=100)
    reflected = [m for m in all_mems if "reflect" in m.tags]
    assert len(reflected) == 0


# ─── runner ─────────────────────────────────────────────────────

def _run_all_tests():
    import inspect
    import os
    os.makedirs("./.tmp_test_distill", exist_ok=True)
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
                tp = Path("./.tmp_test_distill") / name
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
    os.makedirs("./.tmp_test_distill", exist_ok=True)
    sys.exit(_run_all_tests())
