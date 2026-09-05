"""test_dsh_shared.py -- conversation-tree _dsh_shared module.

CT reads dsh's settings.yaml + .credentials.yaml to share LLM config
with dsh-web (no drift between the two clients). These tests cover the
resolution paths without touching the real ~/.dsh.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CT_DIR = _HERE.parent
_CORE = _CT_DIR.parent  # E:/Ikaros/core

# Make _dsh_shared importable as a module (server.py imports it relatively
# later; here we load it directly by path).
_DSH_SHARED_PATH = _CT_DIR / "_dsh_shared.py"
spec = importlib.util.spec_from_file_location("_dsh_shared", _DSH_SHARED_PATH)
dsh_shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dsh_shared)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dsh_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point _dsh_shared at a temp directory for isolated yaml files."""
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    # Reset module-level cache so tests don't see stale data
    dsh_shared.refresh_active_llm_cache()
    yield tmp_path
    dsh_shared.refresh_active_llm_cache()


def _write_yaml(path: Path, data) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


# ── Tests ───────────────────────────────────────────────────────────────────


def test_resolve_dsh_home_prefers_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    assert dsh_shared.resolve_dsh_home() == tmp_path.resolve()


def test_resolve_dsh_home_falls_back_to_dot_dsh(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DSH_HOME", raising=False)
    home = dsh_shared.resolve_dsh_home()
    assert home.name == ".dsh"


def test_read_yaml_returns_empty_on_missing(tmp_dsh_home: Path):
    assert dsh_shared.read_yaml(tmp_dsh_home / "missing.yaml") == {}


def test_read_yaml_returns_empty_on_invalid(tmp_dsh_home: Path):
    p = tmp_dsh_home / "bad.yaml"
    p.write_text(":\n  :\n: not valid", encoding="utf-8")
    assert dsh_shared.read_yaml(p) == {}


def test_read_dsh_settings_round_trip(tmp_dsh_home: Path):
    _write_yaml(
        tmp_dsh_home / "settings.yaml",
        {
            "llm-pi-ai": {
                "providers": {
                    "opencode-go": {
                        "apiKeyEnv": "OPENCODE_GO_API_KEY",
                        "baseURL": "https://api.example.com/v1",
                        "models": [{"id": "test-model", "contextWindow": 100000}],
                    }
                }
            },
            "agent-default-model": {"provider": "opencode-go", "model": "test-model"},
        },
    )
    s = dsh_shared.read_dsh_settings()
    assert "llm-pi-ai" in s
    assert s["agent-default-model"]["model"] == "test-model"


def test_get_active_llm_resolves_opencode_provider(tmp_dsh_home: Path):
    _write_yaml(
        tmp_dsh_home / "settings.yaml",
        {
            "llm-pi-ai": {
                "providers": {
                    "opencode-go": {
                        "apiKeyEnv": "OPENCODE_GO_API_KEY",
                        "baseURL": "https://api.example.com/v1",
                        "models": [
                            {"id": "mimo-v2.5-free", "name": "Mimo", "contextWindow": 200000, "maxTokens": 32000},
                            {"id": "deepseek-v4-flash", "name": "DS", "contextWindow": 1000000, "maxTokens": 384000},
                        ],
                    }
                }
            },
            "agent-default-model": {"provider": "opencode-go", "model": "mimo-v2.5-free"},
        },
    )
    _write_yaml(
        tmp_dsh_home / ".credentials.yaml",
        {"version": 1, "refs": {"OPENCODE_GO_API_KEY": "sk-test-12345"}},
    )
    result = dsh_shared.get_active_llm()
    assert result["provider"] == "opencode-go"
    assert result["model"] == "mimo-v2.5-free"
    assert result["baseURL"] == "https://api.example.com/v1"
    assert result["apiKey"] == "sk-test-12345"
    assert result["apiKeyEnv"] == "OPENCODE_GO_API_KEY"
    assert result["source"] == "settings"
    assert result["contextWindow"] == 200000
    assert result["maxTokens"] == 32000
    assert len(result["models"]) == 2


def test_get_active_llm_falls_back_to_first_provider_when_no_active(
    tmp_dsh_home: Path, monkeypatch: pytest.MonkeyPatch
):
    """If agent-default-model missing but providers exist, use first
    provider's first model."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    _write_yaml(
        tmp_dsh_home / "settings.yaml",
        {
            "llm-pi-ai": {
                "providers": {
                    "opencode-go": {
                        "apiKeyEnv": "OPENCODE_GO_API_KEY",
                        "baseURL": "https://api.example.com/v1",
                        "models": [{"id": "default-model", "contextWindow": 100000}],
                    }
                }
            },
        },
    )
    _write_yaml(
        tmp_dsh_home / ".credentials.yaml",
        {"refs": {"OPENCODE_GO_API_KEY": "sk-fb"}},
    )
    result = dsh_shared.get_active_llm()
    assert result["provider"] == "opencode-go"
    assert result["model"] == "default-model"


def test_get_active_llm_raises_when_provider_missing(tmp_dsh_home: Path):
    _write_yaml(
        tmp_dsh_home / "settings.yaml",
        {"agent-default-model": {"provider": "nonexistent", "model": "x"}},
    )
    with pytest.raises(dsh_shared.ProviderNotFound):
        dsh_shared.get_active_llm()


def test_get_active_llm_raises_when_model_not_in_catalog(tmp_dsh_home: Path):
    _write_yaml(
        tmp_dsh_home / "settings.yaml",
        {
            "llm-pi-ai": {
                "providers": {
                    "opencode-go": {
                        "apiKeyEnv": "OPENCODE_GO_API_KEY",
                        "models": [{"id": "real-model"}],
                    }
                }
            },
            "agent-default-model": {"provider": "opencode-go", "model": "fake-model"},
        },
    )
    _write_yaml(tmp_dsh_home / ".credentials.yaml", {"refs": {"OPENCODE_GO_API_KEY": "sk-x"}})
    with pytest.raises(dsh_shared.ModelNotInCatalog):
        dsh_shared.get_active_llm()


def test_get_active_llm_raises_when_api_key_missing(tmp_dsh_home: Path):
    _write_yaml(
        tmp_dsh_home / "settings.yaml",
        {
            "llm-pi-ai": {
                "providers": {
                    "opencode-go": {
                        "apiKeyEnv": "OPENCODE_GO_API_KEY",
                        "baseURL": "https://x",
                        "models": [{"id": "m1"}],
                    }
                }
            },
            "agent-default-model": {"provider": "opencode-go", "model": "m1"},
        },
    )
    # No .credentials.yaml
    with pytest.raises(dsh_shared.ApiKeyMissing):
        dsh_shared.get_active_llm()


def test_get_active_llm_falls_back_to_env_when_settings_missing(
    tmp_dsh_home: Path, monkeypatch: pytest.MonkeyPatch
):
    """When settings.yaml is empty AND env DEEPSEEK_API_KEY is set,
    use env fallback (DSH launcher path)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-fallback")
    monkeypatch.setenv("CT_DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    # settings.yaml empty -> fallback kicks in
    result = dsh_shared.get_active_llm()
    assert result["source"] == "fallback"
    assert result["model"] == "deepseek-v4-pro"
    assert result["apiKey"] == "sk-env-fallback"
    assert result["baseURL"] == "https://api.deepseek.com"


def test_get_active_llm_api_key_from_credentials_takes_priority_over_env(
    tmp_dsh_home: Path, monkeypatch: pytest.MonkeyPatch
):
    """`.credentials.yaml` refs[apiKeyEnv] wins over env of same name."""
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "sk-env")
    _write_yaml(
        tmp_dsh_home / "settings.yaml",
        {
            "llm-pi-ai": {
                "providers": {
                    "opencode-go": {
                        "apiKeyEnv": "OPENCODE_GO_API_KEY",
                        "baseURL": "https://x",
                        "models": [{"id": "m1"}],
                    }
                }
            },
            "agent-default-model": {"provider": "opencode-go", "model": "m1"},
        },
    )
    _write_yaml(
        tmp_dsh_home / ".credentials.yaml",
        {"refs": {"OPENCODE_GO_API_KEY": "sk-from-credentials"}},
    )
    result = dsh_shared.get_active_llm()
    assert result["apiKey"] == "sk-from-credentials"
