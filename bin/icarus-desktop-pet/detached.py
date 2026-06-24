"""
Icarus Desktop Pet — Detached bootstrap.
Spawns the real pet process with DETACHED_PROCESS so it survives
parent terminal closure. Logs all output to data/logs/icarus-pet.log.
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
LOG = HERMES_ROOT / "data" / "logs" / "icarus-pet.log"

# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP = 0x00000008 | 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

def main():
    HERMES_ROOT.joinpath("data", "logs").mkdir(parents=True, exist_ok=True)

    # Open log file
    flog = open(LOG, "a", encoding="utf-8")
    flog.write(f"\n\n=== launch {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    flog.flush()

    # Build command
    cmd = [str(PY), str(MAIN)]

    # Spawn with DETACHED_PROCESS — survives parent terminal closure
    if sys.platform == "win32":
        creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(HERE),
            creationflags=creationflags,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(HERE),
        )

    flog.write(f"spawned PID {proc.pid}\n")
    flog.flush()

    # Wait briefly to confirm startup
    time.sleep(2)
    flog.write(f"parent exiting, child {proc.pid} should keep running\n")
    flog.close()

if __name__ == "__main__":
    main()
