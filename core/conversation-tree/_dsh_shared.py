"""
_dsh_shared.py -- read DeepSeek Harness (dsh) shared configuration.

Conversation-tree is one of dsh's clients (along with dsh-web). To avoid
config drift, CT reads the LLM provider/model/baseUrl/apiKey from dsh's
single source of truth instead of its own .env.

Sources (read-only, never written by CT):
  1. $DSH_HOME or ~/.dsh/settings.yaml      -> llm-pi-ai.providers.<route>.*
  2. $DSH_HOME or ~/.dsh/.credentials.yaml  -> refs.<apiKeyEnv>
  3. ~/.dsh/settings.yaml -> agent-default-model: {provider, model}

CT only reads; users edit via the dsh UI (settings page Models tab), which
writes the canonical yaml. CT picks up the change on the next /api/chat
call (or the next call to get_active_llm()).
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

import yaml  # PyYAML 6.0.3 verified in portable-python

logger = logging.getLogger("ct.dsh_shared")

# ── Home resolution ─────────────────────────────────────────────────────────

_DSH_HOME_ENV = "DSH_HOME"
_active_llm_cache: dict[str, Any] | None = None
_active_llm_lock = threading.Lock()


def resolve_dsh_home() -> Path:
    """Resolve the dsh home directory.

    Priority: $DSH_HOME > ~/.dsh. Never raises; falls back to ~/.dsh
    even if it does not exist (callers handle the missing-file case).
    """
    env_home = os.environ.get(_DSH_HOME_ENV, "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".dsh").resolve()


# ── YAML helpers ─────────────────────────────────────────────────────────────


def read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file and return a dict. Returns {} on any failure.

    Catches OSError (file missing, permission denied), yaml.YAMLError
    (parse failure), and UnicodeDecodeError. Never raises -- callers
    treat empty dict as "config unavailable".
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.debug("yaml not found: %s", path)
        return {}
    except PermissionError as exc:
        logger.warning("yaml permission denied: %s (%s)", path, exc)
        return {}
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as exc:
        logger.warning("yaml read failed: %s (%s)", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def read_dsh_settings() -> dict[str, Any]:
    """Read $DSH_HOME/settings.yaml. Returns {} on any failure."""
    return read_yaml(resolve_dsh_home() / "settings.yaml")


def read_dsh_credentials() -> dict[str, Any]:
    """Read $DSH_HOME/.credentials.yaml. Returns {} on any failure."""
    return read_yaml(resolve_dsh_home() / ".credentials.yaml")


# ── Errors ───────────────────────────────────────────────────────────────────


class LLMConfigError(Exception):
    """Base error for dsh-shared LLM config resolution."""


class SettingsUnreadable(LLMConfigError):
    """settings.yaml could not be parsed (after read_yaml fallback)."""


class ProviderNotFound(LLMConfigError):
    """The provider route from agent-default-model is not in llm-pi-ai.providers."""


class ModelNotInCatalog(LLMConfigError):
    """The model from agent-default-model is not in providers.<route>.models[]."""


class ApiKeyMissing(LLMConfigError):
    """apiKeyEnv name found but no key in .credentials.yaml refs nor env."""


# ── Provider resolution ──────────────────────────────────────────────────────


def _resolve_api_key(api_key_env: str, credentials: dict[str, Any]) -> str | None:
    """Resolve an API key from .credentials.yaml refs[api_key_env] or env.

    Priority: .credentials.yaml refs > os.environ[api_key_env] > None.
    """
    if not api_key_env:
        return None
    refs = credentials.get("refs") or {}
    if isinstance(refs, dict):
        key = refs.get(api_key_env)
        if isinstance(key, str) and key.strip():
            return key.strip()
    env_key = os.environ.get(api_key_env, "").strip()
    return env_key or None


def _provider_base_url(provider_id: str, provider_cfg: dict[str, Any]) -> str | None:
    """Resolve the base URL for a provider.

    Priority: provider_cfg.baseURL > os.environ[DEEPSEEK_BASE_URL] (fallback
    for dsh-llm-deepseek) > hardcoded public endpoint for known ids.
    Returns the URL WITHOUT trailing slash.
    """
    base = provider_cfg.get("baseURL")
    if isinstance(base, str) and base.strip():
        return base.strip().rstrip("/")
    # Fallback: if this provider route is the dsh-llm-deepseek default, use
    # the public endpoint unless overridden via env.
    if provider_id == "deepseek-official":
        env = os.environ.get("DEEPSEEK_BASE_URL", "").strip()
        if env:
            return env.rstrip("/")
        return "https://api.deepseek.com"
    # 2026-09-06: opencode-go / opencode 是 dsh llm-pi-ai 的内置 provider,
    # settings.yaml 不写 baseURL (由 pi-ai provider registry 解析). CT 直连
    # OpenAI-compatible /chat/completions, 用 openai-completions 对应的端点.
    # 来源: @earendil-works/pi-ai dist/providers/data/opencode-go.json / opencode.json
    if provider_id == "opencode-go":
        env = os.environ.get("OPENCODE_GO_BASE_URL", "").strip()
        if env:
            return env.rstrip("/")
        return "https://opencode.ai/zen/go/v1"
    if provider_id == "opencode":
        env = os.environ.get("OPENCODE_BASE_URL", "").strip()
        if env:
            return env.rstrip("/")
        return "https://opencode.ai/zen/v1"
    return None


def _provider_models(provider_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the provider's models list (each entry: {id, name, contextWindow,
    maxTokens, ...}). Empty list if unset."""
    models = provider_cfg.get("models")
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def get_active_llm() -> dict[str, Any]:
    """Resolve the currently active LLM configuration.

    Returns a dict with keys:
      - provider (str): provider route id (e.g. "opencode-go")
      - model (str): model id (e.g. "deepseek-v4-flash")
      - baseURL (str): full root URL (no trailing slash)
      - apiKey (str | None): resolved API key, or None if missing
      - apiKeyEnv (str): env var name the key was resolved from
      - models (list[dict]): full model catalog for this provider
      - contextWindow (int): context window for the active model
      - maxTokens (int | None): max output tokens for the active model
      - source (str): "settings" or "fallback"

    Raises:
      SettingsUnreadable: settings.yaml is missing/unreadable AND no env fallback.
      ProviderNotFound: agent-default-model.provider not in providers dict.
      ModelNotInCatalog: agent-default-model.model not in that provider's models[].
      ApiKeyMissing: apiKeyEnv set but no key in .credentials.yaml or env.
    """
    settings = read_dsh_settings()
    credentials = read_dsh_credentials()

    providers_root = (
        settings.get("llm-pi-ai", {}).get("providers", {})
        if isinstance(settings.get("llm-pi-ai"), dict)
        else {}
    )
    if not isinstance(providers_root, dict):
        providers_root = {}

    active = settings.get("agent-default-model")
    if not isinstance(active, dict):
        active = {}

    provider_id = active.get("provider") or ""
    model_id = active.get("model") or ""

    # Fallback 1: env DEEPSEEK_* (if dsh launcher exports it and settings
    # has no agent-default-model section -- rare but possible during boot)
    if not provider_id or not model_id:
        env_provider = "deepseek-official"
        env_model = os.environ.get("CT_DEEPSEEK_MODEL") or os.environ.get(
            "DEEPSEEK_MODEL", "deepseek-v4-flash"
        )
        env_base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip() or None
        if env_key:
            return {
                "provider": env_provider,
                "model": env_model,
                "baseURL": env_base,
                "apiKey": env_key,
                "apiKeyEnv": "DEEPSEEK_API_KEY",
                "models": [],
                "contextWindow": 128000,
                "maxTokens": None,
                "source": "fallback",
            }
        if not settings:
            raise SettingsUnreadable(
                "settings.yaml is empty/missing and no DEEPSEEK_API_KEY env"
            )
        # settings.yaml exists but has no agent-default-model: pick first
        # provider's first model as a last-resort default.
        first_pid, first_pcfg = next(iter(providers_root.items()), (None, None))
        if first_pid and isinstance(first_pcfg, dict):
            models = _provider_models(first_pcfg)
            if models:
                provider_id = first_pid
                model_id = models[0].get("id") or ""
                # fall through to normal resolution below

    if provider_id not in providers_root:
        raise ProviderNotFound(
            f"agent-default-model.provider '{provider_id}' not in "
            f"llm-pi-ai.providers (have: {sorted(providers_root)})"
        )
    pcfg = providers_root[provider_id]
    if not isinstance(pcfg, dict):
        raise ProviderNotFound(f"provider '{provider_id}' config is not a dict")

    models = _provider_models(pcfg)
    model_entry = next((m for m in models if m.get("id") == model_id), None)
    if model_id and not model_entry:
        raise ModelNotInCatalog(
            f"model '{model_id}' not in providers.{provider_id}.models[]"
            f" (have: {[m.get('id') for m in models]})"
        )
    if not model_id and models:
        model_entry = models[0]
        model_id = model_entry.get("id") or ""

    api_key_env = pcfg.get("apiKeyEnv") or ""
    api_key = _resolve_api_key(api_key_env, credentials)
    if api_key_env and not api_key:
        raise ApiKeyMissing(
            f"apiKey '{api_key_env}' not found in .credentials.yaml refs nor env"
        )

    base_url = _provider_base_url(provider_id, pcfg)
    if not base_url:
        raise SettingsUnreadable(
            f"provider '{provider_id}' has no baseURL and no env fallback"
        )

    context_window = (
        (model_entry or {}).get("contextWindow")
        or pcfg.get("defaultContextWindow")
        or 128000
    )
    max_tokens = (model_entry or {}).get("maxTokens")

    return {
        "provider": provider_id,
        "model": model_id,
        "baseURL": base_url,
        "apiKey": api_key,
        "apiKeyEnv": api_key_env,
        "models": models,
        "contextWindow": int(context_window),
        "maxTokens": int(max_tokens) if isinstance(max_tokens, (int, float)) else None,
        "source": "settings",
    }


def get_active_llm_cached() -> dict[str, Any]:
    """Cached variant of get_active_llm(). Cache is per-process; cleared on
    explicit refresh (none today). Errors propagate (caller decides).
    """
    global _active_llm_cache
    with _active_llm_lock:
        if _active_llm_cache is not None:
            return dict(_active_llm_cache)
        result = get_active_llm()
        # Only cache success results; failures should retry next call.
        _active_llm_cache = result
        return dict(result)


def refresh_active_llm_cache() -> None:
    """Clear the cache so the next get_active_llm_cached() re-reads from disk."""
    global _active_llm_cache
    with _active_llm_lock:
        _active_llm_cache = None
