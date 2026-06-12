"""
Hermes - modules.env_bootstrap package entry point.

Lets callers run `python -m modules.env_bootstrap <cmd>` (shorthand for
`python -m modules.env_bootstrap.gpu_detect <cmd>`).

Available subcommands: status, check, install, recommend.
Pass `--cuda <11.8|12.4|13.0>` to force a specific CUDA version.
"""
from modules.env_bootstrap.gpu_detect import main as _gpu_detect_main

if __name__ == "__main__":
    raise SystemExit(_gpu_detect_main())
