#!/usr/bin/env python3
"""ikaros-dashboard studio-update backend (loadable from wrapper, untracked).

This module is NOT imported by server.py directly.  It is loaded at
startup by bin/ikaros-dashboard-patched.py which monkey-patches the
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
    HERMES_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

STUDIO_UPDATE_SCRIPT = HERMES_ROOT / "bin" / "studio-local-update.bat"
LOG_PATH = HERMES_ROOT / "data" / "logs" / "studio-update.log"

# ── state (shared with the patched handler) ────────────────
studio_update_lock = threading.Lock()       # POST reentrancy
_studio_update_mlock = threading.Lock()     # memory-state lock
_studio_updating = False
_studio_update_lines: list[str] = []


def run_studio_local_update() -> None:
    """Background thread: launch the bat, tail its log file, and restart
    Studio when it finishes."""
    # late imports avoid circular dependency at module load
    from server import ENV, ROOT, component_start, component_stop  # noqa: E402

    global _studio_updating, _studio_update_lines
    with _studio_update_mlock:
        _studio_update_lines = []
        _studio_updating = True
    try:
        script = STUDIO_UPDATE_SCRIPT
        if not script.exists():
            msg = "update script not found: %s" % script
            log.error(msg)
            with _studio_update_mlock:
                _studio_update_lines.append(msg)
            return

        # prepend runtime/node to PATH as a fallback
        # (the bat itself also calls Ikaros-environment/init.bat for full
        # CUDA/node/git/python registration)
        cenv = dict(os.environ)
        node_dir = str(HERMES_ROOT / "runtime" / "node")
        cenv["PATH"] = node_dir + os.pathsep + cenv.get("PATH", "")

        log.info("[studio-update] running %s", script)
        try:
            proc = subprocess.Popen(
                ["cmd", "/c", str(script)],
                cwd=str(HERMES_ROOT),
                env=cenv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            msg = "[studio-update] failed to launch script: %s" % e
            log.error(msg)
            with _studio_update_mlock:
                _studio_update_lines.append(msg)
            return

        # tail the bat's own log file (it truncates at start, then appends)
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
                        lines = lf.read().splitlines()
                except FileNotFoundError:
                    lines = []
                tail = lines[seen:]
                if tail:
                    with _studio_update_mlock:
                        _studio_update_lines.extend(tail)
                break
            time.sleep(0.5)
        log.info("[studio-update] script exited code=%s", proc.returncode)

        # restart Studio after the script finishes
        try:
            component_stop("studio", ENV)
        except Exception as e:
            log.warning("[studio-update] stop studio failed: %s", e)
        time.sleep(3)
        try:
            component_start("studio", ENV, False)
        except Exception as e:
            log.warning("[studio-update] start studio failed: %s", e)
        with _studio_update_mlock:
            _studio_update_lines.append("[studio-update] flow done — Studio restart attempted")
    finally:
        with _studio_update_mlock:
            _studio_updating = False
