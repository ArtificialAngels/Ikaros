"""
v4.tests.test_llm_client — LLM client 单元测试 (mock, 不真打 cloud)

覆盖:
  - call_llm 路由 (local vs deepseek)
  - DeepSeek API key 缺失时显式抛
  - HTTP 错误显式抛 (不吞)
  - 响应 shape 异常显式抛
  - has_api_key / stats 不泄露 key
  - JSON 响应解析 (Qwen3 思考模式回退)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401  (用于 type hints, runner 不依赖)

V4_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V4_ROOT.parent))


def test_stats_does_not_leak_key(monkeypatch=None):
    """stats() 不应暴露 API key 全文."""
    from v4.reflect import llm_client
    s = llm_client.stats()
    # 全文里不应有 sk-... 形式
    s_str = json.dumps(s)
    assert "sk-" not in s_str, f"stats() leaks key: {s_str}"
    # 应有 api_key_set bool
    assert "api_key_set" in s["deepseek"]


def test_has_api_key_returns_bool():
    from v4.reflect import llm_client
    result = llm_client.has_api_key()
    assert isinstance(result, bool)


def test_call_deepseek_without_api_key_raises():
    """无 API key 时应显式抛 RuntimeError, 不静默."""
    from v4.reflect import llm_client

    with patch.object(llm_client, "DEEPSEEK_API_KEY", ""):
        try:
            llm_client.call_llm("system", "user", provider="deepseek")
            assert False, "应该抛 RuntimeError"
        except RuntimeError as e:
            assert "DEEPSEEK_API_KEY" in str(e)


def test_call_deepseek_http_error_raises():
    """HTTP 4xx/5xx 应显式抛, 含 body 摘要."""
    from v4.reflect import llm_client

    fake_response = MagicMock()
    fake_response.status_code = 401
    fake_response.text = "Unauthorized: invalid api_key"

    with patch.object(llm_client, "DEEPSEEK_API_KEY", "sk-fake"), \
         patch.object(llm_client.httpx, "Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = fake_response
        fake_response.raise_for_status.side_effect = llm_client.httpx.HTTPStatusError(
            "401", request=MagicMock(), response=fake_response
        )
        try:
            llm_client.call_llm("system", "user", provider="deepseek")
            assert False, "应该抛 RuntimeError"
        except RuntimeError as e:
            assert "401" in str(e)
            assert "Unauthorized" in str(e)


def test_call_deepseek_success():
    """正常响应: 解析 content, 返 LLMResponse."""
    from v4.reflect import llm_client

    fake_data = {
        "choices": [
            {"message": {"role": "assistant", "content": "Hello, 哥哥"}}
        ]
    }
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = fake_data
    fake_response.raise_for_status.return_value = None

    with patch.object(llm_client, "DEEPSEEK_API_KEY", "sk-fake"), \
         patch.object(llm_client.httpx, "Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = fake_response
        resp = llm_client.call_llm("sys", "user", provider="deepseek", max_tokens=100)
        assert resp.provider == "deepseek"
        assert resp.model == "deepseek-v4-flash"  # 默认
        assert "Hello" in resp.content


def test_call_deepseek_empty_content_raises():
    """content 为空字符串应抛, 不返空响应."""
    from v4.reflect import llm_client

    fake_data = {"choices": [{"message": {"role": "assistant", "content": ""}}]}
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = fake_data
    fake_response.raise_for_status.return_value = None

    with patch.object(llm_client, "DEEPSEEK_API_KEY", "sk-fake"), \
         patch.object(llm_client.httpx, "Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = fake_response
        try:
            llm_client.call_llm("sys", "user", provider="deepseek")
            assert False, "应该抛"
        except RuntimeError as e:
            assert "empty" in str(e).lower()


def test_call_local_qwen3_thinking_mode_fallback():
    """Qwen3 思考模式: content 空时回退 reasoning_content."""
    from v4.reflect import llm_client

    fake_data = {
        "choices": [
            {"message": {
                "role": "assistant",
                "content": "",
                "reasoning_content": "thought: 1+1=2, so answer is 2",
            }}
        ]
    }
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = fake_data
    fake_response.raise_for_status.return_value = None

    with patch.object(llm_client.httpx, "Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = fake_response
        resp = llm_client.call_llm("sys", "user", provider="local")
        assert resp.provider == "local"
        assert "thought" in resp.content.lower()


def test_call_local_empty_both_raises():
    """Qwen3 content + reasoning_content 都空 → 抛."""
    from v4.reflect import llm_client

    fake_data = {
        "choices": [{"message": {"role": "assistant", "content": "", "reasoning_content": ""}}]
    }
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = fake_data
    fake_response.raise_for_status.return_value = None

    with patch.object(llm_client.httpx, "Client") as mock_client:
        mock_client.return_value.__enter__.return_value.post.return_value = fake_response
        try:
            llm_client.call_llm("sys", "user", provider="local")
            assert False, "应该抛"
        except RuntimeError as e:
            assert "empty" in str(e).lower()


def test_unknown_provider_raises():
    from v4.reflect import llm_client
    try:
        llm_client.call_llm("sys", "user", provider="invalid")  # type: ignore
        assert False, "应该抛 ValueError"
    except ValueError as e:
        assert "unknown provider" in str(e)


# ─── runner ─────────────────────────────────────────────────────

def _run_all_tests():
    import inspect
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
            if "monkeypatch" in sig.parameters:
                # 不传 monkeypatch (我们没装 pytest)
                continue
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception as e:
            failed.append((name, e))
            print(f"  FAIL  {name}: {e}")
    print(f"\n{passed}/{len(tests)} tests passed (跳过需 monkeypatch 的)")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all_tests())
