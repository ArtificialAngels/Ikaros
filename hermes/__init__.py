"""
Hermes Agent — bridge package (this project).

This package is the THIN BRIDGE between:
  - upstream hermes-agent v0.16.0 (provides AIAgent, get_hermes_home, etc.)
  - upstream hermes-web-ui v0.6.12 (provides Web UI on :8648)
  - this project's portable USB-stick deployment (llama-server, configs,
    runtime bootstrap, GPU detection, file workspace, KB, memos, etc.)

After Phases 1-13 of the modular refactor, this package only contains
bridge-side glue that isn't naturally placed in `modules/`:

  - `config.py`        — yaml config with bash-style ${VAR:-default} expansion
  - `knowledge.py`     — markdown KB with chunking
  - `memos_client.py`  — memos note-taking client
  - `watchdog.py`      — process supervisor (kills orphans on parent exit)
  - `workspace.py`     — whitelisted file browser (HERMES_ROOT trust boundary)

Everything else has been moved into the `modules/` tree:

  - `modules/env_bootstrap/`   — GPU detection + CUDA runtime install (gpu_detect.py)
  - `modules/model_manager/`   — DownloadManager, GGUF parser, HF mirror
  - `modules/llm_engine/`      — llama-server router-mode launcher
  - `modules/bridge/`          — FastAPI HTTP bridge (port :7860)
  - `modules/webui/`           — upstream Vue 3 + Koa Web UI (port :8648)
  - `modules/supervisor/`      — PowerShell orchestrator for all of the above

For full agent / CLI / cron / kanban / memory / plugin functionality, prefer
direct upstream imports:

    from hermes_cli.main import main as upstream_cli
    from run_agent import AIAgent
    from hermes_state import SessionDB
    from cron.jobs import Job, JobStore
    from agent.memory_provider import MemoryProvider
    from hermes_constants import get_hermes_home

This package should stay small. If a feature can live upstream, push it
upstream (PR) instead of forking here.
"""
__version__ = "2.1.0-bridge"
__author__ = "Hermes Project (bridge layer)"
