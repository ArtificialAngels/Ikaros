"""
Ikaros Desktop Pet — Detached bootstrap.
Spawns the real pet process with DETACHED_PROCESS so it survives
parent terminal closure. Logs all output to data/logs/ikaros-pet.log.

NOTE on detached mode:
A truly detached process runs in its own session without an interactive
desktop. Qt.exec() + QWebEngineView need a window station with a desktop,
so the pet WILL crash in pure detached mode. The intended UX is:
  - Double-click start.bat in a user session, OR
  - Rely on HKCU Run autostart at login (already registered)
We keep this detached launcher for graceful fallback / future fixes
(e.g. when we figure out how to attach to the user's desktop session
from a service-context process).
"""

import os
import sys
import time
import subprocess
import ctypes
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).parent
HERMES_ROOT = HERE.parent.parent
PY = HERMES_ROOT / "portable-python" / "python.exe"
MAIN = HERE / "main.py"
LOG = HERMES_ROOT / "data" / "logs" / "ikaros-pet.log"

# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP = 0x00000008 | 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

def main():
    HERMES_ROOT.joinpath("data", "logs").mkdir(parents=True, exist_ok=True)

    # Open log file (append)
    flog = open(LOG, "a", encoding="utf-8")
    flog.write(f"\n\n=== launch {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    flog.flush()

    # PYTHONUNBUFFERED=1: stdout flushes immediately so the parent log
    # shows progress without waiting for buffer fill. Without this, any
    # crash during startup loses buffered log lines.
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [str(PY), str(MAIN)]

    # Spawn with DETACHED_PROCESS — survives parent terminal closure.
    # Pipe stdout/stderr to the same log file for debugging.
    if sys.platform == "win32":
        creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=flog,
            stderr=subprocess.STDOUT,  # merge stderr into stdout (= log)
            cwd=str(HERE),
            env=env,
            creationflags=creationflags,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=flog,
            stderr=subprocess.STDOUT,
            cwd=str(HERE),
            env=env,
        )

    flog.write(f"spawned PID {proc.pid}\n")
    flog.flush()

    # Wait briefly to confirm startup
    time.sleep(2)
    flog.write(f"parent exiting, child {proc.pid} should keep running\n")
    flog.close()

if __name__ == "__main__":
    main()