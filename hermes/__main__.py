"""
Hermes CLI — thin wrapper that delegates to upstream `hermes_cli.main`.

Legacy entry point: `python -m hermes <cmd>` now forwards to the upstream
`hermes` CLI on the user's behalf. New users should prefer:

    python hermes-agent/cli.py <cmd>      # explicit upstream entry
    hermes <cmd>                          # upstream entry via PATH

This shim is kept so existing `bin/*.bat` launchers that call
`python -m hermes <cmd>` keep working without modification.
"""
import os
import sys


def main() -> int:
    # Lazy import so this shim is cheap to inspect (e.g. `python -m hermes --help`)
    from hermes_cli.main import main as upstream_main
    return upstream_main()


if __name__ == "__main__":
    sys.exit(main())