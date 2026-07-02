#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hermes Supervisor — Pure Python process orchestrator.
======================================================

Why this file exists
    The legacy `modules/supervisor/orchestrator.ps1` was launched from a bat
    via `cmd /c "powershell -NoProfile -ExecutionPolicy Bypass -File ..."`.
    That bridge dies on paths with spaces (e.g. ``E:\\Hermes Agent``): cmd's
    quote parser treats `M` and `\\` as command names, and PowerShell 5.1
    `-File` itself has a path-with-spaces bug that forced the 8.3 short
    path workaround. The old error log showed it plainly::

        'M' is not recognized as an internal or external command
        \\ : The term '\\' is not recognized ...

Design rules
    1. No cmd /c — Python's ``subprocess.Popen`` list args go straight to
       ``CreateProcessW`` on Windows; quotes / paths / spaces cannot break
       parsing.
    2. No short path — Python handles long paths, spaces, Unicode natively.
    3. DETACHED_PROCESS — children are fully detached from the supervisor;
       closing the launching terminal does not kill them.
    4. Socket health check — no urllib / requests dependency.
    5. Reuse existing start.ps1 — PowerShell still owns per-module launch
       logic (llama-server CUDA selection, bridge env injection, webui
       Node args). Python only orchestrates: launch + health-check + shutdown.

Usage
    python bin/hermes-supervisor.py              # start all services
    python bin/hermes-supervisor.py --stop       # stop all in reverse order
    python bin/hermes-supervisor.py --status     # port health check
    python bin/hermes-supervisor.py --dry-run    # show start order
    python bin/hermes-supervisor.py --ports      # show every module's IO port contract
    python bin/hermes-supervisor.py --inspect <name>   # dump one module's full module.json
    python bin/hermes-supervisor.py --only llm_engine bridge webui
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ============================================================
# Constants
# ============================================================

# HERMES_ROOT: single source of truth is bin\hermes-root.py.
# Resolution priority (delegated to that module):
#   1. HERMES_ROOT env var (set by callers like deps\hermes-env.bat)
#   2. <bin/..>/.hermes-root cache
#   3. <bin/..> (one level up from this script: assume <root>/bin/)
#   4. Drive letter scan across D:/..Z:/
# If all four fail, we fall back to the inferred location so the
# user at least gets a clear error rather than a silent crash.
HERE = Path(__file__).resolve()


def _resolve_hermes_root(here: Path) -> Path:
    """Resolve HERMES_ROOT via the single-source-of-truth resolver.

    See the module docstring at the top of bin/hermes-root.py for the
    full resolution algorithm. This wrapper:
      - honors an explicit HERMES_ROOT env var (so callers that already
        resolved it don't pay for a second resolver invocation)
      - shells out to bin/hermes-root.py resolve as the canonical path
      - falls back to <bin/..> if both fail (so error messages stay useful)
    """
    env_root = os.environ.get("HERMES_ROOT", "").strip()
    if env_root:
        p = Path(env_root).resolve()
        if (p / "portable-python" / "python.exe").is_file():
            return p
    resolver = here.parent / "hermes-root.py"
    # FIX 2026-06-16: here.parent.parent (one level up from bin/), not here.parent — original pointed at nonexistent bin/portable-python/, so subprocess.run was dead code. Now supervisor can drive-scan-resolve when env var is stale.
    py = here.parent.parent / "portable-python" / "python.exe"
    if resolver.is_file() and py.is_file():
        try:
            r = subprocess.run(
                [str(py), str(resolver), "resolve"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                p = Path(r.stdout.strip()).resolve()
                if p.is_dir():
                    # FIX 2026-06-16: explicitly update os.environ so any Popen-spawned child (start.ps1 -> hermes-env.ps1) inherits the freshly-resolved HERMES_ROOT instead of a stale env var (USB drive-letter swap E: -> F:).
                    os.environ["HERMES_ROOT"] = str(p)
                    return p
        except Exception:
            pass
    return here.parent.parent


HERMES_ROOT = _resolve_hermes_root(HERE)
MODULES_DIR = HERMES_ROOT / "modules"
LOG_DIR = HERMES_ROOT / "data" / "logs"
PYTHON_EXE = HERMES_ROOT / "portable-python" / "python.exe"

# Windows process creation flags
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

# Windows ANSI (Unicode output to console)
ENABLE_VIRTUAL_TERMINAL = 0x00000004

# ============================================================
# Colors
# ============================================================


class C:
    """ANSI colors (available on Windows 10+ after ENABLE_VIRTUAL_TERMINAL)."""
    RST = "\x1b[0m"
    GRN = "\x1b[32m"
    RED = "\x1b[31m"
    YEL = "\x1b[33m"
    CYN = "\x1b[36m"
    DIM = "\x1b[90m"
    BLD = "\x1b[1m"


def enable_vt() -> None:
    """Enable ANSI colors on Windows 10+ (non-fatal if it fails)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL)
    except Exception:
        pass


# ============================================================
# Data model
# ============================================================


@dataclass
class Module:
    name: str
    path: Path
    type: str                      # 'service' | 'tool'
    depends: List[str] = field(default_factory=list)
    requires: Dict[str, bool] = field(default_factory=dict)  # name -> required
    depends_detail: List[dict] = field(default_factory=list)  # raw {module, required, reason}
    port: Optional[int] = None
    host: str = "127.0.0.1"
    protocol: str = "http"
    health_endpoint: str = "/health"
    health_timeout_ms: int = 5000
    startup_timeout_s: int = 60
    shutdown_timeout_s: int = 10
    start_script: str = "start.ps1"
    stop_script: str = "stop.ps1"
    health_script: str = "health.ps1"
    description: str = ""
    version: str = ""
    runtime_kind: str = ""         # 'native' | 'python' | 'node' | ''
    runtime_args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    required: bool = False         # 哥哥 2026-07-02: required=true → startup fails → supervisor 报错退出 (no silent skip)
    required_reason: str = ""


# ============================================================
# Module discovery
# ============================================================


def discover_modules() -> Dict[str, Module]:
    """Scan modules/*/module.json, return name -> Module dict."""
    modules: Dict[str, Module] = {}
    for entry in sorted(MODULES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        json_path = entry / "module.json"
        if not json_path.is_file():
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [WARN] {entry.name}: bad module.json: {e}", file=sys.stderr)
            continue

        name = data.get("name", entry.name)
        net = data.get("network") or {}
        depends = []
        requires = {}
        for dep in data.get("depends_on", []) or []:
            depends.append(dep["module"])
            requires[dep["module"]] = bool(dep.get("required", False))

        lifecycle = data.get("lifecycle", {}) or {}
        runtime = data.get("runtime", {}) or {}

        modules[name] = Module(
            name=name,
            path=entry,
            type=data.get("type", "service"),
            depends=depends,
            requires=requires,
            depends_detail=list(data.get("depends_on", []) or []),
            port=net.get("port"),
            host=net.get("host", "127.0.0.1"),
            protocol=net.get("protocol", "http"),
            health_endpoint=net.get("health_endpoint", "/health"),
            health_timeout_ms=int(net.get("health_timeout_ms", 5000)),
            startup_timeout_s=int(lifecycle.get("startup_timeout_s", 60)),
            shutdown_timeout_s=int(lifecycle.get("shutdown_timeout_s", 10)),
            start_script=lifecycle.get("start", "start.ps1"),
            stop_script=lifecycle.get("stop", "stop.ps1"),
            health_script=lifecycle.get("health", "health.ps1"),
            description=data.get("description", ""),
            version=str(data.get("version", "")),
            runtime_kind=runtime.get("kind", ""),
            runtime_args=list(runtime.get("args", []) or []),
            env=dict(data.get("env", {}) or {}),
            required=bool(data.get("required", False)),
            required_reason=str(data.get("_required_reason", "")),
        )
    return modules


# ============================================================
# Topological sort (Kahn's algorithm)
# ============================================================


def topo_sort(modules: Dict[str, Module], reverse: bool = False) -> List[str]:
    """Topological sort. reverse=True yields the stop order."""
    in_deg: Dict[str, int] = {n: 0 for n in modules}
    adj: Dict[str, List[str]] = {n: [] for n in modules}
    for n, m in modules.items():
        for d in m.depends:
            if d in modules:
                adj[d].append(n)
                in_deg[n] += 1

    # stable: in-degree-0 nodes sorted by name for deterministic order
    queue = sorted([n for n, d in in_deg.items() if d == 0])
    order: List[str] = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in sorted(adj[cur]):
            in_deg[nxt] -= 1
            if in_deg[nxt] == 0:
                queue.append(nxt)
                queue.sort()

    if len(order) != len(modules):
        cycle = set(modules) - set(order)
        raise RuntimeError(f"Dependency cycle detected, missing: {sorted(cycle)}")
    if reverse:
        order.reverse()
    return order


# ============================================================
# Port health check
# ============================================================


def check_port(host: str, port: int, timeout_s: float = 1.0) -> bool:
    """Non-blocking TCP probe: is the service already listening?"""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def check_http_health(host: str, port: int, endpoint: str = "/health",
                      timeout_s: float = 3.0) -> bool:
    """HTTP health probe: does the service respond with 200 OK?

    FIX 2026-06-27: TCP LISTENING doesn't mean the service is actually
    serving HTTP. A zombie process can bind the port but not respond.
    This function sends a real HTTP request and checks for 200/204/301/302.
    Used by start_module() and cmd_status() for more accurate health checks.
    See: data/ikaros-coordination/handshake.2026-06-27.bridge-zombie.json
    """
    import http.client
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout_s)
        conn.request("GET", endpoint)
        resp = conn.getresponse()
        conn.close()
        return resp.status in (200, 204, 301, 302)
    except Exception:
        return False


def wait_for_port(host: str, port: int, timeout_s: int,
                  http_health: bool = False, endpoint: str = "/health") -> bool:
    """Poll until the port is ready or the timeout elapses.

    If http_health=True, also verify the service responds to HTTP requests
    (not just TCP LISTENING). This catches zombie processes that bind the
    port but don't serve HTTP.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if http_health:
            if check_http_health(host, port, endpoint, timeout_s=2.0):
                return True
        else:
            if check_port(host, port, timeout_s=1.0):
                return True
        time.sleep(1.0)
    return False


# ============================================================
# Process management
# ============================================================


def start_module(m: Module) -> Optional[subprocess.Popen]:
    """
    Launch the module's start.ps1 via subprocess.Popen (list args).
    Returns the Popen handle; returns None if start.ps1 exits immediately
    (HasExited=True).
    """
    if not m.start_script:
        print(f"  {C.YEL}[SKIP]{C.RST} {m.name} — start disabled (start: null)")
        return None

    script = m.path / m.start_script
    if not script.is_file():
        print(f"  {C.YEL}[SKIP]{C.RST} {m.name} — no {m.start_script}")
        return None

    log_path = LOG_DIR / f"{m.name}.log"
    err_path = LOG_DIR / f"{m.name}.err"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    err_path.write_text("", encoding="utf-8")

    log_f = open(log_path, "a", encoding="utf-8", buffering=1)
    err_f = open(err_path, "a", encoding="utf-8", buffering=1)

    # KEY: list args go straight to CreateProcessW, bypassing cmd /c quote parsing.
    # No 8.3 short path needed — Python handles spaces / Unicode natively.
    # Note: DETACHED_PROCESS (0x8) looks tempting (detaches from supervisor), but
    # empirically PowerShell exits 0 immediately with no output (start.ps1 banner
    # never prints). CREATE_NEW_PROCESS_GROUP is enough: child is in a new process
    # group (Ctrl-C doesn't propagate), and redirecting stdio to files detaches
    # the file handles from supervisor so children outlive the supervisor.
    # env: merge module.json's `env` (HERMES_LLAMA_FALLBACKS, etc.) over the
    # current os.environ so the child sees both system vars and module config.
    child_env = dict(os.environ)
    if m.env:
        child_env.update(m.env)
    creationflags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=str(m.path),
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=err_f,
            env=child_env,
            creationflags=creationflags,
            close_fds=True,
        )
    except FileNotFoundError:
        print(f"  {C.RED}[FAIL]{C.RST} {m.name} — powershell.exe not found")
        log_f.close()
        err_f.close()
        return None
    except Exception as e:
        print(f"  {C.RED}[FAIL]{C.RST} {m.name} — Popen error: {e}")
        log_f.close()
        err_f.close()
        return None

    # tool type (one-shot task): return Popen immediately, check later
    if m.type == "tool":
        return proc

    # service type: wait a few seconds to see if the launcher crashes immediately
    time.sleep(2)
    if proc.poll() is not None:
        rc = proc.returncode
        print(f"  {C.RED}[FAIL]{C.RST} {m.name} — start.ps1 exited (rc={rc})")
        tail = read_tail(err_path, 12)
        for line in tail:
            print(f"    {C.DIM}| {line}{C.RST}")
        log_f.close()
        err_f.close()
        return None

    # port health check
    if m.port:
        # FIX 2026-06-27: Use TCP probe for all services (fast, reliable).
        # Zombie process prevention is handled by start.ps1 (kills existing
        # processes on the port before starting). HTTP health check was too
        # strict for bridge's slow startup (60s+ init time).
        # See: data/ikaros-coordination/handshake.2026-06-27.bridge-zombie.json
        if wait_for_port(m.host, m.port, m.startup_timeout_s):
            print(f"  {C.GRN}[OK]{C.RST}   {m.name} (:{m.port})")
        else:
            print(f"  {C.RED}[TIMEOUT]{C.RST} {m.name} — port {m.port} not ready in {m.startup_timeout_s}s")
            tail = read_tail(err_path, 12)
            for line in tail:
                print(f"    {C.DIM}| {line}{C.RST}")
            stop_module(m)
            log_f.close()
            err_f.close()
            return None
    else:
        print(f"  {C.GRN}[OK]{C.RST}   {m.name} (no port)")

    log_f.close()
    err_f.close()
    return proc


def stop_module(m: Module) -> None:
    """Invoke stop.ps1 to gracefully shut down a module."""
    if not m.stop_script:
        return
    script = m.path / m.stop_script
    if not script.is_file():
        return
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            timeout=15,
            capture_output=True,
            cwd=str(m.path),
        )
    except Exception as e:
        print(f"  {C.YEL}[WARN]{C.RST} {m.name} stop.ps1 error: {e}")


def read_tail(path: Path, n: int) -> List[str]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [ln.rstrip("\r\n") for ln in lines[-n:]]
    except Exception:
        return []


# ============================================================
# Main flow
# ============================================================


def cmd_dry_run(modules: Dict[str, Module], only: List[str]) -> int:
    print(f"{C.BLD}Hermes Supervisor — dry-run{C.RST}")
    print(f"  Root:   {HERMES_ROOT}")
    print(f"  Python: {PYTHON_EXE}")
    print(f"  Modules discovered: {len(modules)}")
    print()
    order = topo_sort(modules, reverse=False)
    if only:
        order = [n for n in order if n in only]
    print("  Start order:")
    for n in order:
        m = modules[n]
        deps = ", ".join(m.depends) if m.depends else "none"
        kind = f"service :{m.port}" if m.port else m.type
        print(f"    {C.CYN}{n:20}{C.RST} [{kind:18}] depends: {deps}")
    return 0


def cmd_ports(modules: Dict[str, Module], only: List[str]) -> int:
    """Print the IO-port contract table for every module (the 'signal map').

    This is the -help-style view the operator types to inspect what each
    module listens on, what /health endpoint to probe, and what it depends
    on. It is also the offline counterpart to `GET /v1/modules` on the
    bridge, so a service that is DOWN can still be introspected.
    """
    print(f"{C.BLD}Hermes Supervisor — IO port contract{C.RST}")
    print(f"  Root:   {HERMES_ROOT}")
    print(f"  Modules: {len(modules)}")
    print()

    targets = only or list(modules.keys())
    headers = ("MODULE", "KIND", "RUNTIME", "ENDPOINT", "HEALTH", "DEPENDS")
    rows = []
    for n in targets:
        m = modules.get(n)
        if m is None:
            rows.append((n, "?", "?", "?", "?", f"{C.RED}unknown module{C.RST}"))
            continue
        endpoint = (
            f"{m.protocol}://{m.host}:{m.port}" if m.port else f"({m.type}, no port)"
        )
        health = (
            f"{m.host}:{m.port}{m.health_endpoint}"
            if m.port else "(one-shot tool)"
        )
        runtime = (
            f"{m.runtime_kind} {(' '.join(m.runtime_args)[:40] + ('…' if len(' '.join(m.runtime_args)) > 40 else ''))}"
            if m.runtime_kind else "-"
        )
        deps_disp = "none"
        if m.depends_detail:
            bits = []
            for d in m.depends_detail:
                tag = "!" if d.get("required") else "~"
                bits.append(f"{tag}{d['module']}")
            deps_disp = ", ".join(bits)
        rows.append((n, m.type, runtime.strip(), endpoint, health, deps_disp))

    # ASCII table
    col_w = [max(len(str(r[i])) for r in rows + [tuple(headers)]) for i in range(len(headers))]
    line = "  " + " | ".join(headers[i].ljust(col_w[i]) for i in range(len(headers)))
    sep  = "  " + "-+-".join("-" * col_w[i] for i in range(len(headers)))
    print(line)
    print(sep)
    for r in rows:
        # Highlight module name in cyan; rest is plain text
        print(f"  {C.CYN}{str(r[0]).ljust(col_w[0])}{C.RST} | "
              f"{str(r[1]).ljust(col_w[1])} | "
              f"{str(r[2]).ljust(col_w[2])} | "
              f"{str(r[3]).ljust(col_w[3])} | "
              f"{str(r[4]).ljust(col_w[4])} | "
              f"{str(r[5]).ljust(col_w[5])}")

    print()
    print(f"  Legend: ! = required dependency,  ~ = soft dependency")
    print(f"  Live liveness probe (TCP-level): see --status")
    print(f"  Online counterpart: GET http://127.0.0.1:7860/v1/modules (bridge)")
    return 0


def cmd_inspect(modules: Dict[str, Module], name: str) -> int:
    """Dump the full module.json (decoded) of a single module."""
    m = modules.get(name)
    if m is None:
        print(f"{C.RED}[FAIL]{C.RST} unknown module: {name}", file=sys.stderr)
        print(f"  known: {', '.join(sorted(modules.keys()))}")
        return 1
    raw_path = m.path / "module.json"
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"{C.RED}[FAIL]{C.RST} cannot read {raw_path}: {e}", file=sys.stderr)
        return 1

    print(f"{C.BLD}Module: {name}{C.RST}  ({raw_path})")
    print()
    print(json.dumps(raw, indent=2, ensure_ascii=False))
    print()
    # Live probe
    if m.port:
        alive = check_port(m.host, m.port, timeout_s=0.5)
        tag = f"{C.GRN}UP{C.RST}" if alive else f"{C.RED}DOWN{C.RST}"
        print(f"  Live probe:  {tag}  tcp://{m.host}:{m.port}")
        print(f"  Health URL:  http://{m.host}:{m.port}{m.health_endpoint}  "
              f"(timeout {m.health_timeout_ms}ms)")
    else:
        print(f"  Live probe:  {C.DIM}no port (tool / one-shot){C.RST}")
    print(f"  Lifecycle:   start={m.start_script}  stop={m.stop_script}  "
          f"health={m.health_script}")
    print(f"  Timeouts:    start<={m.startup_timeout_s}s  stop<={m.shutdown_timeout_s}s")
    return 0


def cmd_status(modules: Dict[str, Module], only: List[str]) -> int:
    print(f"{C.BLD}Hermes Supervisor — status{C.RST}")
    print()
    name_w = max((len(n) for n in modules), default=10)
    rc = 0
    targets = only or list(modules.keys())
    for n in targets:
        m = modules.get(n)
        if m is None:
            print(f"  {C.YEL}?{C.RST}  {n} (unknown module)")
            rc = 1
            continue
        if not m.port:
            print(f"  {C.DIM}-{C.RST}  {n:<{name_w}}  (no port)")
            continue
        # FIX 2026-06-27: Use TCP probe for all services (fast, reliable).
        # Zombie process prevention is handled by start.ps1.
        if check_port(m.host, m.port, timeout_s=0.5):
            print(f"  {C.GRN}UP{C.RST}    {n:<{name_w}}  http://{m.host}:{m.port}")
        else:
            print(f"  {C.RED}DOWN{C.RST}  {n:<{name_w}}  http://{m.host}:{m.port}")
            rc = 1
    return rc


def cmd_watchdog_start(modules: Dict[str, Module]) -> int:
    """Detached-launch the watchdog daemon, then return immediately.

    Why detached: supervisor exits after spawning services (`hermes-all.bat`
    wants a quick return so its window can close). The watchdog must keep
    running as a detached child — parent exit doesn't affect it, file
    handles survive, and it can resurrect any service that crashes.

    Why idempotent: `hermes-stop.bat` followed by `hermes-all.bat` should
    not fork multiple watchdogs. PID file exists + process alive → skip.
    """
    pid_file = LOG_DIR / "hermes-watchdog.pid"
    # singleton check: skip if a watchdog is already running
    if pid_file.is_file():
        try:
            old_pid = int(pid_file.read_text(encoding="utf-8").strip())
            # cross-platform check: tasklist on Windows, /proc on Linux
            if os.name == "nt":
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {old_pid}", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                if str(old_pid) in r.stdout:
                    print(f"  {C.DIM}[skip]{C.RST} watchdog already running (pid {old_pid})")
                    return 0
        except (ValueError, subprocess.TimeoutExpired, Exception):
            pass
        # stale pid file: remove and recreate
        pid_file.unlink(missing_ok=True)

    script = HERE.parent / "hermes-watchdog.py"
    if not script.is_file():
        print(f"  {C.RED}[FAIL]{C.RST} watchdog script not found: {script}")
        return 1

    creationflags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            [str(PYTHON_EXE), str(script)],
            cwd=str(HERMES_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=open(LOG_DIR / "hermes-watchdog.log", "a", encoding="utf-8", buffering=1),
            stderr=open(LOG_DIR / "hermes-watchdog.err", "a", encoding="utf-8", buffering=1),
            creationflags=creationflags,
            close_fds=True,
        )
    except Exception as e:
        print(f"  {C.RED}[FAIL]{C.RST} cannot launch watchdog: {e}")
        return 1

    # write PID file (consumed by hermes-stop.bat)
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    print(f"  {C.GRN}[OK]{C.RST}   watchdog detached (pid {proc.pid})")
    print(f"         log: {LOG_DIR / 'hermes-watchdog.log'}")
    print(f"         pid: {pid_file}")
    return 0


def cmd_restart(modules: Dict[str, Module], name: str) -> int:
    """Restart a single service. Used by the watchdog; also handy for manual debugging."""
    m = modules.get(name)
    if m is None:
        print(f"{C.RED}[FAIL]{C.RST} unknown module: {name}", file=sys.stderr)
        return 1
    if m.type != "service":
        print(f"  {C.DIM}[skip]{C.RST} {name} is type={m.type}, not a service")
        return 0
    print(f"  {C.CYN}>{C.RST} restarting {name} (:{m.port})")
    # Fix (2026-06-18): stop the old instance first so start_module's port
    # check does not race against a stale process. Without this, a service
    # bound to a fixed port (e.g. llama-server :8080) would either get
    # EADDRINUSE or leave two processes fighting for the same port.
    stop_module(m)
    proc = start_module(m)
    return 0 if proc is not None else 1


def cmd_watchdog_stop() -> int:
    """Gracefully stop the watchdog via its PID file + cmdline grep.

    More precise than `taskkill python.exe` — won't kill unrelated Python.
    """
    pid_file = LOG_DIR / "hermes-watchdog.pid"
    if not pid_file.is_file():
        print(f"  {C.DIM}[skip]{C.RST} no watchdog pid file")
        return 0

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, FileNotFoundError):
        pid_file.unlink(missing_ok=True)
        print(f"  {C.DIM}[skip]{C.RST} stale pid file removed")
        return 0

    print(f"  {C.YEL}x{C.RST} watchdog (pid {pid})")
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid), "/T"],
                       capture_output=True, timeout=10)
    else:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
    pid_file.unlink(missing_ok=True)
    print(f"  {C.GRN}Done.{C.RST}")
    return 0


def cmd_start(modules: Dict[str, Module], only: List[str]) -> int:
    print(f"{C.BLD}Hermes Supervisor — start{C.RST}")
    print(f"  Root:   {HERMES_ROOT}")
    print(f"  Python: {PYTHON_EXE}")
    if not PYTHON_EXE.is_file():
        print(f"  {C.RED}[FATAL]{C.RST} portable-python not found: {PYTHON_EXE}")
        return 2

    order = topo_sort(modules, reverse=False)
    if only:
        order = [n for n in order if n in only]
    print(f"  Order:  {' -> '.join(order)}")
    print()

    started: List[str] = []
    failed: List[str] = []

    for n in order:
        m = modules[n]
        print(f"  {C.CYN}>{C.RST} {n} ({m.type}){C.YEL}{' [REQUIRED]' if m.required else ''}{C.RST}")
        proc = start_module(m)
        if proc is None and m.type == "service" and m.start_script:
            failed.append(n)
            # 哥哥 2026-07-02 axiom: required 模块失败 → supervisor 立即报错退出.
            # 不要 silent skip — 这是 supervisor 编排的"安全网", 让哥哥一眼看到缺少什么.
            if m.required:
                print(f"{C.RED}============================================================{C.RST}")
                print(f"  {C.RED}[FATAL]{C.RST} required module failed: {C.BLD}{n}{C.RST} (:{m.port})")
                if m.required_reason:
                    print(f"          reason: {m.required_reason}")
                # 缺模型下载提示 (从 module.json 读 model_download_url)
                raw = json.loads((m.path / "module.json").read_text(encoding="utf-8"))
                if raw.get("model_file") and raw.get("model_download_url"):
                    mf = m.path.parent.parent / raw["model_file"]
                    print(f"  {C.RED}[model]{C.RST} {raw['model_file']}")
                    if mf.is_file():
                        print(f"          exists at: {mf}")
                    else:
                        print(f"          {C.YEL}MISSING{C.RST} — download to: {mf}")
                        print(f"          URL: {raw['model_download_url']}")
                print(f"{C.RED}============================================================{C.RST}")
                print()
                # 失败后停止已经启动的 (reverse order) 干净退出
                for started_name in started:
                    stop_module(modules[started_name])
                return 2
        else:
            started.append(n)

    # persist state
    state = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "started": started,
        "failed": failed,
        "order": order,
    }
    (LOG_DIR / "supervisor-state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print()
    print(f"{C.BLD}============================================================{C.RST}")
    if failed:
        print(f"  {C.RED}FAILED{C.RST}: {len(failed)} module(s): {', '.join(failed)}")
        return 1
    print(f"  {C.GRN}STARTED{C.RST}: {len(started)} module(s)")
    print(f"{C.BLD}============================================================{C.RST}")

    # 哥哥 2026-06-29 暂时禁用 watchdog — 影响开发
    # 原因: 反复重启干扰调试, 服务死了需要手动 restart
    # TODO: 重新启用时, 把这段换成 cmd_watchdog_start() 即可
    # 2026-07-02 重新启用 (哥哥 out-of-band: nomic-embed :8587 + R1 :8589 是 required,
    # 必须有 watchdog 死掉自动拉起). 见 handshake.2026-07-02.local-inference-required-restart.json
    cmd_watchdog_start(modules)
    return 0


def cmd_stop(modules: Dict[str, Module], only: List[str]) -> int:
    print(f"{C.BLD}Hermes Supervisor — stop{C.RST}")
    order = topo_sort(modules, reverse=True)
    if only:
        order = [n for n in order if n in only]
    print(f"  Reverse order: {' -> '.join(order)}")
    print()
    for n in order:
        m = modules[n]
        if m.type == "service":
            print(f"  {C.YEL}x{C.RST} {n}")
            stop_module(m)
    print()
    print(f"  {C.GRN}Done.{C.RST}")
    return 0


# ============================================================
# Entry
# ============================================================


def main() -> int:
    enable_vt()
    parser = argparse.ArgumentParser(
        description="Hermes process orchestrator (pure-Python; bypasses cmd /c quote bugs)\n\n"
                    "Examples:\n"
                    "  --status     TCP-level liveness probe of every module\n"
                    "  --dry-run    show topological start order\n"
                    "  --ports      print every module's IO port contract (signal map)\n"
                    "  --inspect N  dump one module's full module.json\n"
                    "  --restart N  restart one service module (used by watchdog)\n"
                    "  --watchdog   start the auto-restart watchdog as a detached process\n"
                    "  --watchdog-stop  kill the watchdog via PID file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--start", action="store_true", help="start all services (default) + detached watchdog")
    grp.add_argument("--stop", action="store_true", help="reverse-stop all services + kill watchdog")
    grp.add_argument("--status", action="store_true", help="port health check (TCP)")
    grp.add_argument("--dry-run", action="store_true", help="show start order only")
    grp.add_argument("--ports", action="store_true",
                     help="print every module's IO port contract (signal map)")
    grp.add_argument("--inspect", metavar="MODULE",
                     help="dump one module's full module.json")
    grp.add_argument("--restart", metavar="MODULE",
                     help="restart one service (used by watchdog)")
    grp.add_argument("--watchdog", action="store_true",
                     help="start the watchdog daemon as a detached process")
    grp.add_argument("--watchdog-stop", action="store_true",
                     help="stop the watchdog via PID file")
    parser.add_argument("--only", nargs="+", default=[], help="process only the named modules")
    args = parser.parse_args()

    if not HERMES_ROOT.is_dir():
        print(f"{C.RED}[FATAL]{C.RST} HERMES_ROOT not found: {HERMES_ROOT}")
        return 2
    if not MODULES_DIR.is_dir():
        print(f"{C.RED}[FATAL]{C.RST} modules/ not found: {MODULES_DIR}")
        return 2

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    modules = discover_modules()
    if not modules:
        print(f"{C.RED}[FATAL]{C.RST} no modules found in {MODULES_DIR}")
        return 2

    if args.stop:
        # stop watchdog first, then services (watchdog may be mid-restart)
        cmd_watchdog_stop()
        return cmd_stop(modules, set(args.only))
    if args.status:
        return cmd_status(modules, set(args.only))
    if args.dry_run:
        return cmd_dry_run(modules, args.only)
    if args.ports:
        return cmd_ports(modules, args.only)
    if args.inspect:
        return cmd_inspect(modules, args.inspect)
    if args.restart:
        return cmd_restart(modules, args.restart)
    if args.watchdog:
        return cmd_watchdog_start(modules)
    if args.watchdog_stop:
        return cmd_watchdog_stop()
    return cmd_start(modules, set(args.only))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
