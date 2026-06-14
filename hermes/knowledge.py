"""
Knowledge base for Hermes.

Manages a collection of documents, chunked and indexed for retrieval.
"""
from __future__ import annotations
import hashlib
import json
import time
import uuid
import logging
from pathlib import Path
from typing import Any
from dataclasses import dataclass, asdict

# Cosine search uses a minimal in-tree dep (was previously in hermes/memory.py).
import math


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0.0 on empty / mismatch."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class Embedder:
    """Base interface for embedders used by KnowledgeBase.

    Concrete impls (HTTPEmbedder for OpenAI-compatible /v1/embeddings,
    HashEmbedder as offline fallback) can be passed via the `embedder`
    constructor arg. Upstream hermes-agent has its own embedder hierarchy
    at `agent/memory_provider.py` — if we want a richer embedder later,
    import from there instead of inlining.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class HashEmbedder(Embedder):
    """Deterministic offline pseudo-embedder. NOT semantically meaningful.

    Useful as a fallback when no real embedder is configured. Vectors are
    384-dimensional word-bag hashes — search quality is poor but indexing
    still works for keyword overlap.
    """

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        out: list[list[float]] = []
        for t in texts:
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
        return self._embed_sync(texts)

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        return self._embed_sync(texts)

logger = logging.getLogger("hermes.knowledge")


@dataclass
class KnowledgeChunk:
    id: str
    text: str
    source: str
    embedding: list[float]
    metadata: dict[str, Any]
    created_at: float


class KnowledgeBase:
    """
    File-based knowledge base.

    Layout:
        knowledge/
        ├── index.jsonl      # chunk index
        ├── sources/         # raw source files (copies)
        └── README.md        # knowledge base description
    """

    def __init__(
        self,
        path: Path,
        embedder: "Embedder | None" = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        max_results: int = 5,
    ):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "sources").mkdir(exist_ok=True)
        self.embedder = embedder
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_results = max_results
        self.chunks: list[KnowledgeChunk] = []
        self._load()

    def _load(self):
        f = self.path / "index.jsonl"
        if not f.exists():
            return
        with open(f, "r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    self.chunks.append(KnowledgeChunk(**d))
                except Exception as e:
                    logger.warning(f"Bad knowledge chunk: {e}")
        logger.info(f"Loaded {len(self.chunks)} knowledge chunks")

    def _save(self):
        f = self.path / "index.jsonl"
        with open(f, "w", encoding="utf-8") as fp:
            for c in self.chunks:
                fp.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")

    def ingest(
        self,
        source: str | Path,
        tag: str | None = None,
        recursive: bool = True,
    ) -> int:
        """
        Ingest a file or directory into the knowledge base.

        Returns the number of chunks added.
        """
        source = Path(source)
        if source.is_dir():
            count = 0
            for p in source.rglob("*") if recursive else source.iterdir():
                if p.is_file() and self._is_text(p):
                    count += self._ingest_file(p, tag)
            return count
        elif source.is_file():
            return self._ingest_file(source, tag)
        else:
            raise FileNotFoundError(source)

    def _is_text(self, p: Path) -> bool:
        if p.suffix.lower() in {".md", ".txt", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".html", ".css", ".sh", ".bat"}:
            return True
        # Binary file: peek first 8KB
        try:
            with open(p, "rb") as f:
                chunk = f.read(8192)
            return not any(b == 0 for b in chunk[:1024])  # crude text check
        except Exception:
            return False

    def _ingest_file(self, p: Path, tag: str | None) -> int:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.warning(f"Cannot read {p}: {e}")
            return 0

        if not text.strip():
            return 0

        # Copy source
        dest = self.path / "sources" / p.name
        if not dest.exists():
            try:
                dest.write_text(text, encoding="utf-8")
            except Exception:
                pass

        # Chunk
        chunks = self._chunk_text(text)
        added = 0
        for chunk_text in chunks:
            if not chunk_text.strip():
                continue
            embedding: list[float] = []
            if self.embedder:
                try:
                    # Use sync method if available, otherwise fallback to async
                    if hasattr(self.embedder, 'embed_sync'):
                        result = self.embedder.embed_sync([chunk_text])
                    else:
                        # Fallback: use asyncio.run() which always works from sync context
                        import asyncio
                        coro = self.embedder.embed([chunk_text])
                        result = asyncio.run(coro)
                    embedding = result[0]
                except Exception as e:
                    logger.debug(f"Embedding failed for chunk: {e}")
                    pass

            chunk = KnowledgeChunk(
                id=hashlib.sha1(f"{p.name}:{chunk_text[:64]}".encode()).hexdigest()[:16],
                text=chunk_text,
                source=str(p),
                embedding=embedding,
                metadata={"tag": tag, "file": p.name},
                created_at=time.time(),
            )
            self.chunks.append(chunk)
            added += 1

        self._save()
        logger.info(f"Ingested {p}: {added} chunks (tag={tag})")
        return added

    def _chunk_text(self, text: str) -> list[str]:
        """Simple sliding-window chunker."""
        text = text.replace("\r\n", "\n")
        chunks = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + self.chunk_size, n)
            # Try to break on newline
            if end < n:
                nl = text.rfind("\n", start + self.chunk_size // 2, end)
                if nl > start:
                    end = nl + 1
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= n:
                break
            start = max(end - self.chunk_overlap, start + 1)
        return chunks

    async def search(
        self,
        query: str,
        k: int | None = None,
        tag: str | None = None,
    ) -> list[tuple[KnowledgeChunk, float]]:
        """Search knowledge base by semantic similarity."""
        k = k or self.max_results
        if not self.chunks:
            return []

        query_emb: list[float] = []
        if self.embedder:
            try:
                query_emb = (await self.embedder.embed([query]))[0]
            except Exception as e:
                logger.warning(f"Query embedding failed: {e}")
                return []

        if not query_emb:
            # Fallback: simple keyword match
            return self._keyword_search(query, k, tag)

        scored = []
        for c in self.chunks:
            if tag and c.metadata.get("tag") != tag:
                continue
            if not c.embedding:
                continue
            sim = cosine_similarity(query_emb, c.embedding)
            scored.append((c, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def _keyword_search(self, query: str, k: int, tag: str | None) -> list[tuple[KnowledgeChunk, float]]:
        query_words = set(query.lower().split())
        scored = []
        for c in self.chunks:
            if tag and c.metadata.get("tag") != tag:
                continue
            text_words = set(c.text.lower().split())
            if not query_words:
                continue
            overlap = len(query_words & text_words) / len(query_words)
            if overlap > 0:
                scored.append((c, overlap))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def stats(self) -> dict[str, Any]:
        return {
            "total_chunks": len(self.chunks),
            "with_embeddings": sum(1 for c in self.chunks if c.embedding),
            "sources": len(set(c.source for c in self.chunks)),
            "path": str(self.path),
        }



