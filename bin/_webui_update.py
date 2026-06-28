#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bin/_webui_update.py — Perform hermes-web-ui npm update.

Invoked by:
  - bin/hermes-watchdog.py (when data/webui/needs-update.json marker appears)
  - modules/webui_proxy/webui_proxy.py (auto-update daemon thread, direct spawn)

What it does:
  1. Acquires upgrading.lock (~/.hermes-web-ui/upgrading.lock) so the
     watchdog skips webui port checks during the update.
  2. Stops the running webui (calls modules/webui/stop.ps1 via supervisor).
  3. Runs `npm install -g hermes-web-ui@latest` to update the package.
  4. Starts webui again (calls modules/webui/start.ps1 via supervisor).
  5. Deletes the needs-update.json marker.
  6. Releases the upgrading.lock.

Why this script exists separately:
  The npm install takes 30-90s and must happen while webui is stopped
  (Windows EBUSY if Node holds the files). Running it inline in the
  watchdog would block the 10s tick loop; running it inline in
  webui_proxy would block the proxy's event loop. A separate script
  in its own process avoids both problems.

Lock format (upgrading.lock):
  "<created_epoch>|<deadline_epoch>"
  The watchdog checks deadline > now() to decide if the lock is active.
  We use a 10-minute deadline (600s) which is generous for npm install.

Exit codes:
  0 = success (or nothing to do)
  1 = failure (logged to stderr)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ============================================================
# Path resolution (mirrors watchdog / supervisor)
# ============================================================

HERE = Path(__file__).resolve()
HERMES_ROOT = HERE.parent.parent

# upgrading.lock lives next to the npm global prefix
_USER_HOME = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
_UPGRADING_LOCK = _USER_HOME / ".hermes-web-ui" / "upgrading.lock"
_LOCK_TTL_S = 600  # 10 minutes; generous for slow npm installs

# needs-update.json marker (written by webui_proxy or user action)
_UPDATE_MARKER = HERMES_ROOT / "data" / "webui" / "needs-update.json"

# Paths for stop/start
SUPERVISOR = HERMES_ROOT / "bin" / "hermes-supervisor.py"
PYTHON_EXE = HERMES_ROOT / "portable-python" / "python.exe"
STOP_PS1 = HERMES_ROOT / "modules" / "webui" / "stop.ps1"
START_PS1 = HERMES_ROOT / "modules" / "webui" / "start.ps1"

# npm / node
NODE_EXE = HERMES_ROOT / "runtime" / "node23" / "node.exe"
NPM_CMD = HERMES_ROOT / "runtime" / "node23" / "npm.cmd"


def _acquire_lock() -> bool:
    """Write upgrading.lock. Returns True on success."""
    try:
        _UPGRADING_LOCK.parent.mkdir(parents=True, exist_ok=True)
        now = int(time.time())
        deadline = now + _LOCK_TTL_S
        _UPGRADING_LOCK.write_text(f"{now}|{deadline}", encoding="utf-8")
        print(f"[webui_update] lock acquired: {_UPGRADING_LOCK} (ttl={_LOCK_TTL_S}s)")
        return True
    except OSError as e:
        print(f"[webui_update] lock write failed: {e}", file=sys.stderr)
        return False


def _release_lock() -> None:
    """Remove upgrading.lock. Best-effort."""
    try:
        _UPGRADING_LOCK.unlink(missing_ok=True)
        print("[webui_update] lock released")
    except OSError as e:
        print(f"[webui_update] lock release failed: {e}", file=sys.stderr)


def _stop_webui() -> bool:
    """Stop the running webui. Uses supervisor --stop if available,
    otherwise calls stop.ps1 directly."""
    # Try supervisor first (cleanest: handles PID tracking)
    if SUPERVISOR.is_file() and PYTHON_EXE.is_file():
        try:
            r = subprocess.run(
                [str(PYTHON_EXE), str(SUPERVISOR), "--stop", "webui"],
                capture_output=True, text=True, timeout=30,
                cwd=str(HERMES_ROOT),
            )
            if r.returncode == 0:
                print("[webui_update] webui stopped via supervisor")
                return True
            print(f"[webui_update] supervisor --stop webui rc={r.returncode}: {r.stderr[:200]}")
        except subprocess.TimeoutExpired:
            print("[webui_update] supervisor --stop timeout (30s)")
        except Exception as e:
            print(f"[webui_update] supervisor --stop error: {e}")

    # Fallback: call stop.ps1 directly via PowerShell
    if STOP_PS1.is_file():
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(STOP_PS1)],
                capture_output=True, text=True, timeout=20,
                cwd=str(HERMES_ROOT),
            )
            if r.returncode == 0:
                print("[webui_update] webui stopped via stop.ps1")
                return True
            print(f"[webui_update] stop.ps1 rc={r.returncode}: {r.stderr[:200]}")
        except subprocess.TimeoutExpired:
            print("[webui_update] stop.ps1 timeout (20s)")
        except Exception as e:
            print(f"[webui_update] stop.ps1 error: {e}")

    return False


def _start_webui() -> bool:
    """Start webui. Uses supervisor --start-module if available,
    otherwise calls start.ps1 directly."""
    if SUPERVISOR.is_file() and PYTHON_EXE.is_file():
        try:
            r = subprocess.run(
                [str(PYTHON_EXE), str(SUPERVISOR), "--start-module", "webui"],
                capture_output=True, text=True, timeout=90,
                cwd=str(HERMES_ROOT),
            )
            if r.returncode == 0:
                print("[webui_update] webui started via supervisor")
                return True
            print(f"[webui_update] supervisor --start-module webui rc={r.returncode}: {r.stderr[:200]}")
        except subprocess.TimeoutExpired:
            print("[webui_update] supervisor --start-module timeout (90s)")
        except Exception as e:
            print(f"[webui_update] supervisor --start-module error: {e}")

    # Fallback: call start.ps1 directly
    if START_PS1.is_file():
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(START_PS1)],
                capture_output=True, text=True, timeout=60,
                cwd=str(HERMES_ROOT),
            )
            if r.returncode == 0:
                print("[webui_update] webui started via start.ps1")
                return True
            print(f"[webui_update] start.ps1 rc={r.returncode}: {r.stderr[:200]}")
        except subprocess.TimeoutExpired:
            print("[webui_update] start.ps1 timeout (60s)")
        except Exception as e:
            print(f"[webui_update] start.ps1 error: {e}")

    return False


def _run_npm_update() -> bool:
    """Run npm install -g hermes-web-ui@latest. Returns True on success."""
    # Determine npm command. On Windows with runtime/node23, npm.cmd is
    # the standard entry point.
    npm = str(NPM_CMD) if NPM_CMD.is_file() else "npm"
    node_dir = str(HERMES_ROOT / "runtime" / "node23")

    env = os.environ.copy()
    env["PATH"] = node_dir + ";" + env.get("PATH", "")

    try:
        print(f"[webui_update] running: {npm} install -g hermes-web-ui@latest")
        r = subprocess.run(
            [npm, "install", "-g", "hermes-web-ui@latest"],
            capture_output=True, text=True, timeout=600,
            cwd=str(HERMES_ROOT),
            env=env,
        )
        if r.stdout:
            for line in r.stdout.splitlines()[-20:]:
                print(f"[webui_update]   {line}")
        if r.returncode == 0:
            print("[webui_update] npm install succeeded")
            return True
        print(f"[webui_update] npm install failed (rc={r.returncode})")
        if r.stderr:
            for line in r.stderr.splitlines()[-10:]:
                print(f"[webui_update]   stderr: {line}")
        return False
    except subprocess.TimeoutExpired:
        print("[webui_update] npm install timeout (600s)")
        return False
    except Exception as e:
        print(f"[webui_update] npm install error: {e}")
        return False


def _clear_marker() -> None:
    """Delete the needs-update.json marker. Best-effort."""
    try:
        _UPDATE_MARKER.unlink(missing_ok=True)
        print("[webui_update] update marker cleared")
    except OSError as e:
        print(f"[webui_update] marker clear failed: {e}", file=sys.stderr)


def main() -> int:
    print("[webui_update] === starting ===")
    print(f"[webui_update] HERMES_ROOT={HERMES_ROOT}")

    # 1. Acquire lock
    if not _acquire_lock():
        return 1

    stop_ok = False
    npm_ok = False
    start_ok = False

    try:
        # 2. Stop webui
        time.sleep(1)  # brief pause to let supervisor finish any in-flight restart
        stop_ok = _stop_webui()
        if not stop_ok:
            print("[webui_update] stop failed; aborting update")
            # finally block will release the lock
            return 1
        time.sleep(2)  # let Node release file handles

        # 3. npm install
        npm_ok = _run_npm_update()

        # 4. Start webui (even if npm failed — bring the old version back up)
        start_ok = _start_webui()
        if not start_ok:
            print("[webui_update] WARNING: webui start may have failed")

        # 5. Clear marker only on npm success; keep it on failure so the
        #    next scheduled run retries the update.
        if npm_ok:
            _clear_marker()
        else:
            print("[webui_update] npm failed; keeping marker for retry")

    finally:
        # 6. Release lock
        _release_lock()

    print(f"[webui_update] === done === stop={stop_ok} npm={npm_ok} start={start_ok}")
    return 0 if npm_ok else 1


if __name__ == "__main__":
    sys.exit(main())
