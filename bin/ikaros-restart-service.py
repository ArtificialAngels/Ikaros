#!/usr/bin/env python
"""ikaros-restart-service.py — Graceful Hermes Dashboard restart with handoff.

Usage:
    python bin/ikaros-restart-service.py [--reason "重启原因"]

Handoff flow:
    1. Saves current conversation context to V5_DATA/service_handoff.json
    2. Kills the Hermes Dashboard process (:9119)
    3. Restarts Hermes Dashboard
    4. On next session start, MemoryProvider reads handoff and continues
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

IKAROS_ROOT = Path(os.environ.get("IKAROS_ROOT",
                    Path(__file__).resolve().parent))
V5_DATA = IKAROS_ROOT / "core" / "v5" / "data" / "v5"
HANDOFF_FILE = V5_DATA / "service_handoff.json"
HERMES_BIN = IKAROS_ROOT / "runtime" / "portable-python" / "Scripts" / "hermes.exe"
DASHBOARD_PORT = 9119

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [restart] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ikaros.restart")


def save_handoff(reason: str = "service restart",
                 context: str = "",
                 state: dict | None = None) -> None:
    """Save conversation handoff so the next session can continue seamlessly."""
    V5_DATA.mkdir(parents=True, exist_ok=True)
    handoff = {
        "saved_at": time.time(),
        "reason": reason,
        "conversation_context": (context or "").strip()[:500],
        "state": state or {},
        "restart_count": 0,
    }
    # Increment restart counter if handoff already exists
    if HANDOFF_FILE.is_file():
        try:
            old = json.loads(HANDOFF_FILE.read_text(encoding="utf-8"))
            handoff["restart_count"] = old.get("restart_count", 0) + 1
        except Exception:
            pass
    HANDOFF_FILE.write_text(json.dumps(handoff, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    logger.info("Handoff saved: reason=%s", reason)


def kill_process_on_port(port: int) -> bool:
    """Find and kill the process listening on port. Returns True if killed."""
    try:
        import subprocess
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                if pid and pid.isdigit():
                    os.kill(int(pid), signal.SIGTERM)
                    logger.info("Killed PID %s (port %d)", pid, port)
                    time.sleep(1)
                    return True
    except Exception as e:
        logger.warning("Failed to kill process on port %d: %s", port, e)
    return False


def start_dashboard() -> bool:
    """Start Hermes Dashboard on :9119. Returns True if started."""
    if not HERMES_BIN.is_file():
        logger.error("Hermes binary not found: %s", HERMES_BIN)
        return False
    try:
        subprocess.Popen(
            [str(HERMES_BIN), "dashboard", "--port", str(DASHBOARD_PORT)],
            cwd=str(IKAROS_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else 0,
        )
        logger.info("Dashboard started on :%d", DASHBOARD_PORT)
        return True
    except Exception as e:
        logger.error("Failed to start Dashboard: %s", e)
        return False


def wait_for_port(port: int, timeout: int = 30) -> bool:
    """Wait for port to be listening. Returns True if ready."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(1)
    return False


def restart(reason: str = "service restart",
            context: str = "",
            state: dict | None = None) -> dict:
    """Full restart cycle: save handoff → kill → start → wait."""
    logger.info("=== Restart cycle start ===")

    # 1. Save handoff
    save_handoff(reason=reason, context=context, state=state)

    # 2. Kill existing
    killed = kill_process_on_port(DASHBOARD_PORT)
    if not killed:
        logger.warning("No process found on :%d, starting fresh", DASHBOARD_PORT)

    # 3. Wait for port to free
    time.sleep(2)

    # 4. Start new
    started = start_dashboard()
    if not started:
        return {"ok": False, "error": "failed to start dashboard"}

    # 5. Wait for ready
    ready = wait_for_port(DASHBOARD_PORT, timeout=45)
    if not ready:
        return {"ok": False, "error": "dashboard not ready after 45s"}

    logger.info("=== Restart cycle complete ===")
    return {
        "ok": True,
        "port": DASHBOARD_PORT,
        "restart_count": (json.loads(HANDOFF_FILE.read_text("utf-8"))
                          .get("restart_count", 0)) if HANDOFF_FILE.is_file() else 0,
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Graceful Hermes Dashboard restart")
    parser.add_argument("--reason", default="service restart", help="Reason for restart")
    parser.add_argument("--context", default="", help="Current conversation context")
    parser.add_argument("--state", default=None, help="JSON state dict")
    parser.add_argument("--dry-run", action="store_true", help="Save handoff only, don't restart")
    args = parser.parse_args()

    state = json.loads(args.state) if args.state else None
    if args.dry_run:
        save_handoff(reason=args.reason, context=args.context, state=state)
        print(f"[DRY-RUN] Handoff saved to {HANDOFF_FILE}")
        return

    result = restart(reason=args.reason, context=args.context, state=state)
    if result["ok"]:
        print(f"✅ Dashboard restarted (port {result['port']})")
        if result.get("restart_count"):
            print(f"   Total restarts: {result['restart_count']}")
    else:
        print(f"❌ Restart failed: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
