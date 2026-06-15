"""
Cloud API client for Hermes Bridge.

Handles calls to external LLM providers (OpenAI, Anthropic, OpenRouter,
MiniMax) with automatic key resolution from:

1. ``os.environ`` (project .env, loaded at startup)
2. ``HERMES_HOME/.env`` (WebUI writes keys here via Settings → Providers)
3. ``HERMES_HOME/auth.json`` (WebUI credential pool)
4. ``HERMES_HOME/config.yaml`` (provider configuration)

No manual .env editing needed — users add keys in the WebUI and the bridge
picks them up automatically on the next request.

Usage::

    from bridge.cloud_client import CloudClient

    client = CloudClient.from_config()
    response = await client.chat(
        provider="openai",
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello"}],
    )
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("hermes.cloud_client")

# ---- Resolve HERMES_HOME (shared with bridge/server.py) ----
_HERMES_HOME = Path(os.environ.get(
    "HERMES_HOME",
    str(Path(__file__).resolve().parent.parent / "data" / "hermes-agent"),
))

# ---- Provider URL registry ----
_PROVIDER_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "minimax": "https://api.minimaxi.chat/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "xai": "https://api.x.ai/v1",
}

# ---- API key env var mapping ----
_PROVIDER_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
}


class CloudClient:
    """Stateless client for calling cloud LLM APIs.

    Instantiate once at bridge startup. Each :meth:`chat` call is
    self-contained and thread-safe.
    """

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=120, write=30, pool=10),
        )

    @classmethod
    def from_config(cls, config_path: str | Path | None = None) -> CloudClient:
        """Create a client with provider URLs from hermes.yaml."""
        return cls()

    async def chat(
        self,
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a chat completion request to a cloud provider.

        Parameters
        ----------
        provider:
            Provider name: "openai", "openrouter", "minimax", "deepseek".
        model:
            Model ID for that provider.
        messages:
            List of {"role": "...", "content": "..."} dicts.
        max_tokens:
            Max completion tokens.
        temperature:
            Sampling temperature.
        stream:
            If True, returns an async iterator of SSE chunks.

        Returns
        -------
        dict:
            OpenAI-format response: {"choices": [...], "usage": {...}}.
        """
        api_key = self._resolve_api_key(provider)
        base_url = self._resolve_base_url(provider)
        if not api_key:
            raise CloudClientError(
                f"No API key for provider '{provider}'. "
                f"Set {_PROVIDER_KEY_ENV.get(provider, '?')} in .env"
            )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if provider == "openrouter":
            headers["HTTP-Referer"] = "http://localhost:8648"
            headers["X-Title"] = "Hermes Agent"

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        body.update(kwargs)

        endpoint = f"{base_url}/chat/completions"

        try:
            if stream:
                return await self._stream_chat(endpoint, headers, body)
            r = await self._http.post(endpoint, json=body, headers=headers)
            if r.status_code != 200:
                raise CloudClientError(
                    f"Provider '{provider}' returned HTTP {r.status_code}: "
                    f"{r.text[:400]}"
                )
            return r.json()
        except httpx.HTTPError as e:
            raise CloudClientError(
                f"Provider '{provider}' unreachable: {e}"
            ) from e

    async def chat_stream(
        self,
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[bytes]:
        """Stream chat completion chunks as SSE bytes."""
        api_key = self._resolve_api_key(provider)
        base_url = self._resolve_base_url(provider)
        if not api_key:
            raise CloudClientError(f"No API key for '{provider}'")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        body.update(kwargs)

        endpoint = f"{base_url}/chat/completions"
        async with self._http.stream("POST", endpoint, json=body, headers=headers) as r:
            if r.status_code != 200:
                body_text = await r.aread()
                raise CloudClientError(
                    f"Provider '{provider}' stream HTTP {r.status_code}: "
                    f"{body_text.decode()[:400]}"
                )
            async for line in r.aiter_lines():
                if line:
                    yield (line + "\n\n").encode("utf-8")

    # ---- Internal helpers ----

    def _resolve_api_key(self, provider: str) -> str:
        """Resolve API key from multiple sources.

        Priority:
        1. os.environ (project .env, loaded at startup)
        2. HERMES_HOME/.env (WebUI writes keys here)
        3. HERMES_HOME/auth.json (WebUI credential pool)
        4. HERMES_HOME/config.yaml (provider section)
        """
        # 1. os.environ
        env_var = _PROVIDER_KEY_ENV.get(provider, "")
        if env_var:
            key = os.environ.get(env_var, "").strip()
            if key:
                return key
        # Fallback: try provider name directly
        key = os.environ.get(provider.upper() + "_API_KEY", "").strip()
        if key:
            return key

        # 2. HERMES_HOME/.env (WebUI writes here via Settings → Providers)
        hermes_env = _HERMES_HOME / ".env"
        if hermes_env.exists():
            key = _read_key_from_dotenv(hermes_env, env_var)
            if key:
                return key

        # 3. HERMES_HOME/auth.json (WebUI credential pool)
        auth_file = _HERMES_HOME / "auth.json"
        if auth_file.exists():
            key = _read_key_from_auth_json(auth_file, provider)
            if key:
                return key

        # 4. HERMES_HOME/config.yaml (provider config with api_key field)
        config_file = _HERMES_HOME / "config.yaml"
        if config_file.exists():
            key = _read_key_from_config_yaml(config_file, provider)
            if key:
                return key

        return ""

    def _resolve_base_url(self, provider: str) -> str:
        """Resolve base URL for a provider."""
        # Check env override first
        env_var = f"{provider.upper()}_BASE_URL"
        if os.environ.get(env_var):
            return os.environ[env_var].rstrip("/")
        return _PROVIDER_BASE_URLS.get(provider, "")

    async def _stream_chat(
        self, endpoint: str, headers: dict, body: dict
    ) -> dict[str, Any]:
        """Non-streaming helper that collects SSE chunks."""
        chunks: list[dict] = []
        async with self._http.stream("POST", endpoint, json=body, headers=headers) as r:
            if r.status_code != 200:
                raise CloudClientError(f"HTTP {r.status_code}: {await r.aread()}")
            async for line in r.aiter_lines():
                if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                    try:
                        chunks.append(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        pass
        # Merge chunks into a single response
        if not chunks:
            return {"choices": [{"message": {"content": ""}}], "usage": {}}
        merged_content = "".join(
            c.get("choices", [{}])[0].get("delta", {}).get("content", "")
            for c in chunks
        )
        return {
            "choices": [{"message": {"role": "assistant", "content": merged_content}}],
            "usage": chunks[-1].get("usage", {}),
            "model": chunks[0].get("model", ""),
        }

    async def close(self) -> None:
        await self._http.aclose()


class CloudClientError(Exception):
    """Raised when a cloud API call fails."""
    pass


# ---- File-based key readers (WebUI writes to these files) ----


def _read_key_from_dotenv(path: Path, key_name: str) -> str:
    """Read a single key from a .env file."""
    if not key_name or not path.exists():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key_name:
                val = v.strip().strip("'\"").strip()
                if val and not val.startswith("$"):
                    return val
    except Exception:
        pass
    return ""


def _read_key_from_auth_json(path: Path, provider: str) -> str:
    """Read API key from the WebUI credential pool (auth.json).

    WebUI writes entries like::

        {
          "credential_pool": {
            "openai": [{
              "auth_type": "api_key",
              "source": "env:OPENAI_API_KEY",
              ...
            }]
          }
        }

    The actual key value is stored in .env (referenced by source).
    We read the key name from auth.json, then look it up in .env.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    # Look for provider in credential_pool
    pool = data.get("credential_pool", {})
    for pool_key, entries in pool.items():
        if not isinstance(entries, list):
            continue
        # Match: pool key contains provider name, OR entry label contains it
        if provider.lower() not in pool_key.lower():
            # Check individual entry labels
            matched = False
            for entry in entries:
                if isinstance(entry, dict):
                    label = entry.get("label", "")
                    if provider.lower() in label.lower():
                        matched = True
                        break
            if not matched:
                continue

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source = entry.get("source", "")
            # source format: "env:KEY_NAME" or "config:label"
            if source.startswith("env:"):
                key_name = source[4:].strip()
                # Read the actual key from HERMES_HOME/.env
                env_file = _HERMES_HOME / ".env"
                if env_file.exists():
                    key = _read_key_from_dotenv(env_file, key_name)
                    if key:
                        logger.info("resolved API key for '%s' from auth.json → .env", provider)
                        return key
            elif source.startswith("config:"):
                # Key stored in config.yaml
                config_file = _HERMES_HOME / "config.yaml"
                if config_file.exists():
                    key = _read_key_from_config_yaml(config_file, provider)
                    if key:
                        return key

    return ""


def _read_key_from_config_yaml(path: Path, provider: str) -> str:
    """Read API key from config.yaml provider section."""
    try:
        # Simple line-based parser (avoids yaml dependency)
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""

    # Look for patterns like:
    #   providers:
    #     openai:
    #       api_key: sk-xxx
    # Or:
    #   custom_providers:
    #     - name: openai
    #       api_key: sk-xxx
    import re

    # Pattern 1: providers.<name>.api_key
    pat1 = re.compile(
        rf'^{provider}\s*:.*$[\s\S]*?^\s+api_key\s*:\s*["\']?(\S+)["\']?\s*$',
        re.MULTILINE | re.IGNORECASE,
    )
    m = pat1.search(text)
    if m:
        val = m.group(1).strip().strip("'\"")
        if val and not val.startswith("$"):
            return val

    # Pattern 2: custom_providers list
    pat2 = re.compile(
        rf'^\s*-\s+name\s*:\s*["\']?{provider}["\']?[\s\S]*?^\s+api_key\s*:\s*["\']?(\S+)["\']?',
        re.MULTILINE | re.IGNORECASE,
    )
    m = pat2.search(text)
    if m:
        val = m.group(1).strip().strip("'\"")
        if val and not val.startswith("$"):
            return val

    return ""
