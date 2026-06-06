r"""
Hermes Agent - Long-running task watchdog (uses wall-clock time).

Mavis memory rule: "When polling a long-running task, do NOT trust the
task's own 'completed' feedback. Use wall-clock time to detect hangs."

This module provides a generic polling helper that:
- Tracks elapsed wall-clock time
- Compares against an SLA (expected duration)
- Warns (via callback) when SLA is exceeded
- Raises TimeoutError on hard timeout
- Optionally checks if a process is still alive

Use:
    from hermes.watchdog import wait_process_alive, wait_with_progress

    # Wait for a PID to exit (e.g. llama-server start)
    wait_process_alive(
        pid=llama_pid,
        sla_seconds=90,           # Qwen3.5 35B-A3B typical: 30-90s
        timeout_seconds=300,      # hard kill at 5min
        on_tick=lambda elapsed, alive: print(f"  {elapsed:.0f}s alive={alive}"),
    )
"""
from __future__ import annotations
import os
import signal
import subprocess
import time
from typing import Callable, Optional


def is_pid_alive(pid: int) -> bool:
    """Check if a process exists (Windows + Unix)."""
    if pid is None or pid <= 0:
        return False
    try:
        if os.name == "nt":
            # Windows: OpenProcess with PROCESS_QUERY_LIMITED_INFORMATION
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        else:
            # Unix: kill -0
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return True  # process exists but no permission
    except Exception:
        return False


def wait_process_alive(pid: int,
                       sla_seconds: float = 60.0,
                       timeout_seconds: Optional[float] = None,
                       check_interval: float = 2.0,
                       on_tick: Optional[Callable[[float, bool], None]] = None,
                       ) -> bool:
    """Wait until a process exits (alive=False), or until SLA/timeout.

    Args:
        pid: process ID to monitor
        sla_seconds: expected duration; if exceeded, on_tick gets a 'slow' hint
        timeout_seconds: hard timeout (None = no limit)
        check_interval: how often to poll alive status
        on_tick: callback(elapsed_seconds, still_alive) every check_interval

    Returns:
        True if process exited within SLA
        False if exceeded SLA (still alive) — caller decides to kill or wait
    Raises:
        TimeoutError if timeout_seconds exceeded
    """
    start = time.time()
    deadline = start + timeout_seconds if timeout_seconds else None
    while True:
        elapsed = time.time() - start
        alive = is_pid_alive(pid)
        if on_tick:
            try:
                on_tick(elapsed, alive)
            except Exception:
                pass
        if not alive:
            return elapsed <= sla_seconds
        if deadline and time.time() >= deadline:
            raise TimeoutError(
                f"PID {pid} still alive after {elapsed:.1f}s "
                f"(timeout {timeout_seconds}s)"
            )
        time.sleep(check_interval)


def wait_with_progress(label: str,
                       sla_seconds: float,
                       timeout_seconds: Optional[float] = None,
                       check_interval: float = 5.0,
                       on_tick: Optional[Callable[[float, bool], None]] = None,
                       ) -> Callable[[], bool]:
    """Returns a polling function. Useful for 'wait until condition becomes true'.

    Example:
        poll = wait_with_progress("llama-server start", sla_seconds=60, timeout_seconds=180)
        while not poll():
            pass  # poll() updates internal state, returns True when SLA met
    """
    state = {"start": time.time(), "done": False, "exceeded_sla": False}

    def poll() -> bool:
        elapsed = time.time() - state["start"]
        alive = is_pid_alive(state.get("pid", -1)) if "pid" in state else True
        if on_tick:
            try:
                on_tick(elapsed, alive)
            except Exception:
                pass
        if elapsed > sla_seconds and not state["exceeded_sla"]:
            state["exceeded_sla"] = True
            print(f"[WATCHDOG] {label}: exceeded SLA {sla_seconds:.0f}s (now {elapsed:.0f}s)")
        if timeout_seconds and elapsed > timeout_seconds:
            raise TimeoutError(f"{label} timeout after {elapsed:.0f}s")
        if not alive and not state["done"]:
            state["done"] = True
            return True
        return state["done"]

    return poll


def wait_port_open(host: str, port: int,
                   sla_seconds: float = 30.0,
                   timeout_seconds: Optional[float] = 120.0,
                   check_interval: float = 1.0,
                   ) -> bool:
    """Wait for a TCP port to start listening. Returns True if open within SLA.

    Used for waiting on llama-server to bind :8080 etc.
    """
    import socket
    start = time.time()
    deadline = start + timeout_seconds if timeout_seconds else None
    while True:
        elapsed = time.time() - start
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            pass
        if elapsed > sla_seconds:
            print(f"[WATCHDOG] {host}:{port} not listening after {elapsed:.0f}s (SLA {sla_seconds:.0f}s)")
        if deadline and time.time() >= deadline:
            return False
        time.sleep(check_interval)


# ---- CLI for testing ----

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python -m hermes.watchdog port <host> <port>")
        print("       python -m hermes.watchdog alive <pid> [sla_sec] [timeout_sec]")
        sys.exit(1)
    mode = sys.argv[1]
    if mode == "port":
        host, port = sys.argv[2], int(sys.argv[3])
        ok = wait_port_open(host, port, sla_seconds=15, timeout_seconds=60)
        print(f"port {host}:{port} open: {ok}")
    elif mode == "alive":
        pid = int(sys.argv[2])
        sla = float(sys.argv[3]) if len(sys.argv) > 3 else 30
        to = float(sys.argv[4]) if len(sys.argv) > 4 else 60
        try:
            fast = wait_process_alive(pid, sla_seconds=sla, timeout_seconds=to,
                                      check_interval=1.0,
                                      on_tick=lambda e, a: print(f"  {e:5.1f}s alive={a}"))
            print(f"exited within SLA: {fast}")
        except TimeoutError as e:
            print(f"TIMEOUT: {e}")
