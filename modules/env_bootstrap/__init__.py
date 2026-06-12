"""Hermes ``env_bootstrap`` module.

Responsibilities:

* Detect the GPU (NVIDIA / AMD / Intel / Vulkan) and the NVIDIA driver
  version on the host.
* Map the detected driver version to a compatible CUDA runtime
  (currently 11.8 / 12.4 / 13.0; see ``runtime/cuda/<ver>/manifest.json``).
* Download / verify the matching CUDA runtime DLLs into
  ``runtime/cuda/<ver>/`` on demand (idempotent).
* Pick the right ``llama-server-cuda-<ver>.exe`` for the local GPU.

Public entry points:

* ``gpu_detect.main()`` — the canonical CLI dispatcher.
* ``python -m modules.env_bootstrap`` — the same thing, exposed as a
  ``python -m`` package.
* ``start.ps1`` — Windows launcher used by the supervisor.

Note: we deliberately do NOT eagerly re-export ``gpu_detect`` here.
Doing so would cause a Python ``runpy`` warning when invoked as
``python -m modules.env_bootstrap.gpu_detect`` (the ``__init__`` is
imported first, the eager import puts ``gpu_detect`` in
``sys.modules`` *before* runpy begins executing the module as
``__main__``). Callers should import the submodule directly:
``from modules.env_bootstrap.gpu_detect import ...``.
"""

__all__: list[str] = []
