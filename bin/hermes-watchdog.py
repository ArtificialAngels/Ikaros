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
import threading
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
# HERMES_HOME is the user-specific hermes-agent state dir (skills,
# memory, sessions, cron jobs). It is *not* a sibling of LOG_DIR — it
# lives one level deeper under data/. Previously we used
# LOG_DIR.parent/cron/jobs.json which is the wrong path (LOG_DIR.parent
# is data/, not data/hermes-agent/). Constants here so both
# _read_device_info and _service_status_snapshot agree.
HERMES_HOME = HERMES_ROOT / "data" / "hermes-agent"
PYTHON_EXE = HERMES_ROOT / "portable-python" / "python.exe"
SUPERVISOR = HERMES_ROOT / "bin" / "hermes-supervisor.py"

PID_FILE = LOG_DIR / "hermes-watchdog.pid"
# FIX 2026-06-18: persistent heartbeat log. watchdog writes one line per
# tick / state-change / restart / system-change to this file. Survives
# across watchdog restarts because it's append-only JSONL. We never
# delete or rotate it; the user inspects it to see "when was I asleep
# / when did the drive change / when did service X die".
# Each line is independently parseable (one JSON object per line).
HEARTBEAT_FILE = LOG_DIR / "ikaros-heartbeat.jsonl"

# Watchdog cycle (seconds). Too short wastes CPU; too long delays restart.
INTERVAL_S = 10
# Minimum gap between two restarts of the same service (prevents tight loops
# if start.ps1 itself is broken).
RESTART_COOLDOWN_S = 30
# Heartbeat write throttle. Service snapshots are 1/tick (10s) by default;
# that's noisy. We snapshot at a slower rate and emit lightweight "tick"
# markers in between. The full service_status line is emitted every
# SNAPSHOT_EVERY_TICKS ticks (i.e. every 60s at 10s interval).
SNAPSHOT_EVERY_TICKS = 6
# System-change probe: every N ticks (slow), re-run `bin/hermes-root.bat
# init` and diff against last seen fingerprint. If anything changed
# (user/host/bios_uuid/drive/serial), emit a system_change event.
SYSTEM_PROBE_EVERY_TICKS = 30  # every ~5min
# Memory ingest: every N ticks, run bin/ikaros-remember.py to write
# today's narrative entry into data/hermes-agent/memories/ikaros/.
# This is the "memory core" ingest — converts the structured heartbeat
# into a human-readable paragraph that the agent can re-read later.
# Default ~6h (6h * 360 ticks) so we get 4 entries/day without flooding.
MEMORY_INGEST_EVERY_TICKS = 2160
# Heartbeat archival: every N ticks, run bin/ikaros-heartbeat-archive.py
# to bound the log file size. At 10s tick that's every ~24h. Old logs
# are compressed (FRESHFUZZY) or dropped (DROP). Failure mode: silent.
ARCHIVE_EVERY_TICKS = 8640  # ~24h at 10s/tick
# Liveness probe: every N ticks, call GET :7860/v1/liveness and write
# the verdict (ok / degraded / dead) into the heartbeat. 4 ticks =
# 40s with default 10s tick. Per plan G (liveness 守护).
LIVENESS_PROBE_EVERY_TICKS = 4
# Dojo daily loop (plan 2): every N ticks run bin/ikaros-dojo-daily.py
# which calls monitor.py → tracker.py save → writes a daily note.
# Always read-only (no auto-apply). At 10s tick, 8640 = ~24h. Run it
# shortly after the archive tick so the daily note isn't shadowed by
# the archive summary.
DOJO_DAILY_EVERY_TICKS = 8640  # ~24h at 10s/tick
# Liveness dead-threshold: after N consecutive "dead" verdicts, emit
# a special alert event (so ikaros-remember surfaces it in the daily
# narrative as "I lost all providers for X minutes").
LIVENESS_DEAD_ALERT_AFTER = 5  # ~3min20s at 40s/probe × 5

# FIX 2026-06-17: when launcher is mid-update it writes ~/.hermes-web-ui/upgrading.lock.
# Webui :8649 will go down briefly (npm renames its dir → process exits) and
# the watchdog MUST NOT race npm by restarting the old webui. If the lock
# is present and unexpired we skip webui checks; other services (bridge,
# llm_engine, webui_proxy) are unaffected. Lock is auto-ignored past its
# deadline so a crashed launcher never strands the watchdog.
UPGRADING_LOCK = (Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")
                  / ".hermes-web-ui" / "upgrading.lock")

# 2026-06-23: webui self-update marker. webui_proxy intercepts
# POST /api/hermes/update and writes this file (since the upstream
# npm endpoint EBUSYs on Windows). The watchdog picks it up at the
# next tick, invokes bin/_webui_update.py to perform the actual
# stop->npm install->start cycle, and deletes the marker.
WEBUI_UPDATE_MARKER = HERMES_ROOT / "data" / "webui" / "needs-update.json"
WEBUI_UPDATE_SCRIPT = HERMES_ROOT / "bin" / "_webui_update.py"


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


def check_http_health(host: str, port: int, endpoint: str = "/health",
                      timeout_s: float = 3.0) -> bool:
    """HTTP health probe: does the service respond with 200 OK?

    FIX 2026-06-27: TCP LISTENING doesn't mean the service is actually
    serving HTTP. A zombie process can bind the port but not respond.
    This function sends a real HTTP request and checks for 200/204/301/302.
    Used for bridge to catch zombie processes.
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
# Heartbeat (FIX 2026-06-18)
# ============================================================


def _hb_event(event: str, **fields) -> None:
    """Append a single JSONL line to the heartbeat log. Best-effort; never raises."""
    import json as _json
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields}
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with HEARTBEAT_FILE.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        # Heartbeat is observability, not load-bearing. Never crash the
        # watchdog over a log write failure (e.g. disk full, EOL issue).
        sys.stderr.write(f"[watchdog] heartbeat write failed: {e}\n")


def _read_device_info() -> dict:
    """Read hermes-root.py device-info. Returns empty dict on failure.

    Output format is a single space-separated line of key=value pairs:
        "host=LEGION9 user=PZS0X uuid=FC0BC32E-... serial=PF36EHVY os=Windows 11 (build 10.0.26200)"
    Note: `os=` value contains spaces ("Windows 11 (build 10.0.26200)") so
    we cannot split on '=' alone. We split on whitespace, then take the
    first '=' to separate key from value. The 'os' key is special-cased
    to join all remaining tokens back into the value.
    """
    try:
        r = subprocess.run(
            [str(PYTHON_EXE), str(HERE.parent / "hermes-root.py"), "device-info"],
            capture_output=True, text=True, timeout=5,
        )
        out: dict = {}
        # Walk tokens. The "os" key consumes every remaining token.
        # Device-info output looks like:
        #   host=LEGION9 user=PZS0X uuid=... serial=PF36EHVY os=Windows 11 (build 10.0.26200)
        # So "os" is the LAST key in the line. We split on whitespace,
        # partition each token on '=', and once we hit key=='os' we
        # join the remaining tokens (which together form the value
        # "Windows 11 (build 10.0.26200)").
        tokens = r.stdout.split()
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if "=" not in tok:
                i += 1
                continue
            key, _, val = tok.partition("=")
            key = key.strip()
            if key == "os":
                # Greedily consume remaining tokens for the os value.
                rest: list[str] = []
                for t in tokens[i:]:
                    if "=" in t:
                        rest.append(t.partition("=")[2])
                    else:
                        # Continuation token (e.g. "11" or "(build 10.0.26200)"
                        # when device-info was already partitioned on '=').
                        # We only include it if the previous token *also*
                        # came from an os= ... line — but tokens above
                        # already are space-split, so the continuation is
                        # just plain text. Append verbatim to preserve.
                        if rest:
                            rest.append(t)
                out["os"] = " ".join(rest)
                break
            else:
                out[key] = val
            i += 1
        return out
    except Exception:
        return {}


def _service_status_snapshot() -> dict:
    """Probe all service ports + check gateway/cron liveness.

    Gateway and cron are *not* standalone port services. Gateway runs as
    `python -m hermes gateway run`; the cron scheduler is a module
    inside the gateway process. So we check by scanning the running
    process list for their command lines (cheap, ~50ms on Windows).
    """
    mods = discover_modules()
    services = {}
    for name, m in mods.items():
        if m.type == "service" and m.port:
            services[name] = {
                "port": m.port,
                "up": check_port(m.host, m.port, timeout_s=0.8),
            }

    # Scan process list once for both gateway and cron (both are inside
    # the same `hermes gateway run` process; we report them as up when
    # the gateway process is alive, since killing gateway would kill
    # cron with it). This replaces the old "is pid_file present" check
    # which was wrong (no pid file is ever written for gateway).
    try:
        r = subprocess.run(
            ["wmic", "process", "where",
             "name='python.exe'", "get", "processid,commandline", "/format:list"],
            capture_output=True, text=True, timeout=4,
        )
        text = r.stdout
    except Exception:
        text = ""
    gateway_alive = "hermes gateway run" in text
    gateway_pid = None
    # Pull the gateway pid for diagnostics. wmic /format:list emits
    # CommandLine=... BEFORE ProcessId=... (verified), so the pattern
    # is: match "hermes gateway run" then look at the FOLLOWING
    # ProcessId=... (skip over other fields in between).
    import re as _re
    m = _re.search(
        r"hermes gateway run.*?ProcessId=(\d+)",
        text, _re.DOTALL,
    )
    if m:
        gateway_pid = int(m.group(1))
    services["gateway"] = {"up": gateway_alive, "pid": gateway_pid}
    # Cron runs inside gateway. Report as "up" iff gateway is up AND
    # jobs.json is present at the canonical HERMES_HOME/cron path.
    cron_jobs = HERMES_HOME / "cron" / "jobs.json"
    services["cron"] = {
        "up": gateway_alive and cron_jobs.is_file(),
        "jobs_file": str(cron_jobs) if cron_jobs.is_file() else None,
    }
    return services


# ---- Liveness probe (talks to bridge /v1/liveness) ----
def _probe_liveness() -> dict | None:
    """Ask bridge whether Ikaros can talk to a model right now.

    Returns the verdict dict on success, None on bridge unreachable.
    The dict is shaped like:
        {"status": "ok"|"degraded"|"dead", "summary": str, ...}
    and matches the contract of POST /v1/liveness exactly.

    We use a 20s timeout to absorb a slow bridge / slow cloud probe.
    /v1/liveness runs 4 parallel HTTP probes (local + 3 clouds + maybe
    Anthropic) with 4s timeout each, but the local llama-server probe
    can take 12+ seconds when models are loading or slow to respond.
    The endpoint can take up to ~15s under normal conditions. The 10s
    timeout in earlier versions produced false "dead" verdicts because
    it was shorter than the actual response time.
    """
    import json
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request("http://127.0.0.1:7860/v1/liveness", method="GET")
        with urllib.request.urlopen(req, timeout=20.0) as r:
            payload = r.read().decode("utf-8", errors="replace")
            return json.loads(payload)
    except urllib.error.URLError as e:
        # Bridge down — by definition Ikaros is "dead" until it comes back.
        return {"status": "dead", "summary": f"bridge unreachable: {type(e).__name__}: {e}",
                "local": None, "cloud": {}}
    except Exception as e:
        return {"status": "dead", "summary": f"liveness probe error: {type(e).__name__}: {e}",
                "local": None, "cloud": {}}


def _detect_system_change(prev: dict | None) -> tuple[dict, list[str]]:
    """Re-probe device info. Returns (current_dict, list_of_changed_fields)."""
    cur = _read_device_info()
    changed: list[str] = []
    if prev:
        for k, v in cur.items():
            if prev.get(k) != v:
                changed.append(k)
    return cur, changed


def _run_archive() -> bool:
    """Run ikaros-heartbeat-archive.py to bound log size. Returns True
    if it actually wrote (i.e. records were dropped). Watchdog invokes
    this every ARCHIVE_EVERY_TICKS ticks (default ~24h). Failures are
    non-fatal: archive logs its own errors and watchdog keeps going.
    """
    script = HERE.parent / "ikaros-heartbeat-archive.py"
    if not script.is_file():
        return False
    try:
        before = HEARTBEAT_FILE.stat().st_size if HEARTBEAT_FILE.is_file() else 0
        r = subprocess.run(
            [str(PYTHON_EXE), str(script), "--json"],
            capture_output=True, text=True, timeout=60,
            cwd=str(HERMES_ROOT),
        )
        if r.returncode != 0:
            sys.stderr.write(f"[watchdog] archive failed: rc={r.returncode} {r.stderr[:200]}\n")
            return False
        after = HEARTBEAT_FILE.stat().st_size if HEARTBEAT_FILE.is_file() else 0
        try:
            info = json.loads(r.stdout)
            counts = info.get("counts", {})
            dropped = (counts.get("dropped_noisy_old", 0)
                       + counts.get("dropped_too_old", 0))
            _hb_event("archive", dropped=dropped,
                      bytes_before=before, bytes_after=after)
        except Exception:
            pass
        return dropped > 0
    except Exception as e:
        sys.stderr.write(f"[watchdog] archive error: {e}\n")
        return False


def _run_memory_ingest() -> bool:
    """Run ikaros-remember.py to write today's narrative entry to
    data/hermes-agent/memories/ikaros/YYYY-MM-DD.md. This is the
    "memory core" ingest — converts structured heartbeat into
    human-readable text the agent can re-read. Runs every
    MEMORY_INGEST_EVERY_TICKS ticks (~6h by default).
    """
    script = HERE.parent / "ikaros-remember.py"
    if not script.is_file():
        return False
    try:
        r = subprocess.run(
            [str(PYTHON_EXE), str(script)],
            capture_output=True, text=True, timeout=30,
            cwd=str(HERMES_ROOT),
        )
        if r.returncode not in (0, 1):
            # 0 = success, 1 = no records (acceptable on a fresh day)
            sys.stderr.write(
                f"[watchdog] memory-ingest failed: rc={r.returncode} {r.stderr[:200]}\n"
            )
            return False
        return r.returncode == 0
    except Exception as e:
        sys.stderr.write(f"[watchdog] memory-ingest error: {e}\n")
        return False


def _run_dojo_daily() -> bool:
    """Run ikaros-dojo-daily.py (plan 2). Always read-only: never
    auto-applies skill fixes. Emits a heartbeat event so Ikaros'
    timeline sees the dojo tick. Failures are non-fatal.
    """
    script = HERE.parent / "ikaros-dojo-daily.py"
    if not script.is_file():
        return False
    try:
        r = subprocess.run(
            [str(PYTHON_EXE), str(script)],
            capture_output=True, text=True, timeout=120,
            cwd=str(HERMES_ROOT),
        )
        log_path = LOG_DIR / "hermes-watchdog.log"
        with log_path.open("a", encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            f.write(f"\n[{ts}] dojo-daily rc={r.returncode}\n")
            if r.stdout:
                f.write("--- stdout ---\n" + r.stdout + "\n")
            if r.stderr:
                f.write("--- stderr ---\n" + r.stderr + "\n")
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        sys.stderr.write("[watchdog] dojo-daily timeout (120s)\n")
        return False
    except Exception as e:
        sys.stderr.write(f"[watchdog] dojo-daily error: {e}\n")
        return False


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

    started_at = time.time()
    print(f"[watchdog] started (pid {os.getpid()})", flush=True)
    print(f"[watchdog] interval: {INTERVAL_S}s  cooldown: {RESTART_COOLDOWN_S}s",
          flush=True)
    print(f"[watchdog] supervising: {HERMES_ROOT}", flush=True)

    # Heartbeat: emit wake event with full device fingerprint on startup.
    # This is the "what machine am I running on" anchor for the heartbeat
    # log. Also captures the watchdog's own pid and the supervising
    # HERMES_ROOT, so the user can tell watchdog instances apart.
    boot_info = _read_device_info()
    _hb_event(
        "wake",
        watchdog_pid=os.getpid(),
        hermes_root=str(HERMES_ROOT),
        **boot_info,
    )
    # Plan 3: emit an awake-briefing on every wake so the heartbeat has
    # a "what did I do last" snapshot at the start of every session.
    # The actual memory fetch is in bridge (via /v1/ikaros/awake-briefing).
    # Here we just trigger it; failures are silent (the bridge may not
    # be up yet on a cold start).
    try:
        import urllib.request as _url
        with _url.urlopen("http://127.0.0.1:7860/v1/ikaros/awake-briefing",
                          timeout=4) as _r:
            _brief = json.loads(_r.read().decode("utf-8"))
            _hb_event(
                "awake_briefing",
                last_session_date=_brief.get("last_session", {}).get("date"),
                last_session_headline=(
                    _brief.get("last_session", {}).get("headline") or ""
                )[:160],
                recent_count=len(_brief.get("recent_three_dates", [])),
            )
    except Exception:
        # Bridge not up yet — skip silently; the next tick at +10s will
        # still find the same memory. Don't spam logs.
        pass
    # last-known device fingerprint, for system_change diff
    last_device_info = boot_info
    # last-known service state, to detect UP->DOWN transitions without
    # spamming the log (DOWN->UP emits a restart event separately).
    last_svc_state: dict[str, bool] = {}

    # name -> timestamp of last restart (for cooldown)
    last_restart: Dict[str, float] = {}

    # Liveness state machine: counter for consecutive "dead" verdicts so
    # we only emit a `liveness_dead_alert` after N probes (~3 min) rather
    # than on every transient blip. Reset to 0 on first non-dead verdict.
    consecutive_dead = 0

    tick_count = 0
    try:
        while True:
            time.sleep(INTERVAL_S)
            tick_count += 1

            # 2026-06-23: webui update marker. webui_proxy intercepts the
            # upstream /api/hermes/update endpoint and writes this file.
            # We invoke _webui_update.py which does the actual work in a
            # SEPARATE PROCESS (so this watchdog tick can keep doing
            # other things). _webui_update.py acquires the upgrading.lock
            # itself, so the "skip webui" branch below sees the lock and
            # backs off. The script blocks for ~30-90s; we run it in a
            # thread so other modules' checks stay on cadence.
            if WEBUI_UPDATE_MARKER.is_file():
                print(f"[watchdog] webui update marker detected -> "
                      f"spawning {WEBUI_UPDATE_SCRIPT.name}", flush=True)
                _hb_event("webui_update_start", marker=str(WEBUI_UPDATE_MARKER))
                try:
                    proc = subprocess.Popen(
                        [str(PYTHON_EXE), str(WEBUI_UPDATE_SCRIPT)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, cwd=str(HERMES_ROOT),
                    )
                    # Run it in a thread so we don't block the 10s tick.
                    # Don't wait — the script writes its own log lines and
                    # deletes the marker on success. If it fails the
                    # marker will remain; we'll retry next tick.
                    def _drain_update(p):
                        try:
                            out, _ = p.communicate(timeout=600)
                        except subprocess.TimeoutExpired:
                            p.kill()
                            out = "[watchdog] _webui_update.py timeout\n"
                        if out:
                            for line in out.splitlines():
                                print(f"[webui_update] {line}", flush=True)
                        _hb_event("webui_update_end", rc=p.returncode)
                    threading.Thread(target=_drain_update, args=(proc,),
                                     daemon=True, name="webui-update").start()
                except Exception as e:
                    print(f"[watchdog] failed to spawn _webui_update.py: {e}",
                          flush=True)
                    _hb_event("webui_update_spawn_fail", error=str(e))
                # Note: we do NOT consume the marker here. The update
                # script itself deletes it on success. If it fails, the
                # marker remains and we retry next tick.

            # FIX 2026-06-17: while launcher is mid-update, skip webui checks.
            # npm install renames the webui dir → its process dies → port
            # goes down. Restarting the old webui at that moment EBUSYs npm.
            skip_modules: set[str] = set()
            if is_upgrading_lock_active():
                skip_modules.add("webui")
                skip_modules.add("webui_proxy")
                # only announce once per cycle
                if not getattr(run, "_upgrade_skip_announced", False):
                    print("[watchdog] upgrading.lock active — skipping webui/webui_proxy check",
                          flush=True)
                    run._upgrade_skip_announced = True
            else:
                run._upgrade_skip_announced = False

            # Lightweight tick heartbeat (every cycle). Cheap; the file grows
            # at ~6 lines/minute which is fine for human inspection.
            _hb_event("tick", watchdog_pid=os.getpid(),
                      uptime_s=int(time.time() - started_at),
                      tick=tick_count)

            # Snapshot: every SNAPSHOT_EVERY_TICKS ticks (60s) we record the
            # full service state. This is the user's primary "what was
            # running" view; ticks are just liveness.
            snapshot = None
            if tick_count % SNAPSHOT_EVERY_TICKS == 0:
                snapshot = _service_status_snapshot()
                _hb_event("service_status", services=snapshot)

            # Liveness probe: every LIVENESS_PROBE_EVERY_TICKS ticks (40s)
            # call bridge /v1/liveness and emit a `liveness` event with the
            # verdict (ok / degraded / dead). If `dead` persists for
            # LIVENESS_DEAD_ALERT_AFTER consecutive probes, emit a
            # `liveness_dead_alert` so ikaros-remember surfaces it in
            # the daily narrative ("lost all providers at 03:14 UTC").
            if tick_count % LIVENESS_PROBE_EVERY_TICKS == 0:
                lv = _probe_liveness()
                if lv is not None:
                    _hb_event("liveness", **lv)
                    if lv["status"] == "dead":
                        consecutive_dead += 1
                        if consecutive_dead == LIVENESS_DEAD_ALERT_AFTER:
                            _hb_event("liveness_dead_alert",
                                      consecutive_dead=consecutive_dead,
                                      local=lv.get("local"),
                                      cloud=lv.get("cloud"))
                    else:
                        # Recovered: emit recovery event the first time
                        if consecutive_dead >= LIVENESS_DEAD_ALERT_AFTER:
                            _hb_event("liveness_recovered",
                                      after_dead_count=consecutive_dead)
                        consecutive_dead = 0

            # System-change probe: every SYSTEM_PROBE_EVERY_TICKS ticks
            # (~5 min) re-run device-info and diff. Emits system_change
            # events the first time drive/user/host/uuid changes. This
            # is how watchdog answers "what machine am I on right now"
            # without the user having to run bin/hermes-root.bat init.
            if tick_count % SYSTEM_PROBE_EVERY_TICKS == 0:
                cur, changed = _detect_system_change(last_device_info)
                if changed:
                    _hb_event("system_change",
                              changed=changed,
                              prev={k: last_device_info.get(k) for k in changed},
                              cur={k: cur.get(k) for k in changed})
                last_device_info = cur

            # Heartbeat archival: every ARCHIVE_EVERY_TICKS ticks (~24h)
            # run the archiver. The archiver is a no-op if there's
            # nothing old enough to compress/delete, so the cost is
            # one subprocess per day.
            if tick_count % ARCHIVE_EVERY_TICKS == 0:
                _run_archive()

            # Memory core ingest: every MEMORY_INGEST_EVERY_TICKS ticks
            # (~6h) write today's narrative entry to
            # data/hermes-agent/memories/ikaros/YYYY-MM-DD.md. This
            # converts the structured heartbeat into human-readable
            # text the agent can re-read on session start.
            if tick_count % MEMORY_INGEST_EVERY_TICKS == 0:
                _run_memory_ingest()

            # Dojo daily loop (plan 2): every DOJO_DAILY_EVERY_TICKS ticks
            # (~24h) run ikaros-dojo-daily.py → monitor.py + tracker save
            # + daily note. Read-only: never auto-applies skill fixes.
            # The dojo-daily script emits its own heartbeat event.
            if tick_count % DOJO_DAILY_EVERY_TICKS == 0:
                _run_dojo_daily()

            # Per-service port check + restart on DOWN.
            modules = discover_modules()
            for name, m in modules.items():
                if m.type != "service" or not m.port:
                    continue
                if name in skip_modules:
                    continue
                # FIX 2026-06-27: Use HTTP probe for bridge (catch zombie processes),
                # TCP probe for other services (fast, reliable).
                # Zombie process prevention is handled by start.ps1.
                if m.name == "bridge":
                    # Bridge needs HTTP health check - TCP LISTENING doesn't mean
                    # the service is actually responding (uvicorn accept loop can crash)
                    port_up = check_http_health(m.host, m.port, endpoint="/health", timeout_s=3.0)
                else:
                    port_up = check_port(m.host, m.port, timeout_s=1.0)
                if snapshot is None and name in last_svc_state:
                    # Edge-triggered DOWN detection: if last tick saw it
                    # UP but this tick sees it DOWN, log immediately.
                    if last_svc_state[name] and not port_up:
                        _hb_event("service_down", service=name,
                                  port=m.port)
                last_svc_state[name] = port_up
                if port_up:
                    continue
                # cooldown: don't restart the same service repeatedly in a short
                # window (likely start.ps1 itself is broken, not transient crash)
                now = time.time()
                if now - last_restart.get(name, 0) < RESTART_COOLDOWN_S:
                    continue
                print(f"[watchdog] {name} (:{m.port}) DOWN — restarting",
                      flush=True)
                last_restart[name] = now
                ok = restart_service(name)
                _hb_event("restart", service=name, port=m.port, ok=ok)
                if ok:
                    print(f"[watchdog] {name} restarted OK", flush=True)
                else:
                    print(f"[watchdog] {name} restart FAILED (see log)",
                          flush=True)
    except KeyboardInterrupt:
        _hb_event("sleep", reason="KeyboardInterrupt",
                  uptime_s=int(time.time() - started_at))
        print("[watchdog] KeyboardInterrupt — exiting", flush=True)
    finally:
        # Record sleep event BEFORE releasing the PID file. This way the
        # heartbeat log always has a paired wake/sleep entry per watchdog
        # lifetime, even on crash (the 'finally' block still runs).
        try:
            _hb_event("sleep_final",
                      reason="exit",
                      uptime_s=int(time.time() - started_at))
        except Exception:
            pass
        release_singleton()
    return 0


if __name__ == "__main__":
    sys.exit(run())
