"""
LLM provider abstraction with intelligent fallback chain.

Supports:
  - OpenAI-compatible APIs (works with OpenAI, llama-server, Ollama, vLLM, etc.)
  - Anthropic Claude (best-effort)
  - Multi-provider fallback chain
  - Streaming

Streaming API (Phase 1 addition):
  - `BaseProvider.stream(messages, **kwargs) -> AsyncIterator[str]`
    Yields content tokens (or, for Mock, one character at a time).
  - `LLMRouter.stream_chat(...)` walks the fallback chain and returns the
    first async iterator that yields; on provider failure it closes the
    current stream and tries the next provider in line.
  - `LLMRouter.collect_stream(...)` consumes the async iterator to a string
    while still yielding each chunk via an optional callback (useful for
    incremental persistence / SSE).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncIterator, Callable, Optional
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

    async def stream(
        self,
        messages: list[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Yield content tokens from the LLM.

        Default implementation calls ``chat(stream=True)`` and delegates to
        the provider's streaming path. Providers with first-class streaming
        (OpenAI-compatible) override this to avoid buffering the full reply.
        Yields plain content deltas (NOT including role metadata).
        """
        result = await self.chat(
            messages=messages, model=model,
            temperature=temperature, max_tokens=max_tokens,
            stream=True, **kwargs,
        )
        # When stream=True, OpenAIProvider returns the async iterator directly
        if hasattr(result, "__aiter__"):
            async for chunk in result:  # type: ignore[union-attr]
                yield chunk
            return
        # Non-streaming fallback: emit the whole content as a single chunk
        if isinstance(result, LLMResponse):
            yield result.content
            return
        # Defensive: iterator-like but not async
        async for chunk in result:  # type: ignore[union-attr]
            yield chunk


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

    async def stream(
        self,
        messages: list[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs,
    ) -> AsyncIterator[str]:
        """True token-level streaming via SSE.

        For OpenAI-compatible APIs (llama-server, Ollama, vLLM, etc.) this
        uses HTTP chunked transfer and yields each ``delta.content`` as soon
        as it arrives. Falls back to the default ``stream()`` base method
        if the streaming request fails (so the caller still gets a reply).
        """
        payload = {
            "model": model,
            "messages": [self._msg_to_dict(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **kwargs,
        }
        try:
            async for chunk in self._stream_chat(payload, model):
                yield chunk
        except (httpx.HTTPError, LLMError) as e:
            logger.warning(f"[{self.name}] stream failed ({e}); falling back to non-streaming")
            resp = await self.chat(
                messages=messages, model=model,
                temperature=temperature, max_tokens=max_tokens,
                stream=False, **kwargs,
            )
            if isinstance(resp, LLMResponse) and resp.content:
                yield resp.content

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

    async def stream(
        self,
        messages: list[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Mock streaming: build the canned reply, then yield one character
        at a time with a small delay. This simulates true token streaming
        so the SSE pipeline can be tested end-to-end without a real LLM.
        """
        msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
        loop = asyncio.get_event_loop()
        content = await loop.run_in_executor(None, self._chat_fn, msg_dicts)
        if not content:
            return
        # Per-character yield with a small delay to make the stream visible
        # in the WebUI. Cap delay at first/last char to avoid super-slow tails.
        for i, ch in enumerate(content):
            yield ch
            # ~10ms per char — fast enough for a feel, slow enough to demo
            if i < len(content) - 1:
                await asyncio.sleep(0.01)

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

    # ---- Streaming (Phase 1: real SSE) -------------------------------------

    async def stream_chat(
        self,
        messages: list[Message],
        model_hint: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        prefer: str | None = None,
    ) -> AsyncIterator[str]:
        """Async generator that yields content tokens.

        Walks the fallback chain. On LLMError it moves to the next provider;
        on a mid-stream error it raises (so the caller can decide whether to
        retry or terminate). Yields the provider name and model in the first
        chunk via a side-channel is NOT done here; the server captures it
        from the router state before iterating.
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
            model = self._pick_model(provider, model_hint)
            logger.info(f"[llm.stream] trying {name} / {model}")
            # Track which provider actually served the stream — useful for
            # the final "done" event so the UI can show the model name.
            self._last_stream_provider = name
            self._last_stream_model = model
            try:
                agen = provider.stream(
                    messages=messages, model=model,
                    temperature=temperature, max_tokens=max_tokens,
                )
                async for chunk in agen:
                    yield chunk
                # Provider's stream ended cleanly
                return
            except LLMError as e:
                last_error = e
                logger.warning(f"[llm.stream] {name} failed: {e}")
                continue
            except Exception as e:
                # Mid-stream failure — surface as the final error if no more
                # providers are available. We still try the next provider
                # so the user gets a reply even if one backend dies halfway.
                last_error = e
                logger.warning(f"[llm.stream] {name} mid-stream error: {e}")
                continue
        # All providers failed
        raise LLMError(f"All providers failed streaming. Last error: {last_error}")

    async def collect_stream(
        self,
        messages: list[Message],
        on_chunk: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> tuple[str, str, str]:
        """Consume a stream into a final string while calling ``on_chunk``
        for each delta. Returns ``(content, provider, model)``.

        The callback is invoked from the same async context as the consumer
        — useful for incremental session persistence (server.py).
        """
        buf: list[str] = []
        async for ch in self.stream_chat(messages, **kwargs):
            buf.append(ch)
            if on_chunk is not None:
                try:
                    on_chunk(ch)
                except Exception as cb_err:
                    logger.debug(f"[llm.collect_stream] callback error: {cb_err}")
        return (
            "".join(buf),
            getattr(self, "_last_stream_provider", "unknown"),
            getattr(self, "_last_stream_model", "unknown"),
        )


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
