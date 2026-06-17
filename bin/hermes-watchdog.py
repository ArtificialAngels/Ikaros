#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hermes Watchdog — standalone auto-restart daemon.
=================================================

Why a separate process:
    The supervisor exits immediately after spawning services (so `hermes-all.bat`
    can close its window). But services can crash mid-run, and we need a process
    that *always lives* to watch the ports and restart dead ones. A detached
    child process fits this perfectly: parent exit doesn't affect it, services
    that die get revived.

Responsibilities:
    1. Every 10s, scan `discover_modules()` for all type=service modules.
    2. Probe each service's port — if dead, call `supervisor --restart <name>`.
    3. If this watchdog itself dies, the next `hermes-all.bat` respawns it.

Singleton:
    Writes its own PID to `data/logs/hermes-watchdog.pid`. On startup checks:
      - file missing  -> we are first, continue
      - file present, PID dead -> stale, delete and continue
      - file present, PID alive -> exit 0 (avoid forking multiple watchdogs)

Graceful exit:
    Ctrl-C -> KeyboardInterrupt -> unlink PID file -> exit 0
    taskkill /F /PID <pid> -> dies immediately; supervisor `cmd_watchdog_stop`
    cleans the PID file.

Why no `import supervisor`:
    Supervisor is a main-script; importing it triggers its argparse. Duplicating
    `discover_modules()` / `check_port()` (~30 lines each) is cleaner than
    splitting it into a module.

Usage:
    Usually auto-launched detached by `bin/hermes-supervisor.py --start`.
    Manual debug: `portable-python\python.exe bin\hermes-watchdog.py`.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# ============================================================
# HERMES_ROOT resolution (mirrors supervisor; standalone)
# ============================================================

HERE = Path(__file__).resolve()


def _resolve_hermes_root(here: Path) -> Path:
    env_root = os.environ.get("HERMES_ROOT", "").strip()
    if env_root:
        p = Path(env_root).resolve()
        if (p / "portable-python" / "python.exe").is_file():
            return p
    resolver = here.parent / "hermes-root.py"
    py = here.parent / "portable-python" / "python.exe"
    if resolver.is_file() and py.is_file():
        try:
            r = subprocess.run(
                [str(py), str(resolver), "resolve"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                p = Path(r.stdout.strip()).resolve()
                if p.is_dir():
                    return p
        except Exception:
            pass
    return here.parent.parent


HERMES_ROOT = _resolve_hermes_root(HERE)
MODULES_DIR = HERMES_ROOT / "modules"
LOG_DIR = HERMES_ROOT / "data" / "logs"
PYTHON_EXE = HERMES_ROOT / "portable-python" / "python.exe"
SUPERVISOR = HERMES_ROOT / "bin" / "hermes-supervisor.py"

PID_FILE = LOG_DIR / "hermes-watchdog.pid"

# Watchdog cycle (seconds). Too short wastes CPU; too long delays restart.
INTERVAL_S = 10
# Minimum gap between two restarts of the same service (prevents tight loops
# if start.ps1 itself is broken).
RESTART_COOLDOWN_S = 30

# FIX 2026-06-17: when launcher is mid-update it writes ~/.hermes-web-ui/upgrading.lock.
# Webui :8649 will go down briefly (npm renames its dir → process exits) and
# the watchdog MUST NOT race npm by restarting the old webui. If the lock
# is present and unexpired we skip webui checks; other services (bridge,
# llm_engine, webui_proxy) are unaffected. Lock is auto-ignored past its
# deadline so a crashed launcher never strands the watchdog.
UPGRADING_LOCK = (Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
                  / ".hermes-web-ui" / "upgrading.lock")


def is_upgrading_lock_active() -> bool:
    """Return True if a fresh upgrading.lock is present. Stale or absent -> False."""
    try:
        if not UPGRADING_LOCK.is_file():
            return False
        line = UPGRADING_LOCK.read_text(encoding="utf-8", errors="replace").strip()
        # format: "<created>|<deadline>" seconds since epoch
        if "|" not in line:
            return False  # legacy / malformed — treat as not upgrading
        _, deadline_s = line.split("|", 1)
        deadline = int(deadline_s)
        return int(time.time()) < deadline
    except Exception:
        return False

# ============================================================
# Data model (supervisor's slimmer version; only fields watchdog needs)
# ============================================================


@dataclass
class Module:
    name: str
    path: Path
    type: str
    port: Optional[int] = None
    host: str = "127.0.0.1"


def discover_modules() -> Dict[str, Module]:
    """Scan modules/*/module.json; only fields the watchdog cares about."""
    modules: Dict[str, Module] = {}
    if not MODULES_DIR.is_dir():
        return modules
    for entry in sorted(MODULES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        json_path = entry / "module.json"
        if not json_path.is_file():
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        net = data.get("network") or {}
        modules[data.get("name", entry.name)] = Module(
            name=data.get("name", entry.name),
            path=entry,
            type=data.get("type", "service"),
            port=net.get("port"),
            host=net.get("host", "127.0.0.1"),
        )
    return modules


def check_port(host: str, port: int, timeout_s: float = 1.0) -> bool:
    """Non-blocking TCP liveness probe."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


# ============================================================
# Singleton check
# ============================================================


def claim_singleton() -> bool:
    """Claim the singleton watchdog slot. Returns False if another instance is alive.

    Pitfall: supervisor pre-writes PID_FILE with our PID *before* Popen returns,
    so a naive alive-check would see "another instance" (= ourselves) and exit.
    We skip the alive-check when the stored PID equals our own (either freshly
    pre-written by supervisor, or stale from a previous crash) — just overwrite.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if PID_FILE.is_file():
        try:
            old_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = None
        if old_pid is not None and old_pid != os.getpid() and os.name == "nt":
            # only check liveness when the stored PID is actually someone else
            try:
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {old_pid}", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                if str(old_pid) in r.stdout:
                    print(f"[watchdog] another instance running (pid {old_pid}); exiting.",
                          file=sys.stderr)
                    return False
            except Exception:
                pass
        # stale or self -> delete and recreate
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_singleton() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ============================================================
# Restart service
# ============================================================


def restart_service(name: str) -> bool:
    """Restart a single service via `supervisor --restart`."""
    if not SUPERVISOR.is_file():
        print(f"[watchdog] supervisor not found: {SUPERVISOR}", file=sys.stderr)
        return False
    try:
        r = subprocess.run(
            [str(PYTHON_EXE), str(SUPERVISOR), "--restart", name],
            cwd=str(HERMES_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        # append output to log
        log_path = LOG_DIR / "hermes-watchdog.log"
        with log_path.open("a", encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            f.write(f"\n[{ts}] restart {name} rc={r.returncode}\n")
            if r.stdout:
                f.write("--- stdout ---\n" + r.stdout + "\n")
            if r.stderr:
                f.write("--- stderr ---\n" + r.stderr + "\n")
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"[watchdog] restart {name} timeout", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[watchdog] restart {name} error: {e}", file=sys.stderr)
        return False


# ============================================================
# Main loop
# ============================================================


def run() -> int:
    if not claim_singleton():
        return 0

    print(f"[watchdog] started (pid {os.getpid()})", flush=True)
    print(f"[watchdog] interval: {INTERVAL_S}s  cooldown: {RESTART_COOLDOWN_S}s",
          flush=True)
    print(f"[watchdog] supervising: {HERMES_ROOT}", flush=True)

    # name -> timestamp of last restart (for cooldown)
    last_restart: Dict[str, float] = {}

    try:
        while True:
            time.sleep(INTERVAL_S)
            # FIX 2026-06-17: while launcher is mid-update, skip webui checks.
            # npm install renames the webui dir → its process dies → port
            # goes down. Restarting the old webui at that moment EBUSYs npm.
            skip_modules: set[str] = set()
            if is_upgrading_lock_active():
                skip_modules.add("webui")
                # only announce once per cycle
                if not getattr(run, "_upgrade_skip_announced", False):
                    print("[watchdog] upgrading.lock active — skipping webui check",
                          flush=True)
                    run._upgrade_skip_announced = True
            else:
                run._upgrade_skip_announced = False

            modules = discover_modules()
            for name, m in modules.items():
                if m.type != "service" or not m.port:
                    continue
                if name in skip_modules:
                    continue
                if check_port(m.host, m.port, timeout_s=1.0):
                    continue
                # cooldown: don't restart the same service repeatedly in a short
                # window (likely start.ps1 itself is broken, not transient crash)
                now = time.time()
                if now - last_restart.get(name, 0) < RESTART_COOLDOWN_S:
                    continue
                print(f"[watchdog] {name} (:{m.port}) DOWN — restarting",
                      flush=True)
                last_restart[name] = now
                if restart_service(name):
                    print(f"[watchdog] {name} restarted OK", flush=True)
                else:
                    print(f"[watchdog] {name} restart FAILED (see log)",
                          flush=True)
    except KeyboardInterrupt:
        print("[watchdog] KeyboardInterrupt — exiting", flush=True)
    finally:
        release_singleton()
    return 0


if __name__ == "__main__":
    sys.exit(run())
