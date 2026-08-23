"""对话树底座语义 (2026-08-23): deepseek-harness (dsh).

覆盖 /api/health 与 /api/providers 对外声明底座 = deepseek-harness,
对话树推理经 DeepSeek API (与 dsh 同源); 密钥不进响应。
"""
from __future__ import annotations

from conftest import http_get  # noqa: E402


def test_health_declares_dsh_base(http_server):
    st, h = http_get(http_server, "/api/health")
    assert st == 200
    assert h["base"] == "deepseek-harness"
    assert isinstance(h.get("deepseek_key"), bool)
    assert h.get("model")


def test_providers_declare_deepseek_harness(http_server):
    st, p = http_get(http_server, "/api/providers")
    assert st == 200
    assert p["base"] == "deepseek-harness"
    provs = p.get("providers") or []
    assert provs and provs[0]["id"] == "deepseek-harness"
    assert provs[0]["configured"] is True or provs[0]["configured"] is False
    assert "sk-" not in str(p), "密钥不得出现在 providers 响应"