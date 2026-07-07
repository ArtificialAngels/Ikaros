"""
v4.tests.test_consolidate — consolidate 单元测试 (mock LLM, 不真打 cloud)

覆盖:
  - 小模型提取成功 → 大模型验证 → 存
  - 大模型验证失败 → 降级到 _fallback_filter
  - 大模型不可用 → 走本地验证
  - 空 batch → 返 0
  - V3 行为兼容 (老 V3 调 consolidate_conversations 也跑)
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


def _seed_conversations(mod, n: int = 3):
    """插入 n 条未整合的 conversation."""
    ids = []
    for i in range(n):
        mid = mod.store(
            f"[对话{i+1}] Q: 第{i+1}个问题\nA: 第{i+1}个回答",
            type="conversation",
            weight=0.5,
            tags="raw",
        )
        ids.append(mid)
    return ids


def _patch_llm_extract(facts: list[dict]):
    """mock 小模型提取, 返指定 facts."""
    from v4.reflect import llm_client
    fake = MagicMock()
    fake.content = json.dumps(facts, ensure_ascii=False)
    fake.provider = "local"
    fake.model = "qwen3-8b"
    fake.elapsed_sec = 0.1
    fake.raw = None
    return patch.object(llm_client, "call_llm", return_value=fake)


def _patch_llm_verify(verdicts: list[dict]):
    """mock 大模型验证, 返指定 verdicts."""
    from v4.reflect import llm_client
    fake = MagicMock()
    fake.content = json.dumps(verdicts, ensure_ascii=False)
    fake.provider = "deepseek"
    fake.model = "deepseek-v4-flash"
    fake.elapsed_sec = 0.1
    fake.raw = None
    return patch.object(llm_client, "call_llm", return_value=fake)


def test_consolidate_empty_batch(tmp_path):
    """没有未整合的 conversation → 返 0."""
    from v4.reflect import consolidate
    mod = _fresh_store(tmp_path)
    with _patch_llm_extract([{"content": "should not be called", "type": "fact", "weight": 0.5}]):
        result = consolidate.consolidate_conversations()
    assert result["consolidated"] == 0
    assert result["verified_by"] is None
    assert result["error"] is None


def test_consolidate_happy_path(tmp_path):
    """小模型提取 3 条 → 大模型验证全 KEEP → 存 3 条, 删 3 原始."""
    from v4.reflect import consolidate
    from v4.reflect import llm_client
    mod = _fresh_store(tmp_path)
    _seed_conversations(mod, n=3)
    facts = [
        {"content": "哥哥喜欢 terse 中文", "type": "preference", "weight": 0.9},
        {"content": "v4 phase 3 走 deepseek", "type": "fact", "weight": 0.7},
        {"content": "记忆模块要反思", "type": "lesson", "weight": 0.8},
    ]
    verdicts = [{"index": 0, "verdict": "KEEP"},
                {"index": 1, "verdict": "KEEP"},
                {"index": 2, "verdict": "KEEP"}]
    # 用 side_effect 列表: 第 1 次调 = 提取 mock, 第 2 次调 = 验证 mock
    extract_resp = MagicMock()
    extract_resp.content = json.dumps(facts, ensure_ascii=False)
    extract_resp.provider = "local"
    extract_resp.model = "qwen3-8b"
    verify_resp = MagicMock()
    verify_resp.content = json.dumps(verdicts, ensure_ascii=False)
    verify_resp.provider = "deepseek"
    verify_resp.model = "deepseek-v4-flash"
    with patch.object(llm_client, "call_llm",
                      side_effect=[extract_resp, verify_resp]), \
         patch.object(llm_client, "has_api_key", return_value=True):
        result = consolidate.consolidate_conversations()
    assert result["consolidated"] == 3
    assert result["verified_by"] == "deepseek"
    assert result["error"] is None
    # 原始 conversation 应被删
    remaining = mod.list_all(limit=100, type_filter="conversation")
    assert len(remaining) == 0


def test_consolidate_big_verify_drops_some(tmp_path):
    """大模型 drop 一半 → 只存 keep 的."""
    from v4.reflect import consolidate
    from v4.reflect import llm_client
    mod = _fresh_store(tmp_path)
    _seed_conversations(mod, n=2)
    facts = [
        {"content": "好的记忆", "type": "fact", "weight": 0.8},
        {"content": "垃圾记忆", "type": "fact", "weight": 0.3},
    ]
    extract_resp = MagicMock()
    extract_resp.content = json.dumps(facts, ensure_ascii=False)
    extract_resp.provider = "local"
    verdicts = [{"index": 0, "verdict": "KEEP"},
                {"index": 1, "verdict": "DROP"}]
    verify_resp = MagicMock()
    verify_resp.content = json.dumps(verdicts, ensure_ascii=False)
    verify_resp.provider = "deepseek"
    with patch.object(llm_client, "call_llm",
                      side_effect=[extract_resp, verify_resp]), \
         patch.object(llm_client, "has_api_key", return_value=True):
        result = consolidate.consolidate_conversations()
    assert result["consolidated"] == 1
    assert result["verified_by"] == "deepseek"


def test_consolidate_big_llm_fails_fallback(tmp_path):
    """大模型失败 → 降级 _fallback_filter (按 weight 保留前 50%)."""
    from v4.reflect import consolidate
    from v4.reflect import llm_client
    mod = _fresh_store(tmp_path)
    _seed_conversations(mod, n=2)
    facts = [
        {"content": "高质量", "type": "fact", "weight": 0.9},
        {"content": "低质量", "type": "fact", "weight": 0.3},
    ]
    # 小模型提取 OK, 大模型抛
    def fake_call(system, user, *, provider="local", **kwargs):
        if provider == "local":
            f = MagicMock()
            f.content = json.dumps(facts, ensure_ascii=False)
            f.provider = "local"
            f.model = "qwen3-8b"
            f.elapsed_sec = 0.1
            return f
        elif provider == "deepseek":
            raise RuntimeError("API 限流")
        raise ValueError(provider)
    with patch.object(llm_client, "call_llm", side_effect=fake_call), \
         patch.object(llm_client, "has_api_key", return_value=True):
        result = consolidate.consolidate_conversations()
    # 2 条 → 保留前 1 条 (50% of 2 = 1)
    assert result["consolidated"] == 1
    assert result["verified_by"] == "deepseek"  # 仍说 deepseek (失败后回退)


def test_consolidate_no_api_key_uses_local(tmp_path):
    """无 API key → 走本地验证路径."""
    from v4.reflect import consolidate
    from v4.reflect import llm_client
    mod = _fresh_store(tmp_path)
    _seed_conversations(mod, n=2)
    facts = [
        {"content": "A", "type": "fact", "weight": 0.7},
        {"content": "B", "type": "fact", "weight": 0.5},
    ]
    # 第一次 mock: 小模型提取, 第二次 mock: 本地验证
    extract_resp = MagicMock()
    extract_resp.content = json.dumps(facts, ensure_ascii=False)
    extract_resp.provider = "local"
    verify_resp = MagicMock()
    verify_resp.content = json.dumps(
        [{"index": 0, "verdict": "KEEP"}, {"index": 1, "verdict": "KEEP"}],
        ensure_ascii=False,
    )
    verify_resp.provider = "local"
    with patch.object(llm_client, "call_llm", side_effect=[extract_resp, verify_resp]), \
         patch.object(llm_client, "has_api_key", return_value=False):
        result = consolidate.consolidate_conversations()
    assert result["consolidated"] == 2
    assert result["verified_by"] == "local"


def test_consolidate_extraction_fails_deletes_raw(tmp_path):
    """小模型提取失败 → 删原始 conversation (V3 line 224-225 一致)."""
    from v4.reflect import consolidate
    from v4.reflect import llm_client
    mod = _fresh_store(tmp_path)
    _seed_conversations(mod, n=3)
    with patch.object(llm_client, "call_llm",
                      side_effect=RuntimeError("本地模型挂了")):
        result = consolidate.consolidate_conversations()
    assert result["consolidated"] == 0
    assert result["error"] is not None
    # 原始应被删
    remaining = mod.list_all(limit=100, type_filter="conversation")
    assert len(remaining) == 0


# ─── runner ─────────────────────────────────────────────────────

def _run_all_tests():
    import inspect
    import os
    os.makedirs("./.tmp_test_consolidate", exist_ok=True)
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
                tp = Path("./.tmp_test_consolidate") / name
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
    os.makedirs("./.tmp_test_consolidate", exist_ok=True)
    sys.exit(_run_all_tests())
