"""
Memory store for Hermes.

Long-term memory with semantic search via embeddings.
Uses a simple file-based backend by default; can swap to ChromaDB.
"""
from __future__ import annotations
import json
import math
import os
import time
import uuid
import logging
from pathlib import Path
from typing import Any
from dataclasses import dataclass, asdict

logger = logging.getLogger("hermes.memory")


@dataclass
class MemoryItem:
    id: str
    text: str
    embedding: list[float]
    tags: list[str]
    created_at: float
    access_count: int = 0
    last_access: float = 0.0
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.last_access == 0.0:
            self.last_access = self.created_at


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class MemoryStore:
    """
    Simple persistent vector memory.

    - Stores items as JSONL (one per line, easy append/grep)
    - Embeddings are computed lazily via the configured embedding model
    - Search uses cosine similarity + recency decay
    """

    def __init__(
        self,
        path: Path,
        embedder: "Embedder | None" = None,
        recency_decay: float = 0.95,
        max_results: int = 5,
    ):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.items: list[MemoryItem] = []
        self.embedder = embedder
        self.recency_decay = recency_decay
        self.max_results = max_results
        self._load()

    def _load(self):
        f = self.path / "memory.jsonl"
        if not f.exists():
            return
        try:
            with open(f, "r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        self.items.append(MemoryItem(**d))
                    except Exception as e:
                        logger.warning(f"Skipping bad memory line: {e}")
            logger.info(f"Loaded {len(self.items)} memory items from {f}")
        except Exception as e:
            logger.warning(f"Failed to load memory: {e}")

    def _save(self):
        f = self.path / "memory.jsonl"
        with open(f, "w", encoding="utf-8") as fp:
            for item in self.items:
                fp.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")

    async def remember(
        self,
        text: str,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> MemoryItem:
        """Add a new memory item."""
        embedding: list[float] = []
        if self.embedder:
            try:
                embedding = (await self.embedder.embed([text]))[0]
            except Exception as e:
                logger.warning(f"Embedding failed, storing without: {e}")

        item = MemoryItem(
            id=str(uuid.uuid4()),
            text=text,
            embedding=embedding,
            tags=tags or [],
            created_at=time.time(),
            metadata=metadata or {},
        )
        self.items.append(item)
        self._save()
        logger.info(f"Remembered: {text[:60]}... (id={item.id[:8]})")
        return item

    async def recall(
        self,
        query: str,
        k: int | None = None,
        tag_filter: str | None = None,
    ) -> list[tuple[MemoryItem, float]]:
        """
        Search memory by semantic similarity.

        Returns list of (item, score) sorted by score desc.
        Score = similarity * recency_decay^(age_in_hours)
        """
        k = k or self.max_results
        if not self.items:
            return []

        # Embed query
        query_emb: list[float] = []
        if self.embedder:
            try:
                query_emb = (await self.embedder.embed([query]))[0]
            except Exception as e:
                logger.warning(f"Query embedding failed: {e}")
                return []

        if not query_emb:
            return []

        # Score each item
        now = time.time()
        scored: list[tuple[MemoryItem, float]] = []
        for item in self.items:
            if not item.embedding:
                continue
            if tag_filter and tag_filter not in item.tags:
                continue
            sim = cosine_similarity(query_emb, item.embedding)
            age_hours = (now - item.created_at) / 3600
            recency = math.pow(self.recency_decay, age_hours)
            score = sim * recency
            scored.append((item, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def forget(self, item_id: str) -> bool:
        for i, it in enumerate(self.items):
            if it.id == item_id:
                del self.items[i]
                self._save()
                return True
        return False

    def clear(self):
        self.items = []
        self._save()

    def stats(self) -> dict[str, Any]:
        return {
            "total_items": len(self.items),
            "with_embeddings": sum(1 for x in self.items if x.embedding),
            "path": str(self.path),
        }


# ---- Embedder interface ----

class Embedder:
    """Base interface. Concrete impls below."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class HTTPEmbedder(Embedder):
    """Embedder that calls an OpenAI-compatible /v1/embeddings endpoint."""

    def __init__(self, base_url: str, api_key: str = "not-needed", model: str = "nomic-embed", timeout: float = 30.0):
        import httpx
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "not-needed"
        self.model = model
        self.timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"model": self.model, "input": texts},
                )
                r.raise_for_status()
                data = r.json()
                return [item["embedding"] for item in data["data"]]
        except Exception as e:
            logger.warning(f"HTTPEmbedder failed: {e}")
            return []


class HashEmbedder(Embedder):
    """Fallback embedder using simple hashing. NOT semantically meaningful.

    Used when no real embedder is available. Provides deterministic
    pseudo-embeddings so memory can still be searched (just poorly).
    """

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        """Synchronous embedding implementation."""
        out = []
        for t in texts:
            import hashlib
            vec = [0.0] * 384
            for word in t.lower().split():
                h = int(hashlib.md5(word.encode()).hexdigest()[:8], 16)
                idx = h % 384
                vec[idx] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [x / norm for x in vec]
            out.append(vec)
        return out

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Async wrapper around sync implementation."""
        return self._embed_sync(texts)

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        """Sync embedding method for use in sync contexts."""
        return self._embed_sync(texts)


def build_embedder_from_config(cfg, prefer: str = "auto") -> Embedder:
    """Build an embedder based on config and availability."""
    # Honor env var override
    env_prefer = os.environ.get("HERMES_EMBEDDER", "").lower()
    if env_prefer == "hash":
        return HashEmbedder()
    if env_prefer == "http" and cfg.embedding.base_url:
        return HTTPEmbedder(cfg.embedding.base_url, cfg.embedding.api_key, cfg.embedding.model, timeout=10.0)
    if env_prefer == "http" and not cfg.embedding.base_url:
        return HashEmbedder()

    e = cfg.embedding
    if prefer == "hash" or not e.base_url:
        return HashEmbedder()
    return HTTPEmbedder(e.base_url, e.api_key, e.model, timeout=10.0)
