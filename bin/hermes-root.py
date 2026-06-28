"""
bin/hermes-root.py -- Hermes root resolver (single source of truth).

Resolves HERMES_ROOT for portable, drive-letter-agnostic Hermes deployments.
This is the ONLY place that decides "where is the project root" on disk.
Every bat / ps1 / py in the project should call into here.

Subcommands
-----------
resolve   Print the absolute HERMES_ROOT path. Single line on stdout.
verify    Validate that the resolved path has all required markers.
          Exit 0 if OK, 1 if any missing (with reasons on stderr).
init      bat-friendly: resolve + verify + persist + print env block
          (KEY=VALUE lines parseable by `for /f` in cmd.exe).
scan      Diagnose: scan all drive letters for candidate Hermes roots.
persist   Write .hermes-root cache file at <bin/..>/.hermes-root.
clean     Remove .hermes-root cache file.

Resolution priority (first hit wins)
------------------------------------
1. HERMES_ROOT env var (explicit override from caller)
2. .hermes-root cache file at <bin/..>/.hermes-root
3. <bin/..>  (one level up from this script: assume this is <root>\\bin\\)
4. Scan drive letters D:\\..Z:\\ for <drive>:\\<PROJECT_FOLDER>\\
"""
from __future__ import annotations

import argparse
import ctypes
import os
import platform
import socket
import string
import subprocess
import sys
from pathlib import Path


# ---- Paths & markers ----

# This file lives at <HERMES_ROOT>/bin/hermes-root.py
HERE = Path(__file__).resolve()
SCRIPT_BIN_DIR = HERE.parent
INFERRED_ROOT_FROM_SCRIPT = SCRIPT_BIN_DIR.parent

# Project root markers (any one missing -> verification fails)
REQUIRED_MARKERS = [
    "portable-python",   # embedded Python interpreter
    "hermes-agent",      # agent Python source
    "data",              # data directory
    "bin",               # bin directory (we are in it)
    "modules",           # modules directory
]
# Critical: the embedded Python exe must exist for the project to run
CRITICAL_MARKER_PARTS = ("portable-python", "python.exe")

# Cache file: stores the last known HERMES_ROOT
CACHE_FILE = ".hermes-root"

# The standard project folder name (used when scanning drives)
PROJECT_FOLDER_NAME = "Ikaros"

# Exit codes
EXIT_OK = 0
EXIT_MISSING_MARKER = 1
EXIT_NOT_FOUND = 2
EXIT_PERSIST_FAIL = 3


# ---- ANSI colors (Windows 10+ supports VT100 in cmd) ----

class C:
    GRN = "\033[32m"
    YEL = "\033[33m"
    RED = "\033[31m"
    DIM = "\033[2m"
    RST = "\033[0m"
    BLD = "\033[1m"


# ---- Helpers ----

def is_windows() -> bool:
    return sys.platform == "win32"


def get_drive_letters() -> list:
    """Return a list of existing fixed/removable drive letters on Windows (D:..Z:).

    Skips A: and B: (legacy floppy drives -- GetLogicalDrives still reports them
    on some VMs and they are never where Hermes lives). Uses Win32 API.
    """
    if not is_windows():
        return []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except (OSError, AttributeError):
        return []
    letters = []
    for i, letter in enumerate(string.ascii_uppercase):
        if i < 2:  # skip A: B:
            continue
        if bitmask & (1 << i):
            letters.append(f"{letter}:")
    return letters


def has_critical_marker(p: Path) -> bool:
    """True if the project would actually be runnable from this path."""
    return (p.joinpath(*CRITICAL_MARKER_PARTS)).is_file()


def looks_like_hermes_root(p: Path) -> tuple:
    """Return (is_valid, missing_markers).

    A path is "valid" if all REQUIRED_MARKERS subdirs exist. We do NOT require
    the critical python.exe here -- that's a stricter "runnable" check used
    by `resolve()`. Missing list is empty when valid.
    """
    missing = []
    for m in REQUIRED_MARKERS:
        if not (p / m).exists():
            missing.append(m)
    return (len(missing) == 0, missing)


# ---- Resolver ----

def resolve() -> tuple:
    """Resolve HERMES_ROOT per priority. Returns (path_or_None, source_label)."""
    # 1. Explicit env var
    env_root = os.environ.get("HERMES_ROOT", "").strip()
    if env_root:
        p = Path(env_root).resolve()
        if has_critical_marker(p):
            return (p, "env:HERMES_ROOT")

    # 2. Cache file at <bin/..>/.hermes-root
    cache = SCRIPT_BIN_DIR.parent / CACHE_FILE
    if cache.exists():
        try:
            cached = cache.read_text(encoding="utf-8").strip()
        except OSError:
            cached = ""
        if cached:
            p = Path(cached).resolve()
            if has_critical_marker(p):
                return (p, f"cache:{CACHE_FILE}")

    # 3. Inferred from this script's location
    p = INFERRED_ROOT_FROM_SCRIPT
    if has_critical_marker(p):
        return (p, "inferred:script-location")

    # 4. Scan drive letters
    for letter in get_drive_letters():
        candidate = Path(f"{letter}\\{PROJECT_FOLDER_NAME}")
        if has_critical_marker(candidate):
            return (candidate, f"scan:{letter}\\")
    return (None, "not-found")


# ---- Persistence ----

def persist(root: Path) -> None:
    """Write root path to .hermes-root cache file (atomic: tempfile + os.replace)."""
    cache = SCRIPT_BIN_DIR.parent / CACHE_FILE
    tmp = cache.with_suffix(cache.suffix + ".tmp")
    try:
        tmp.write_text(str(root) + "\n", encoding="utf-8")
        os.replace(tmp, cache)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---- Subcommands ----

def cmd_resolve(_args) -> int:
    root, _source = resolve()
    if root is None:
        print(f"{C.RED}HERMES_ROOT not found{C.RST}", file=sys.stderr)
        return EXIT_NOT_FOUND
    print(str(root))
    return EXIT_OK


def cmd_verify(_args) -> int:
    root, source = resolve()
    if root is None:
        print(f"{C.RED}[FAIL] HERMES_ROOT could not be resolved{C.RST}", file=sys.stderr)
        print(f"  Tried: env, .hermes-root cache, script-location, drive scan", file=sys.stderr)
        return EXIT_NOT_FOUND
    valid, missing = looks_like_hermes_root(root)
    print(f"{C.BLD}HERMES_ROOT{C.RST}: {root}")
    print(f"{C.DIM}Source: {source}{C.RST}")
    if valid:
        print(f"{C.GRN}[OK] All required markers present{C.RST}")
        return EXIT_OK
    print(f"{C.RED}[FAIL] Missing markers: {', '.join(missing)}{C.RST}")
    return EXIT_MISSING_MARKER


def cmd_init(_args) -> int:
    """bat-friendly: resolve, verify, persist, print env block.

    Output (parseable by cmd `for /f`):
        KEY=VALUE lines, one per line, no quoting.
        Plus a final HERMES_STATUS=ok|incomplete|not_found sentinel.

    bat usage:
        for /f "usebackq tokens=1,* delims==" %%K in (`call bin\\hermes-root.bat init`) do set "%%K=%%L"
    """
    root, source = resolve()
    if root is None:
        print(f"HERMES_STATUS=not_found", file=sys.stdout)
        print(f"# HERMES_ROOT not found -- tried env, .hermes-root cache, "
              f"script-location, drive scan", file=sys.stderr)
        # Also dump device info so a "machine changed?" guess is easy
        print(f"# device info: {collect_device_info()}", file=sys.stderr)
        return EXIT_NOT_FOUND

    valid, missing = looks_like_hermes_root(root)
    if not valid:
        print(f"# Missing markers: {', '.join(missing)}", file=sys.stderr)
        print(f"# device info: {collect_device_info()}", file=sys.stderr)

    # Persist successful resolution (even if incomplete -- it pins the path
    # so subsequent calls don't re-scan drives)
    try:
        persist(root)
    except OSError as e:
        print(f"# WARN: could not persist .hermes-root: {e}", file=sys.stderr)

    # Print env block
    info = collect_device_info()
    env_block = [
        ("HERMES_ROOT",         str(root)),
        ("HERMES_BIN",          str(root / "bin")),
        ("HERMES_DATA",         str(root / "data")),
        ("HERMES_HOME",         str(root / "data" / "hermes-agent")),
        ("HERMES_MODELS",       str(root / "data" / "models")),
        ("HERMES_PYTHON",       str(root / "portable-python" / "python.exe")),
        ("HERMES_DEPS",         str(root / "deps")),
        ("HERMES_RUNTIME",      str(root / "runtime")),
        ("HERMES_CONFIG",       str(root / "config" / "hermes.yaml")),
        ("HERMES_MODULES",      str(root / "modules")),
        ("HERMES_LOGS",         str(root / "data" / "logs")),
        ("HERMES_DATA_DIR",     str(root / "hermes" / "data")),
        ("HERMES_RESOLVE_SOURCE", source),
        # Device fingerprint — never trust drive letters for machine identity.
        # Use these to verify "am I on the same machine as last session?" even
        # if HERMES_ROOT is on a different drive or USB stick.
        ("HERMES_HOST",         info["host"]),
        ("HERMES_BIOS_UUID",    info["bios_uuid"]),
        ("HERMES_BIOS_SERIAL",  info["bios_serial"]),
        ("HERMES_OS",           info["os"]),
        ("HERMES_USER",         info["user"]),
        ("HERMES_STATUS",       "ok" if valid else "incomplete"),
    ]
    for k, v in env_block:
        print(f"{k}={v}")
    return EXIT_OK if valid else EXIT_MISSING_MARKER


def cmd_scan(_args) -> int:
    """Scan all drive letters and report candidates (diagnostic)."""
    print(f"{C.BLD}Scanning drive letters for Hermes project...{C.RST}")
    print(f"  Project folder name: {PROJECT_FOLDER_NAME}")
    print(f"  Critical marker:     portable-python{os.sep}python.exe")
    print()
    found = []
    for letter in get_drive_letters():
        candidate = Path(f"{letter}\\{PROJECT_FOLDER_NAME}")
        if not candidate.exists():
            continue
        valid, missing = looks_like_hermes_root(candidate)
        runnable = has_critical_marker(candidate)
        if valid and runnable:
            tag = f"{C.GRN}[VALID + runnable]{C.RST}"
        elif valid:
            tag = f"{C.YEL}[VALID markers, missing python.exe]{C.RST}"
        elif runnable:
            tag = f"{C.YEL}[runnable, missing: {', '.join(missing)}]{C.RST}"
        else:
            tag = f"{C.DIM}[exists, missing: {', '.join(missing)}]{C.RST}"
        print(f"  {tag} {candidate}")
        if runnable:
            found.append(candidate)
    print()
    if found:
        print(f"{C.GRN}Found {len(found)} runnable candidate(s).{C.RST}")
        for f in found:
            print(f"  -> {f}")
        return EXIT_OK
    print(f"{C.RED}No runnable Hermes root found on any drive.{C.RST}")
    return EXIT_NOT_FOUND


def cmd_persist(_args) -> int:
    root, source = resolve()
    if root is None:
        print(f"{C.RED}Cannot persist: HERMES_ROOT not resolved{C.RST}", file=sys.stderr)
        return EXIT_NOT_FOUND
    try:
        persist(root)
    except OSError as e:
        print(f"{C.RED}Failed to persist: {e}{C.RST}", file=sys.stderr)
        return EXIT_PERSIST_FAIL
    print(f"{C.GRN}Persisted: {root} -> {SCRIPT_BIN_DIR.parent / CACHE_FILE}{C.RST}")
    print(f"{C.DIM}(source: {source}){C.RST}")
    return EXIT_OK

def cmd_clean(_args) -> int:
    """Remove .hermes-root cache file at the project root."""
    cache = SCRIPT_BIN_DIR.parent / CACHE_FILE
    if cache.exists():
        cache.unlink()
        print(f"{C.GRN}Removed: {cache}{C.RST}")
    else:
        print(f"{C.DIM}No cache file at: {cache}{C.RST}")
    return EXIT_OK


# ============================================================
# Device fingerprint (2026-06-17)
# ============================================================
# Why this lives here: the user explicitly asked that "running on a
# different machine" be detectable when something goes wrong. Drive letters
# (E:\ F:\ ...) are not stable across machines; HERMES_ROOT may even live
# on a USB stick that moves between machines. The only stable per-machine
# identifiers are OS-level: hostname, BIOS UUID, BIOS serial, OS build,
# and the logged-in user.
#
# Failure mode this guards against: an agent concludes "the project moved
# drives" and silently re-installs or re-pins state on a different machine
# than the user's last session — overwriting per-machine state.
#
# Use the helper on every error path so the user can diff their own
# fingerprint against the previous session's.

def _wmic(field: str, klass: str = "Win32_ComputerSystemProduct", timeout: float = 2.0) -> str:
    """Read a single field via PowerShell CIM. Empty string on any failure.

    Why PowerShell and not wmic: wmic on Windows 11 24H2 produces mixed
    codepage output that crashes Python's text decoder, and the wmic
    command itself is deprecated. PowerShell's Get-CimInstance handles
    any string with a single .ToString() call we control, and the call
    is fast (CIM uses WinRM/WSMan locally, no shell overhead).
    """
    if not is_windows():
        return ""
    try:
        ps_cmd = (
            f"[Console]::OutputEncoding=[Text.UTF8Encoding]::UTF8;"
            f"(Get-CimInstance -ClassName '{klass}' "
            f"| Select-Object -First 1 -ExpandProperty '{field}' "
            f"| ForEach-Object {{ $_.ToString().Trim() }})"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, timeout=timeout,
        )
        # PowerShell -Command output is the active console codepage (UTF-8
        # after we set it). Decode as utf-8 with replace as a safety net.
        out = r.stdout.decode("utf-8", errors="replace")
        # Strip the BOM if present
        if out.startswith("\ufeff"):
            out = out[1:]
        for ln in out.splitlines():
            ln = ln.strip()
            if ln:
                return ln
    except Exception:
        return ""
    return ""


def collect_device_info() -> dict:
    """Return a stable per-machine fingerprint. Empty fields when unknown."""
    info = {
        "host": "",
        "user": "",
        "os": "",
        "bios_uuid": "",
        "bios_serial": "",
    }
    # host + user + os are stdlib; no subprocess needed
    try:
        info["host"] = os.environ.get("COMPUTERNAME", "") or ""
        if not info["host"]:
            info["host"] = socket.gethostname()
    except Exception:
        pass
    try:
        info["user"] = os.environ.get("USERNAME", "") or os.environ.get("USER", "")
    except Exception:
        pass
    # OS string — keep it short and stable, no locale noise
    if is_windows():
        try:
            info["os"] = f"Windows {platform.release()} (build {platform.version()})"
        except Exception:
            info["os"] = "Windows"
    else:
        try:
            info["os"] = f"{platform.system()} {platform.release()}"
        except Exception:
            info["os"] = platform.system() or "unknown"
    # BIOS-level identifiers — survive OS reinstall, drive swaps, USB stick
    # moves. THIS is the only thing that truly identifies a machine.
    info["bios_uuid"] = _wmic("UUID", "Win32_ComputerSystemProduct")
    info["bios_serial"] = _wmic("SerialNumber", "Win32_BIOS")
    return info


def cmd_device_info(_args) -> int:
    """Print a compact device fingerprint. Use to diff "same machine?" across sessions."""
    info = collect_device_info()
    summary = (
        f"host={info['host']} user={info['user']} "
        f"uuid={info['bios_uuid']} serial={info['bios_serial']} os={info['os']}"
    )
    print(summary)
    return EXIT_OK


# ---- Main ----

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hermes root resolver (single source of truth for HERMES_ROOT)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("resolve", help="Print resolved HERMES_ROOT path (single line)")
    sub.add_parser("verify", help="Verify all required markers exist")
    sub.add_parser("init",
                   help="bat-friendly: print env block KEY=VALUE for `for /f` parsing")
    sub.add_parser("scan", help="Scan all drive letters for candidates")
    sub.add_parser("persist", help="Write .hermes-root cache file")
    sub.add_parser("clean", help="Remove .hermes-root cache file")
    sub.add_parser("device-info",
                   help="Print device fingerprint (host/uuid/bios/os/user)")
    args = parser.parse_args()

    handlers = {
        "resolve": cmd_resolve,
        "verify": cmd_verify,
        "init": cmd_init,
        "scan": cmd_scan,
        "persist": cmd_persist,
        "clean": cmd_clean,
        "device-info": cmd_device_info,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
