"""Hermes ``model_manager`` module.

Submodules:

* ``llm_manager.LLMManager`` — unified model discovery, selection,
  persistence & switching.  Shared by desktop-pet, WebUI, supervisor.
* ``gguf.list_gguf_models()`` / ``gguf.parse_gguf_meta()`` — pure-Python
  GGUF v2/v3 header parser.
* ``mirror.mirror_url()`` / ``mirror.get_mirror_config()`` — PyPI /
  HuggingFace / GitHub URL rewriting for slow-network users.
"""

from modules.model_manager.llm_manager import LLMManager, FetchResult, PreloadResult

__all__ = ["LLMManager", "FetchResult", "PreloadResult"]
