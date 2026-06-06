"""
LLM provider abstraction with intelligent fallback chain.

Supports:
  - OpenAI-compatible APIs (works with OpenAI, llama-server, Ollama, vLLM, etc.)
  - Anthropic Claude (best-effort)
  - Multi-provider fallback chain
  - Streaming
"""
from __future__ import annotations
import asyncio
import logging
import os
import time
from typing import Any, AsyncIterator
from dataclasses import dataclass, field
import httpx

logger = logging.getLogger("hermes.llm")


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str
    name: str | None = None
    tool_call_id: str | None = None


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


# ---- Provider base ----

class BaseProvider:
    name: str = "base"

    def __init__(self, name: str, base_url: str, api_key: str = "", timeout: float = 30.0):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "not-needed"
        self.timeout = timeout

    async def chat(
        self,
        messages: list[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        stream: bool = False,
        **kwargs,
    ) -> LLMResponse | AsyncIterator[str]:
        raise NotImplementedError


# ---- OpenAI-compatible provider (works for OpenAI, llama-server, etc.) ----

class OpenAIProvider(BaseProvider):
    """OpenAI-compatible Chat Completions API."""

    def __init__(self, name: str, base_url: str, api_key: str = "", timeout: float = 30.0):
        super().__init__(name, base_url, api_key, timeout)
        self.name = name

    async def health_check(self) -> bool:
        """Quick liveness probe."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                # /v1/models is OpenAI's standard health endpoint
                r = await client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                )
                return r.status_code in (200, 401, 403, 404)  # 任何"收到响应"都算活
        except Exception as e:
            logger.debug(f"health_check failed for {self.name}: {e}")
            return False

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: list[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        stream: bool = False,
        **kwargs,
    ) -> LLMResponse | AsyncIterator[str]:
        payload = {
            "model": model,
            "messages": [self._msg_to_dict(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
            **kwargs,
        }

        start = time.time()
        if stream:
            return self._stream_chat(payload, model)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"{self.name} timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            raise LLMHTTPError(f"{self.name} HTTP {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.HTTPError as e:
            raise LLMNetworkError(f"{self.name} network error: {e}") from e

        latency = int((time.time() - start) * 1000)
        choice = data["choices"][0]
        return LLMResponse(
            content=choice["message"]["content"],
            model=data.get("model", model),
            provider=self.name,
            usage=data.get("usage", {}),
            latency_ms=latency,
            raw=data,
        )

    async def _stream_chat(self, payload: dict, model: str) -> AsyncIterator[str]:
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, headers=self._headers(), json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        import json
                        data = json.loads(data_str)
                        delta = data["choices"][0].get("delta", {}).get("content", "")
                        if delta:
                            yield delta
                    except Exception:
                        continue

    async def embed(self, texts: list[str], model: str, **kwargs) -> list[list[float]]:
        """OpenAI-compatible embeddings endpoint."""
        payload = {"model": model, "input": texts, **kwargs}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/embeddings",
                    headers=self._headers(),
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                return [item["embedding"] for item in data["data"]]
        except Exception as e:
            logger.warning(f"{self.name} embed failed: {e}")
            return []

    @staticmethod
    def _msg_to_dict(m: Message) -> dict:
        d: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.name:
            d["name"] = m.name
        return d


# ---- Mock provider (for testing without real LLM) ----

class MockProvider(BaseProvider):
    """In-process mock provider. Returns canned responses for testing."""

    def __init__(self, name: str = "mock"):
        super().__init__(name, base_url="mock://local", api_key="not-needed", timeout=5.0)
        # Lazy import to avoid circular
        from hermes.mock import mock_chat, hash_embed
        self._chat_fn = mock_chat
        self._embed_fn = hash_embed

    async def chat(
        self,
        messages: list[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        stream: bool = False,
        **kwargs,
    ) -> LLMResponse:
        msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
        # Run in thread to be async-friendly
        loop = asyncio.get_event_loop()
        content = await loop.run_in_executor(None, self._chat_fn, msg_dicts)
        return LLMResponse(
            content=content,
            model=model,
            provider=self.name,
            usage={"completion_tokens": len(content)},
            latency_ms=10,
        )

    async def embed(self, texts: list[str], model: str, **kwargs) -> list[list[float]]:
        loop = asyncio.get_event_loop()
        return [await loop.run_in_executor(None, self._embed_fn, t) for t in texts]


# ---- Custom errors (for fallback decisions) ----

class LLMError(Exception):
    """Base LLM error."""
    provider: str = ""


class LLMTimeoutError(LLMError):
    pass


class LLMHTTPError(LLMError):
    def __init__(self, msg: str):
        super().__init__(msg)
        # Try to extract status code
        try:
            self.status_code = int(msg.split("HTTP ")[1].split(" ")[0])
        except Exception:
            self.status_code = 0


class LLMNetworkError(LLMError):
    pass


# ---- Router ----

class LLMRouter:
    """
    Routes chat requests through a fallback chain.

    Strategy:
        1. Try primary provider.
        2. On failure (5xx, timeout, network error), try next in chain.
        3. Return first successful response.

    The `order` list supports:
        - Provider names: 'openai', 'anthropic', 'mock', etc.
        - Category names: 'cloud' (all cloud providers), 'local', 'mock'
    """

    # Categories recognized in fallback order
    CATEGORIES = {
        "cloud": ["openai", "anthropic", "openrouter", "google"],
        "local": ["local"],
        "mock": ["mock"],
    }

    def __init__(self, providers: list[BaseProvider], order: list[str] | None = None):
        self.providers: dict[str, BaseProvider] = {p.name: p for p in providers}
        # Expand category names in order
        self.order: list[str] = self._expand_order(order or list(self.providers.keys()))

    def _expand_order(self, order: list[str]) -> list[str]:
        """Expand 'cloud'/'local'/'mock' categories to actual provider names."""
        expanded: list[str] = []
        seen: set[str] = set()
        for item in order:
            if item in self.CATEGORIES:
                for name in self.CATEGORIES[item]:
                    if name in self.providers and name not in seen:
                        expanded.append(name)
                        seen.add(name)
            elif item in self.providers and item not in seen:
                expanded.append(item)
                seen.add(item)
        # Add any providers not in the order at the end
        for name in self.providers:
            if name not in seen:
                expanded.append(name)
                seen.add(name)
        return expanded

    def get(self, name: str) -> BaseProvider | None:
        return self.providers.get(name)

    def available(self) -> list[BaseProvider]:
        """Return providers in fallback order."""
        return [self.providers[n] for n in self.order if n in self.providers]

    async def health_check_all(self) -> dict[str, bool]:
        results = {}
        for name, p in self.providers.items():
            try:
                if hasattr(p, "health_check"):
                    results[name] = await p.health_check()
                else:
                    results[name] = True
            except Exception:
                results[name] = False
        return results

    async def chat(
        self,
        messages: list[Message],
        model_hint: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        stream: bool = False,
        prefer: str | None = None,
    ) -> LLMResponse | AsyncIterator[str]:
        """
        Try providers in order; return first success.

        `prefer` can override the order temporarily (e.g. force local).
        `model_hint` picks the model; default is provider's "default" model.
        """
        order = self.order[:]
        if prefer and prefer in order:
            order.remove(prefer)
            order.insert(0, prefer)

        last_error: Exception | None = None
        for name in order:
            provider = self.providers.get(name)
            if not provider:
                continue
            # Get model for this provider
            model = self._pick_model(provider, model_hint)
            try:
                logger.info(f"[llm] trying {name} / {model}")
                resp = await provider.chat(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=stream,
                )
                logger.info(f"[llm] {name} ok ({getattr(resp, 'latency_ms', '?')}ms)")
                return resp
            except LLMError as e:
                last_error = e
                logger.warning(f"[llm] {name} failed: {e}")
                continue
            except Exception as e:
                last_error = e
                logger.warning(f"[llm] {name} unexpected error: {e}")
                continue

        raise LLMError(f"All providers failed. Last error: {last_error}")

    def _pick_model(self, provider: BaseProvider, hint: str | None) -> str:
        """Pick model name for this provider."""
        # OpenAI provider stores model in `models` config
        if isinstance(provider, OpenAIProvider):
            models = getattr(provider, "_models", {})
            if hint and hint in models:
                return models[hint]
            return models.get("default") or models.get("fast") or hint or "default"
        return hint or "default"


# ---- Builder ----

def build_router_from_config(cfg, use_mock: bool = False) -> LLMRouter:
    """Build an LLMRouter from HermesConfig. Includes online probing."""
    providers: list[BaseProvider] = []

    # Cloud providers
    for c in cfg.llm.cloud:
        if not c.api_key or c.api_key.startswith("sk-xxx") or c.api_key == "not-needed":
            continue
        providers.append(OpenAIProvider(
            name=c.name,
            base_url=c.base_url,
            api_key=c.api_key,
            timeout=cfg.llm.router.on_timeout_ms / 1000 * 2,
        ))
        # 注入模型映射
        providers[-1]._models = c.models  # type: ignore

    # Mock provider (for testing) - always works
    if use_mock or os.environ.get("HERMES_LLM_MOCK") == "1":
        providers.append(MockProvider("mock"))

    # Local provider (always included)
    local = cfg.llm.local
    local_prov = OpenAIProvider(
        name="local",
        base_url=local.base_url,
        api_key=local.api_key,
        timeout=300.0,  # 本地推理慢，预留 5 分钟
    )
    local_prov._models = local.models  # type: ignore
    providers.append(local_prov)

    return LLMRouter(providers, cfg.llm.router.fallback_order)


# ---- Sync wrapper for tests / CLI ----

def chat_sync(router: LLMRouter, messages: list[Message], **kwargs) -> LLMResponse:
    return asyncio.run(router.chat(messages, **kwargs))
