"""Hermes ``model_manager`` module — minimal core.

Retains only the two submodules with real consumers:

* ``gguf.list_gguf_models()`` / ``gguf.parse_gguf_meta()`` — pure-Python
  GGUF v2/v3 header parser. Used by ``bridge/server.py`` as a cold-boot
  fallback when llama-server is not yet ready, and by
  ``bin/hermes-models.py`` for offline model listing.

* ``mirror.mirror_url()`` / ``mirror.get_mirror_config()`` — PyPI /
  HuggingFace / GitHub URL rewriting for slow-network users. Used by
  ``modules/env_bootstrap/gpu_detect.py`` when installing pip packages.

Everything else (download manager, manager CLI) was
removed in 2026-06-15 — llama-server's router mode natively covers
model listing, loading, LRU eviction, and per-model presets.
"""

__all__: list[str] = []
