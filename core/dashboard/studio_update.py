#!/usr/bin/env python3
"""ikaros-dashboard studio-update backend (loadable from wrapper, untracked).

This module is NOT imported by server.py directly.  It is loaded at
startup by bin/core/dashboard-patched.py which monkey-patches the
update routes into DashboardHandler.

Keep everything self-contained — no edits to git-tracked files required.
"""

from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import threading
import time

log = logging.getLogger("ikaros-dashboard.studio-update")

# ── resolve paths (no hardcoded drive letter) ─────────────────────────
HERMES_ROOT = pathlib.Path(
    os.environ.get("IKAROS_ROOT")
    or os.environ.get("HERMES_ROOT")
    or __file__
).resolve()
if not HERMES_ROOT.is_absolute():
    HERMES_ROOT = pathlib.Path(__file__).resolve().parent

STUDIO_UPDATE_SCRIPT = HERMES_ROOT / "bin" / "studio-local-update.bat"
LOG_PATH = HERMES_ROOT / "data" / "logs" / "studio-update.log"

# ── state (shared with the patched handler) ────────────────
studio_update_lock = threading.Lock()       # POST reentrancy
_studio_update_mlock = threading.Lock()     # memory-state lock
_studio_updating = False
_studio_update_lines: list[str] = []


def run_studio_local_update() -> None:
    """Background thread: launch the bat, tail its log file, and restart
    Studio when it finishes.

    Log lines include timestamps relative to start so the dashboard UI
    shows clear timing context for diagnosing failures."""
    # late imports avoid circular dependency at module load
    from server import ENV, ROOT, component_start, component_stop  # noqa: E402

    global _studio_updating, _studio_update_lines
    with _studio_update_mlock:
        _studio_update_lines = []
        _studio_updating = True
    t0 = time.time()

    def _ts() -> str:
        return time.strftime("%H:%M:%S")

    def _log(msg: str) -> str:
        t = time.time() - t0
        line = f"[{_ts()}] [{t:.1f}s] {msg}"
        log.info("[studio-update] %s", msg)
        with _studio_update_mlock:
            _studio_update_lines.append(line)
        return line

    try:
        script = STUDIO_UPDATE_SCRIPT
        if not script.exists():
            _log(f"ERROR: update script not found: {script}")
            _log(f"       HERMES_ROOT={HERMES_ROOT}")
            return

        # prepend runtime/node to PATH and inject IKAROS_PYTHON
        cenv = dict(os.environ)
        node_dir = str(HERMES_ROOT / "runtime" / "node")
        cenv["PATH"] = node_dir + os.pathsep + cenv.get("PATH", "")
        cenv["IKAROS_PYTHON"] = str(HERMES_ROOT / "runtime" / "portable-python" / "python.exe")

        _log(f"Launching: {script}")
        _log(f"PATH[0]={node_dir}")
        _log(f"IKAROS_PYTHON={cenv.get('IKAROS_PYTHON', '(unset)')}")

        try:
            proc = subprocess.Popen(
                ["cmd", "/c", str(script)],
                cwd=str(HERMES_ROOT),
                env=cenv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            _log(f"FAILED to launch script: {e}")
            return

        _log(f"Script PID={proc.pid} — tailing {LOG_PATH}")

        # tail the bat's own log file (it writes to LOG_PATH)
        seen = 0
        while True:
            try:
                with open(str(LOG_PATH), "r", encoding="utf-8", errors="replace") as lf:
                    lines = lf.read().splitlines()
            except FileNotFoundError:
                lines = []
            new_lines = lines[seen:]
            if new_lines:
                with _studio_update_mlock:
                    _studio_update_lines.extend(new_lines)
                seen += len(new_lines)
            if proc.poll() is not None:
                try:
                    with open(str(LOG_PATH), "r", encoding="utf-8", errors="replace") as lf:
                        tail_lines = lf.read().splitlines()
                except FileNotFoundError:
                    tail_lines = []
                tail = tail_lines[seen:]
                if tail:
                    with _studio_update_mlock:
                        _studio_update_lines.extend(tail)
                break
            time.sleep(0.5)

        elapsed = time.time() - t0
        _log(f"Script exited code={proc.returncode} duration={elapsed:.1f}s")

        # restart Studio after the script finishes
        _log("Stopping Studio for restart...")
        try:
            component_stop("studio", ENV)
            _log("Studio stopped")
        except Exception as e:
            _log(f"WARN stop studio: {e}")

        time.sleep(3)

        _log("Starting Studio (patched code)...")
        try:
            component_start("studio", ENV, False)
            _log("Studio started")
        except Exception as e:
            _log(f"WARN start studio: {e}")

        total = time.time() - t0
        _log(f"Update flow complete (total {total:.1f}s)")
    finally:
        with _studio_update_mlock:
            _studio_updating = False
