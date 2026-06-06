"""Real embedding model support for Hermes.

Adds `SentenceTransformersEmbedder` to the existing memory.py system.
Uses sentence-transformers library + all-MiniLM-L6-v2 model (~80MB, downloads on first use).

Usage:
    pip install sentence-transformers
    # First call will download model to ~/.cache/huggingface/
    # To pre-cache, run:
    #   python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

Then in config, set:
    HERMES_EMBEDDER=sbert  (env var)
or
    embedding:
      provider: sbert
      model: all-MiniLM-L6-v2

If sbert is not installed or model download fails, falls back to HashEmbedder.
"""
from __future__ import annotations
import asyncio
import hashlib
import logging
import math
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes.embeddings")


class SBERTEmbedder:
    """Sentence-Transformers based embedder. Real semantic vectors.

    Model: all-MiniLM-L6-v2 (default)
        - 384-dim output
        - 80MB download
        - Fast on CPU, faster on GPU
        - Good general-purpose quality

    For multilingual support, swap to paraphrase-multilingual-MiniLM-L12-v2.
    For higher quality, all-mpnet-base-v2 (420MB).
    """

    DEFAULT_MODEL = "all-MiniLM-L6-v2"
    DEFAULT_DIM = 384

    def __init__(self, model_name: str | None = None, cache_dir: str | Path | None = None):
        self.model_name = model_name or self.DEFAULT_MODEL
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._model = None
        self._lock = threading.Lock()

    def _load_model(self):
        """Lazy-load the model (downloads on first call)."""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                from sentence_transformers import SentenceTransformer
                import os
                # If cache_dir set, use HF_HOME
                if self.cache_dir:
                    os.environ.setdefault("HF_HOME", str(self.cache_dir))
                logger.info(f"Loading sentence-transformers model: {self.model_name}")
                self._model = SentenceTransformer(self.model_name, cache_folder=str(self.cache_dir) if self.cache_dir else None)
                logger.info(f"Loaded model, dim={self._model.get_sentence_embedding_dimension()}")
            except ImportError:
                logger.warning(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                )
                raise
            except Exception as e:
                logger.error(f"Failed to load SBERT model: {e}")
                raise

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            loop = asyncio.get_event_loop()
            # Run sync encode in thread to not block event loop
            return await loop.run_in_executor(None, self._encode_sync, texts)
        except Exception as e:
            logger.warning(f"SBERT embed failed: {e}, falling back to hash")
            return await HashEmbedderFallback().embed(texts)

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        self._load_model()
        if self._model is None:
            raise RuntimeError("SBERT model not loaded")
        vectors = self._model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]


class HashEmbedderFallback:
    """Same logic as hermes/memory.py HashEmbedder but standalone (avoids circular import)."""

    DIM = 384

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * self.DIM
            for word in t.lower().split():
                h = int(hashlib.md5(word.encode()).hexdigest()[:8], 16)
                idx = h % self.DIM
                vec[idx] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vec = [x / norm for x in vec]
            out.append(vec)
        return out


def make_embedder(prefer: str = "auto", cache_dir: str | Path | None = None) -> Any:
    """Factory: return best available embedder.

    prefer:
        - "sbert"   : try sentence-transformers, raise if not available
        - "hash"    : always use hash
        - "auto"    : try sbert, fall back to hash silently
    """
    if prefer == "hash":
        return HashEmbedderFallback()

    if prefer == "sbert":
        return SBERTEmbedder(cache_dir=cache_dir)

    # auto
    try:
        import sentence_transformers  # noqa
        return SBERTEmbedder(cache_dir=cache_dir)
    except ImportError:
        logger.info("sentence-transformers not available, using hash fallback")
        return HashEmbedderFallback()
