"""
Hermes - Model manager unified entry point.

This module aggregates the model-management submodules into one importable
surface so external callers (CLI, WebUI, bridge) can do:

    from modules.model_manager.manager import (
        DownloadManager, list_gguf_models, parse_gguf_meta,
        mirror_url, get_mirror_config,
    )

Subcommand dispatch is also implemented here so module.json can point
`runtime.script` at this file:

    python -m modules.model_manager.manager list
    python -m modules.model_manager.manager info <path>
    python -m modules.model_manager.manager download <url> [--dest DIR]
    python -m modules.model_manager.manager import-ollama

The split into per-responsibility files (`downloader.py`, `gguf.py`,
`mirror.py`) keeps each one testable in isolation.
"""
from __future__ import annotations
import sys
from pathlib import Path

# Make sure sibling modules/ packages resolve when invoked via
# `python -m modules.model_manager.manager ...` from the project root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Re-exports from the downloader submodule (Phase 10 ready).
from modules.model_manager.downloader import (  # noqa: E402
    DownloadManager,
    GopeedClient,
    download_file,
    find_aria2c,
)

# Re-exports from the GGUF submodule (migrated in Phase 11).
from modules.model_manager.gguf import (  # noqa: E402
    list_gguf_models,
    parse_gguf_meta,
)

# Re-exports from the mirror submodule (migrated in Phase 11).
from modules.model_manager.mirror import (  # noqa: E402
    mirror_url,
    get_mirror_config,
)

__all__ = [
    "DownloadManager", "GopeedClient", "download_file", "find_aria2c",
    "list_gguf_models", "parse_gguf_meta",
    "mirror_url", "get_mirror_config",
]


def _cmd_list(args: list[str]) -> int:
    models_dir = Path(args[0]) if args else _PROJECT_ROOT / "data" / "models"
    import json
    print(json.dumps(list_gguf_models(models_dir), indent=2, default=str))
    return 0


def _cmd_info(args: list[str]) -> int:
    if not args:
        print("usage: manager info <gguf-path>")
        return 2
    import json
    print(json.dumps(parse_gguf_meta(Path(args[0])), indent=2, default=str))
    return 0


def _cmd_download(args: list[str]) -> int:
    if not args:
        print("usage: manager download <url> [--dest DIR]")
        return 2
    url = args[0]
    dest = _PROJECT_ROOT / "data" / "models"
    if "--dest" in args:
        idx = args.index("--dest")
        if idx + 1 < len(args):
            dest = Path(args[idx + 1])
    dm = DownloadManager()
    target = dm.download(url, dest)
    print(f"downloaded -> {target}")
    return 0


def _cmd_import_ollama(_args: list[str]) -> int:
    # Phase 12: the legacy helper script was removed. Future versions can
    # either re-implement this in-tree or point users at the official
    # Ollama `ollama cp` workflow.
    print("[manager] import-ollama is no longer supported.")
    print("[manager] use `ollama cp <source-model> <dest-model>` then point")
    print("[manager] the WebUI at the resulting GGUF under data/models/.")
    return 0


_COMMANDS = {
    "list": _cmd_list,
    "info": _cmd_info,
    "download": _cmd_download,
    "import-ollama": _cmd_import_ollama,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: manager [list|info <path>|download <url>|import-ollama]")
        return 0 if argv else 2
    cmd, args = argv[0], argv[1:]
    fn = _COMMANDS.get(cmd)
    if fn is None:
        print(f"unknown command: {cmd}")
        print("usage: manager [list|info <path>|download <url>|import-ollama]")
        return 2
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
