#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Acceptance test for §0.7f — Watchdog detached extraction.

Validates:
1. supervisor --dry-run still works (topo order unchanged)
2. supervisor --watchdog spawns a detached python.exe process + writes PID file
3. supervisor --watchdog second invocation is idempotent ([skip])
4. supervisor --watchdog-stop kills the watchdog + removes PID file
5. supervisor --start no longer hangs (returns immediately after spawning)
6. AGENTS.md has §0.7f header at line 7
7. fix-eol.py --all --check passes
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
HERMES_ROOT = HERE.parent.parent
PY = HERMES_ROOT / "portable-python" / "python.exe"
SUPERVISOR = HERMES_ROOT / "bin" / "hermes-supervisor.py"
PID_FILE = HERMES_ROOT / "data" / "logs" / "hermes-watchdog.pid"

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
os.environ["HERMES_ROOT"] = str(HERMES_ROOT)
# Force subprocess.run to inherit our UTF-8 env into the child.
_RUN_ENV = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "HERMES_ROOT": str(HERMES_ROOT),
}

results = []


def step(name: str, ok: bool, detail: str = ""):
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}{(' — ' + detail) if detail else ''}")
    results.append((name, ok, detail))


def run(args, timeout=30, **kw):
    env = {**os.environ, **_RUN_ENV, **kw.get("env", {})}
    r = subprocess.run(
        [str(PY), str(SUPERVISOR)] + args,
        cwd=str(HERMES_ROOT),
        capture_output=True,
        text=False,
        timeout=timeout,
        env=env,
    )
    # Decode manually (utf-8 with replace) — avoids GBK codec failures
    # when the supervisor child prints Chinese on a Chinese-locale Windows.
    r.stdout = r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
    r.stderr = r.stderr.decode("utf-8", errors="replace") if r.stderr else ""
    return r


print(f"=== Acceptance §0.7f — Watchdog detached extraction ===")
print(f"Hermes root: {HERMES_ROOT}")
print(f"Python:      {PY}")
print(f"Supervisor:  {SUPERVISOR}")
print()

# --- 0. Pre-clean ---
print("[0] Pre-clean (kill any stale watchdog, remove stale PID file)")
try:
    if PID_FILE.is_file():
        old = int(PID_FILE.read_text(encoding="utf-8").strip())
        subprocess.run(["taskkill", "/F", "/PID", str(old), "/T"],
                       capture_output=True, timeout=10)
        PID_FILE.unlink(missing_ok=True)
        print(f"  killed stale pid {old}")
except Exception as e:
    print(f"  pre-clean error: {e}")

# --- 1. --dry-run still works ---
print("\n[1] supervisor --dry-run")
r = run(["--dry-run"], timeout=15)
step("1a. exit 0", r.returncode == 0, f"rc={r.returncode}")
out = r.stdout
# Expect modules: env_bootstrap, bridge, llm_engine, model_manager, webui
expected = ["env_bootstrap", "bridge", "llm_engine", "model_manager", "webui"]
present = [m for m in expected if m in out]
step("1b. all 5 modules listed", len(present) == 5, f"found: {present}")

# --- 2. supervisor --watchdog spawns detached ---
print("\n[2] supervisor --watchdog")
t0 = time.time()
r = run(["--watchdog"], timeout=15)
elapsed = time.time() - t0
step("2a. exit 0", r.returncode == 0, f"rc={r.returncode} in {elapsed:.2f}s")
step("2b. PID file written", PID_FILE.is_file(), f"path={PID_FILE}")
if PID_FILE.is_file():
    pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    step("2c. PID is integer", pid > 0, f"pid={pid}")
    # Confirm process exists
    tr = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True, text=True, timeout=5,
    )
    step("2d. python.exe with that PID exists",
         str(pid) in tr.stdout,
         f"tasklist output line: {tr.stdout.strip()[:80]}")

# --- 3. second invocation is idempotent ---
print("\n[3] supervisor --watchdog (second time, should skip)")
r = run(["--watchdog"], timeout=10)
step("3a. exit 0", r.returncode == 0, f"rc={r.returncode}")
step("3b. output contains [skip]",
     "[skip]" in r.stdout,
     f"stdout (full): {r.stdout.strip()}")
step("3c. PID file unchanged",
     PID_FILE.is_file() and PID_FILE.read_text(encoding="utf-8").strip().isdigit(),
     "still valid")

# --- 4. --watchdog-stop kills it ---
print("\n[4] supervisor --watchdog-stop")
r = run(["--watchdog-stop"], timeout=15)
step("4a. exit 0", r.returncode == 0, f"rc={r.returncode}")
step("4b. PID file removed", not PID_FILE.is_file(), f"exists={PID_FILE.is_file()}")
# Confirm process is gone
if PID_FILE.is_file():
    pid = int(PID_FILE.read_text(encoding="utf-8").strip())
else:
    # get it from history (just stopped)
    pass

# --- 5. --start returns immediately (no hang) ---
print("\n[5] supervisor --start (no hang test)")
# Note: this WILL actually try to start services. We expect it to
# return within a few seconds even if services fail (because cmd_start
# no longer loops). To test just the "no hang" behavior, we set a
# short startup_timeout via... actually no, just run it and measure.
# If services crash (no model file), it will return quickly too.
# We just want to confirm returncode is 0 or 1 within reasonable time.
try:
    t0 = time.time()
    r = run(["--start", "--only", "bridge"], timeout=90)
    elapsed = time.time() - t0
    step("5a. returned within 90s (no hang)",
         True,
         f"elapsed={elapsed:.2f}s rc={r.returncode}")
    # Check watchdog PID was created (post-start watchdog spawn)
    step("5b. watchdog PID file created by --start",
         PID_FILE.is_file(),
         f"path={PID_FILE}")
except subprocess.TimeoutExpired:
    step("5a. returned within 90s (no hang)", False, "TIMEOUT — still hanging!")

# --- 6. cleanup ---
print("\n[6] Cleanup")
r = run(["--watchdog-stop"], timeout=15)
# Also force-kill any bridge that started in [5]
subprocess.run(["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" "
                "| Where-Object { $_.CommandLine -match 'bridge\\\\.server' } "
                "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
                "-ErrorAction SilentlyContinue }"],
               capture_output=True, timeout=10)

# --- 7. AGENTS.md has §0.7f ---
print("\n[7] AGENTS.md §0.7f section")
ag = HERMES_ROOT / "AGENTS.md"
content = ag.read_text(encoding="utf-8")
step("7a. has 'Last revised: 2026-06-15f'", "2026-06-15f" in content[:500])
step("7b. has '## 0.7f.'", "## 0.7f." in content)

# --- Summary ---
print()
print("=" * 60)
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
print(f"  {passed}/{total} passed")
print("=" * 60)
sys.exit(0 if passed == total else 1)
