"""Hermes ``model_manager`` module.

Self-contained Python package that owns GGUF model discovery, metadata
extraction, multi-source download (gopeed / aria2 / direct), HF/HF-mirror
URL rewriting, and VRAM-aware layer offload hints.

Public entry points:

* ``manager.main()`` — unified CLI (list / info / download / mirror).
* ``python -m modules.model_manager.manager`` — ``python -m`` entrypoint
  for the CLI (subcommands: ``list``, ``info <path>``, ``download <url>``,
  ``import-ollama``).
* ``gguf.list_gguf_models()`` / ``gguf.parse_gguf_meta()`` — used by
  the bridge server, ``bin/hermes-models.py`` and the WebUI launcher
  dropdown.
* ``downloader.DownloadManager`` / ``GopeedClient`` — async download.
* ``mirror.mirror_url()`` — HF -> HF-mirror URL rewriting.

Note: we deliberately do NOT eagerly re-export submodules here. Doing so
would cause a ``runpy`` warning when invoked as
``python -m modules.model_manager.manager`` (the package's ``__init__``
runs first, the eager import puts ``manager`` in ``sys.modules`` *before*
runpy begins executing it as ``__main__``). Callers should import the
submodule they actually need.
"""

__all__: list[str] = []
