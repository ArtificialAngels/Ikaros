#!/usr/bin/env python3
"""Unified command-line dispatcher for Ikaros launcher v1.

The shell launchers only establish ``IKAROS_ROOT``; this module is the
single dispatch layer.  It reads component metadata from
``config/components.yaml`` through :mod:`core.components.registry` and passes
component-specific startup semantics to the existing worker scripts.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import socket
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.components.registry import (  # noqa: E402
    ComponentSpec,
    load_components,
)


START_COMPONENTS = ("embedding", "conversation-tree", "dsh")
WORKER_START_SCRIPTS = {
    # The registry intentionally leaves embedding as a shared resource.  Its
    # component-owned worker is still the authoritative startup command.
    "embedding": "core/memory_v5/services/start-embedding.bat",
}
# 2026-08-23: herdr / omp (pi 底座) 已整体退役 —— 组件收敛为 dsh / conversation-tree /
# embedding 三枚, 基座统一为 deepseek-harness (工作引擎对话/记忆/工具链)。
# point (subcommand / default args / inner-worker marker) is exposed in the
# meantime.
WORKER_DEFAULT_ARGS = {}
WORKER_INNER_ENV = "IKAROS_LAUNCHER_WORKER"


class LauncherError(RuntimeError):
    """A safe, user-facing launcher error."""


def resolve_ikaros_root() -> Path:
    """Resolve the project root, preferring ``IKAROS_ROOT`` when set."""
    configured = os.environ.get("IKAROS_ROOT", "").strip()
    if configured:
        # MSYS/Git-Bash exposes drive roots as /e/... while native Windows
        # Python expects E:/...; convert that form before handing the value
        # to the registry loader.
        if os.name == "nt":
            drive_path = re.match(r"^/([A-Za-z])/(.*)$", configured)
            if drive_path:
                configured = (
                    f"{drive_path.group(1).upper()}:/"
                    f"{drive_path.group(2)}"
                )
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def _component_or_raise(root: Path, component_id: str) -> ComponentSpec:
    specs = load_components(root / "config" / "components.yaml")
    component = next((item for item in specs if item.id == component_id), None)
    if component is None:
        raise LauncherError(
            f"component not configured: {component_id!r} "
            f"(registry: {root / 'config' / 'components.yaml'})"
        )
    return component


def _parse_start_command(command: str) -> list[str]:
    if not command.strip():
        raise LauncherError("component lifecycle.start_script is empty")
    return shlex.split(command, posix=(os.name != "nt"))


def _python_executable(root: Path) -> str:
    configured = Path(os.environ.get("IKAROS_PYTHON", ""))
    if configured.is_file():
        return str(configured)
    local = root / "runtime" / "portable-python" / "python.exe"
    if local.is_file():
        return str(local)
    return sys.executable


def _command_for_subprocess(root: Path, command: str) -> list[str]:
    parts = _parse_start_command(command)
    executable = parts[0]
    suffix = Path(executable).suffix.lower()

    if executable.lower() in {"python", "python3", "py"}:
        return [_python_executable(root), *parts[1:]]
    if executable.lower() in {"llama-server", "llama-server.exe"}:
        llama = root / "runtime" / "llama" / "b10000-cuda" / "llama-server.exe"
        if not llama.is_file():
            raise LauncherError(f"component executable not found: {llama}")
        return [str(llama), *parts[1:]]
    if suffix in {".bat", ".cmd"}:
        script = Path(executable)
        if not script.is_absolute():
            script = root / script
        if not script.is_file():
            raise LauncherError(f"component script not found: {script}")
        arguments = " ".join(shlex.quote(part) for part in parts[1:])
        target = shlex.quote(str(script))
        cmd = f'call {target}' if os.name == "nt" else f'"{script}"'
        if arguments:
            cmd = f"{cmd} {arguments}"
        return [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            cmd,
        ]
    if suffix == ".ps1":
        script = Path(executable)
        if not script.is_absolute():
            script = root / script
        if not script.is_file():
            raise LauncherError(f"component script not found: {script}")
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *parts[1:],
        ]
    if suffix == ".py":
        return [_python_executable(root), executable, *parts[1:]]
    return parts


def start_component(
    root: Path,
    component_id: str,
    args: tuple[str, ...] = (),
) -> int:
    """Start one configured component through its worker start script."""
    component = _component_or_raise(root, component_id)
    command = component.lifecycle.get("start_script") or WORKER_START_SCRIPTS.get(
        component_id
    )
    if not isinstance(command, str) or not command.strip():
        raise LauncherError(
            f"component {component_id!r} has no configured lifecycle.start_script"
        )

    argv = _command_for_subprocess(root, command)
    args = args or WORKER_DEFAULT_ARGS.get(component_id, ())
    if args:
        if argv and Path(argv[0]).suffix.lower() in {".bat", ".cmd"}:
            argv[-1] = f"{argv[-1]} {' '.join(shlex.quote(item) for item in args)}"
        else:
            argv.extend(args)

    # 防递归: dsh 组件的 start_script 是 thin wrapper (调 ikaros),
    # 而 ikaros 又会调 start_script 启动 dsh. 这里直接派 node 真启动 dsh, 跳过 wrapper.
    # 详见 docs/dsh-base-audit-20260820.md #4.
    if component_id == "dsh" and args and args[0] in {"web", "headless"}:
        node = root / "runtime" / "node" / "node.exe"
        dsh_bin = root / "runtime" / "dsh" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
        if not node.is_file():
            raise LauncherError(f"dsh node not found: {node}")
        if not dsh_bin.is_file():
            raise LauncherError(f"dsh bin.js not found: {dsh_bin}")
        web_port = os.environ.get("IKAROS_DSH_WEB_PORT") or str(component.port or 3080)
        if args[0] == "web":
            # web profile auto-loads the patch from ~/.dsh/profiles/web/;
            # --patch is NOT passed here (the old start-dsh-ikaros.bat only
            # used --patch for headless mode).  The patch is synced by
            # bin/sync-dsh-profile-patch.bat.
            argv = [
                str(node), str(dsh_bin), "web",
                "--port", web_port,
            ]
        else:
            # headless mode: --patch is required for the Ikaros overlay.
            overlay = root / "core" / "ikaros-dsh" / "cordis.patch.yml"
            argv = [
                str(node), str(dsh_bin),
                "--profile", "headless",
                "--patch", str(overlay),
            ]
            # headless 接受 task 字符串作为后续 arg
            if len(args) > 1:
                argv.extend(list(args[1:]))

    # 防递归: embedding 的 worker .bat (start-embedding.bat) 也是 thin wrapper
    # 调回 ikaros.bat embed -> 递归. 这里直接派 llama-server.exe, 跳过 wrapper.
    # 校固 design §2.3 / AGENTS.md 2026-08-14 教训: --pooling cls (mean 会让 bge-m3
    # 输出语义降级向量, 沉默死); 历史 .bat 用了 mean, 这次直接固化正确值.
    if component_id == "embedding":
        llama = root / "runtime" / "llama" / "b10000-cuda" / "llama-server.exe"
        model = root / "core" / "memory_v5" / "models" / "bge-m3-q8_0.gguf"
        if not llama.is_file():
            raise LauncherError(f"llama-server.exe not found: {llama}")
        if not model.is_file():
            raise LauncherError(f"embedding model not found: {model}")
        embed_port = str(component.port or 8587)
        argv = [
            str(llama),
            "-m", str(model),
            "--host", "127.0.0.1",
            "--port", embed_port,
            "-ngl", "auto",
            "--embedding",
            "--pooling", "cls",
        ]

    env = os.environ.copy()
    env["IKAROS_ROOT"] = str(root)
    if component_id == "dsh":
        env[WORKER_INNER_ENV] = "1"
    if component.port is not None:
        env.setdefault("IKAROS_COMPONENT_PORT", str(component.port))
    if component_id == "dsh":
        env.setdefault("IKAROS_DSH_WEB_PORT", str(component.port or 3080))

    popen_kwargs: dict[str, Any] = {
        "cwd": root,
        "env": env,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        # DETACHED_PROCESS creates a process with no console.  On Windows,
        # Python servers (uvicorn, FastAPI, etc.) crash when their stdout
        # handle is /dev/null with close_fds=True.  Redirecting to DEVNULL
        # gives the child a valid but silent file descriptor, avoiding the
        # crash.  (llama-server.exe and other native binaries handle /dev/null
        # gracefully; this is a Python-specific issue.)
        popen_kwargs["stdout"] = subprocess.DEVNULL
        popen_kwargs["stderr"] = subprocess.DEVNULL
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(argv, **popen_kwargs)
    except OSError as exc:
        raise LauncherError(
            f"failed to start {component_id!r} with {argv!r}: {exc}"
        ) from exc

    # Wait for the port to come alive before declaring success.  Detached
    # Popen returns immediately even when the child crashes, so we poll the
    # component port (if known) for up to START_WAIT_TIMEOUT.  This catches
    # the "PID reported but process instantly dies" failure mode that the
    # launcher used to silently mask.  Components without a port fall through
    # to the legacy "report and return" path; their health is checked elsewhere.
    import time as _time

    start_wait_timeout = float(os.environ.get("IKAROS_START_WAIT_TIMEOUT", "10"))
    if component.port is not None and start_wait_timeout > 0:
        deadline = _time.monotonic() + start_wait_timeout
        interval = 0.5
        port_up = False
        while _time.monotonic() < deadline:
            if process.poll() is not None:
                # Child already exited -> not a port-wait issue, real crash.
                raise LauncherError(
                    f"{component_id!r} exited immediately after start "
                    f"(rc={process.returncode}, argv={argv!r})"
                )
            if _port_is_open(component.port):
                port_up = True
                break
            _time.sleep(interval)
        if not port_up:
            # Kill the detached child so it does not linger as an orphan.
            try:
                process.terminate()
            except OSError:
                pass
            raise LauncherError(
                f"{component_id!r} did not open port {component.port} "
                f"within {start_wait_timeout:.0f}s after start "
                f"(argv={argv!r})"
            )

    mode = "detached" if os.name == "nt" else "new session"
    print(
        f"[ikaros] started {component.id} ({component.name}) "
        f"PID {process.pid} ({mode})"
    )
    return 0


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _windows_processes_for(component: ComponentSpec) -> list[int]:
    if os.name != "nt":
        return []
    if component.port is not None:
        script = (
            "$p = Get-NetTCPConnection -LocalPort "
            f"{component.port} -State Listen -ErrorAction SilentlyContinue; "
            "$p | Select-Object -ExpandProperty OwningProcess -Unique"
        )
    else:
        marker = component.process_marker.replace("'", "''")
        script = (
            "$p = Get-CimInstance Win32_Process | Where-Object { "
            "$_.ProcessId -ne $PID -and "
            "$_.CommandLine -and $_.CommandLine -like "
            f"'*{marker}*' }}; $p | Select-Object -ExpandProperty ProcessId"
        )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(
        {
            int(line.strip())
            for line in result.stdout.splitlines()
            if line.strip().isdigit()
        }
    )


def component_state(root: Path, component: ComponentSpec) -> tuple[str, list[int]]:
    """Return a shallow launcher-owned state from port/process metadata."""
    if component.port is not None:
        if _port_is_open(component.port):
            return "running", _windows_processes_for(component)
        return "stopped", []

    pids = _windows_processes_for(component)
    return ("running" if pids else "stopped", pids)


def _component_lines(root: Path) -> list[str]:
    lines: list[str] = []
    for component in list_registered_components_for_root(root):
        state, pids = component_state(root, component)
        port = "-" if component.port is None else str(component.port)
        pid_text = ",".join(str(pid) for pid in pids) or "-"
        lines.append(
            f"{component.id:<18} {state:<9} port={port:<5} pid={pid_text}"
        )
    return lines


_HEALTH_FILE = "status.json"  # per-component health snapshot under $IKAROS_LOGS/


def _health_snapshot_path(root: Path, component_id: str) -> Path:
    """Per-component health snapshot path (design §5.3 简单方案).

    Components write ``data/logs/<id>.status.json`` after their own semantic
    checks (embedding probe, dsh /healthz, named pipe reachable, etc.).  The
    launcher only reads; status aggregation lives in :func:`component_state`.
    """
    return root / "data" / "logs" / f"{component_id}.{_HEALTH_FILE}"


def read_health_snapshot(root: Path, component_id: str) -> dict | None:
    """Return the latest parsed health snapshot for ``component_id`` (or None).

    Stale snapshots (older than 90 s) are treated as missing: per design §5.3
    the launcher trusts the file only while it is being refreshed, otherwise
    falls back to port/PID signals.  The 90 s budget covers legitimate
    watchdog intervals (typical is 5-30 s) without keeping dead components
    marked ``healthy`` forever.
    """
    path = _health_snapshot_path(root, component_id)
    if not path.is_file():
        return None
    try:
        import json as _json
        import time as _time

        age = _time.time() - path.stat().st_mtime
        if age > 90:
            return None
        with path.open("r", encoding="utf-8") as handle:
            data = _json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def list_registered_components_for_root(root: Path) -> list[ComponentSpec]:
    """Load registry metadata for an explicitly selected project root."""
    return load_components(root / "config" / "components.yaml")


def _component_count(root: Path) -> int:
    return len(list_registered_components_for_root(root))


def doctor(root: Path | None = None) -> int:
    """Run read-only component and environment diagnostics."""
    project_root = root or resolve_ikaros_root()
    failures: list[str] = []
    if not (project_root / "bin" / "ikaros-env.sh").is_file():
        failures.append("missing bin/ikaros-env.sh")

    count = _component_count(project_root)
    if count == 0:
        failures.append("config/components.yaml contains no components")
    missing: list[str] = []
    for relative in (
        "runtime/portable-python/python.exe",
        "runtime/node/node.exe",
        "runtime/llama/b10000-cuda/llama-server.exe",
    ):
        if not (project_root / relative).is_file():
            missing.append(relative)
    model = project_root / "core/memory_v5/models/bge-m3-q8_0.gguf"
    if not model.is_file() or model.stat().st_size < 500 * 1024 * 1024:
        missing.append("core/memory_v5/models/bge-m3-q8_0.gguf (>= 500 MiB)")

    print(f"IKAROS_ROOT={project_root}")
    print(f"components={count}")
    print("name | status | detail")
    for line in _component_lines(project_root):
        print(f"{line.split(maxsplit=1)[0]} | {line.split()[1]} | {line}")
    if missing:
        print(f"WARN: ignored or missing runtime artifacts ({len(missing)}):")
        for item in missing:
            print(f"  - {item}")

    if failures:
        print(f"FAIL ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("OK: component registry and anchored launcher are available")
    return 0


def status(root: Path | None = None) -> int:
    """Display shallow launcher-owned status for every registered component."""
    project_root = root or resolve_ikaros_root()
    try:
        components = list_registered_components_for_root(project_root)
    except (OSError, ValueError) as exc:
        raise LauncherError(f"cannot read component registry: {exc}") from exc
    if not components:
        raise LauncherError("component registry is empty")

    print("name | status | detail")
    for component in components:
        state, pids = component_state(project_root, component)
        port = "-" if component.port is None else str(component.port)
        pid_text = ",".join(str(pid) for pid in pids) or "-"
        snapshot = read_health_snapshot(project_root, component.id)
        if snapshot is None:
            health_text = "-"
        else:
            health_status = str(snapshot.get("status", "?"))
            checked_at = snapshot.get("checked_at", "-")
            health_text = f"{health_status}@{checked_at}"
        print(
            f"{component.id} | {state} | "
            f"port={port}; pid={pid_text}; marker={component.process_marker}; "
            f"health={health_text}"
        )
    return 0


def _topological_components(root: Path) -> list[ComponentSpec]:
    components = list_registered_components_for_root(root)
    by_id = {component.id: component for component in components}
    resolved: set[str] = set()
    result: list[ComponentSpec] = []
    remaining = set(by_id)

    while remaining:
        progress = False
        for component_id in tuple(remaining):
            component = by_id[component_id]
            dependencies = {
                "embedding" if dep in {"memory_v5", "embedding"} else dep
                for dep in component.dependencies
            }
            if dependencies <= resolved:
                result.append(component)
                resolved.add(component_id)
                remaining.remove(component_id)
                progress = True
        if not progress:
            cycle = ", ".join(sorted(remaining))
            raise LauncherError(f"component dependency cycle detected: {cycle}")
    return result


def start_all(root: Path) -> int:
    """Start registered web-stack components in dependency order.

    dsh is started with ``web`` mode by default (the headless variant
    requires an explicit ``ikaros web --headless`` or ``ikaros dsh headless``).
    """
    components = _topological_components(root)
    started: list[str] = []
    failed: list[str] = []
    for component in components:
        if component.id not in START_COMPONENTS:
            continue
        try:
            if component.id == "dsh":
                # dsh 防递归逻辑需要 ("web",) 参数触发 (line 190-208)
                start_component(root, component.id, ("web",))
            else:
                start_component(root, component.id)
        except LauncherError as exc:
            print(f"[ikaros] WARN: {exc}")
            failed.append(component.id)
        else:
            started.append(component.id)

    print(
        "[ikaros] all summary: "
        f"started={','.join(started) or '-'} "
        f"failed={','.join(failed) or '-'} "
    )
    return 1 if failed else 0


def _stop_windows_pids(component: ComponentSpec, pids: Sequence[int]) -> int:
    if not pids:
        print(f"[ikaros] {component.id} is not running")
        return 0
    script = (
        "$ids = @("
        + ",".join(str(pid) for pid in pids)
        + "); foreach ($id in $ids) { Stop-Process -Id $id -ErrorAction Stop }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise LauncherError(
            f"failed to stop {component.id!r} gracefully: {detail}"
        )
    print(f"[ikaros] stopped {component.id} (graceful signal)")
    return 0


def stop_component(root: Path, component_id: str) -> int:
    """Stop one component without force-killing an unrelated process."""
    component = _component_or_raise(root, component_id)
    state, pids = component_state(root, component)
    if state == "stopped":
        print(f"[ikaros] {component.id} is not running")
        return 0
    if os.name == "nt":
        return _stop_windows_pids(component, pids)
    print(
        f"[ikaros] stop for {component.id} is Windows-oriented; "
        "no process was killed"
    )
    return 1


def _cmd_ps(root: Path) -> int:
    helper = root / "bin" / "proc.py"
    if not helper.is_file():
        raise LauncherError(f"process helper not found: {helper}")
    result = subprocess.run(
        [_python_executable(root), str(helper), "ps"],
        cwd=root,
        check=False,
    )
    return result.returncode


def _resolve_log_path(root: Path, component_id: str) -> Path | None:
    """Find the most recent log file for ``component_id``.

    Priority:
    1. ``data/logs/<id>.out.log`` (launcher convention, written by the
       component's own watchdog when running detached).
    2. ``~/.dsh/ikaros-dsh-web.out.log`` (legacy dsh web stdout location,
       preserved so existing users do not lose log access during the
       base-swap transition).
    """
    candidates = [
        root / "data" / "logs" / f"{component_id}.out.log",
        Path.home() / ".dsh" / "ikaros-dsh-web.out.log",
    ]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def _cmd_logs(root: Path, component_id: str, follow: bool, rotate: bool, lines: int) -> int:
    """Show the latest log for a component, optionally tailing or rotating.

    ``--rotate`` is intentionally minimal (single-file rename + .1 suffix) —
    design §7.3 defers real log rotation to a future phase.
    """
    component = _component_or_raise(root, component_id)
    target = _resolve_log_path(root, component.id)
    if target is None:
        print(f"[ikaros] no log found for {component.id}")
        return 1

    if rotate:
        backup = target.with_suffix(target.suffix + ".1")
        try:
            target.rename(backup)
        except OSError as exc:
            raise LauncherError(f"rotate failed: {exc}") from exc
        print(f"[ikaros] rotated {target} -> {backup}")
        return 0

    print(f"==> {target} (last {lines} lines) <==")
    text = target.read_text(encoding="utf-8", errors="replace")
    history = text.splitlines()[-lines:]
    for line in history:
        print(line)

    if not follow:
        return 0

    # --follow: tail -F semantics (reopen if rotated)
    import time as _time

    print(f"==> following {target} (Ctrl-C to stop) <==")
    inode = target.stat().st_ino
    last_size = target.stat().st_size
    try:
        while True:
            _time.sleep(1.0)
            if not target.exists():
                print(f"[ikaros] log vanished: {target}")
                return 0
            stat = target.stat()
            if stat.st_ino != inode:
                print(f"[ikaros] log rotated, reopening {target}")
                inode = stat.st_ino
                last_size = 0
            if stat.st_size > last_size:
                with target.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(last_size)
                    chunk = handle.read()
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                last_size = stat.st_size
    except KeyboardInterrupt:
        print("\n[ikaros] follow stopped")
        return 0


def _cmd_update(root: Path) -> int:
    print(
        "[ikaros] update is reserved for a later launcher version; "
        f"no git fetch was performed ({root})"
    )
    return 0


def _cmd_runtime_check(root: Path) -> int:
    """Validate the complete runtime environment (portable, cross-folder).

    Checks every vendored runtime component for existence, version, and
    capability.  Designed to be run after cloning the project into a new
    folder — the report tells you exactly what is missing and how to fix it.
    """
    import shutil
    import json as _json

    def _run_version(path: Path, *args: str, timeout: int = 5) -> str:
        """Run an executable's --version and return the first line."""
        try:
            if not path.is_file():
                return "?"
            result = subprocess.run(
                [str(path), *args],
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            version = result.stdout.strip() or result.stderr.strip()
            return version.splitlines()[0].strip()[:60] if version else "?"
        except (OSError, subprocess.TimeoutExpired):
            return "?"

    def _mb(path: Path) -> str:
        """Return file size in MB (or '-' if missing)."""
        if not path.is_file():
            return "-"
        return f"{path.stat().st_size / 1024 / 1024:.0f} MB"

    def _disk_free(path: Path) -> str:
        """Return free disk space on the drive containing path."""
        try:
            total, used, free = shutil.disk_usage(path.anchor if path.anchor else path)
            return f"{free / 1024 / 1024 / 1024:.1f} GB"
        except (OSError, ValueError):
            return "?"

    print("=" * 72)
    print(f"  Ikaros Runtime Environment Check  (IKAROS_ROOT={root})")
    print("=" * 72)

    # ---- check list ----
    checks: list[tuple[str, Path, str, str]] = []

    # (category, path, version_cmd, hint)
    def add(name: str, rel: str, version_args: tuple[str, ...] = ("--version",),
            hint: str = ""):
        p = root / rel
        v = _run_version(p, *version_args) if p.is_file() else "?"
        checks.append((name, p, v, hint))

    # Runtime executables
    add("portable-python", "runtime/portable-python/python.exe", ("--version",),
        "核心运行时: 所有 Python 子命令依赖")
    add("node", "runtime/node/node.exe", ("--version",), "dsh 与前端构建依赖")
    add("bun", "runtime/node/node_modules/bun/bin/bun.exe", ("--version",), "快速 JS 运行器")
    add("git", "runtime/git/bin/git.exe", ("--version",), "便携 git (版本控制)")
    add("aria2", "runtime/aria2/aria2c.exe", ("--version",), "下载器 (下 gguf)")
    add("gopeed", "runtime/gopeed/gopeed-web.exe", ("--version",), "下载器 (备选)")

    # Llama.cpp
    llama = root / "runtime/llama/b10000-cuda/llama-server.exe"
    llama_v = _run_version(llama, "--version") if llama.is_file() else "?"
    checks.append(("llama-server", llama, llama_v, "Embedding 推理引擎 (bge-m3)"))

    # DSH (node.js package)
    dsh = root / "runtime/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js"
    dsh_ok = dsh.is_file()
    dsh_v = "installed" if dsh_ok else "MISSING"
    checks.append(("dsh (node_modules)", dsh, dsh_v, "工作引擎: 需 npm install"))

    # Rust
    rust = root / "runtime/rust/bin/cargo.exe"
    rust_v = _run_version(rust, "--version") if rust.is_file() else "?"
    checks.append(("rust/cargo", rust, rust_v, "Rust 工具链 (便携版)"))

    # MCPServe
    mcp = root / "runtime/MCPServe/codebase-memory/package/bin/codebase-memory-mcp.exe"
    mcp_v = _run_version(mcp, "--version") if mcp.is_file() else "?"
    checks.append(("MCPServe", mcp, mcp_v, "MCP 代码库记忆工具"))

    # Everything
    es = root / "runtime/everything/es.exe"
    es_v = _run_version(es, "--version") if es.is_file() else "?"
    checks.append(("everything", es, es_v, "文件搜索 (IPC 管道)"))

    # memos
    memos = root / "runtime/memos/memos.exe"
    memos_v = _run_version(memos, "--version") if memos.is_file() else "?"
    checks.append(("memos", memos, memos_v, "便携备忘录服务"))

    # Model file
    model = root / "core/memory_v5/models/bge-m3-q8_0.gguf"
    model_sz = _mb(model)
    checks.append(("bge-m3 模型", model, model_sz, "Embedding 向量模型 (>= 500 MiB)"))

    # CUDA
    cuda_dll = root / "runtime/llama/b10000-cuda/cudart64_13.dll"
    cuda_status = "cuda" if cuda_dll.is_file() else "cpu-only"
    checks.append(("CUDA runtime", cuda_dll, cuda_status, "GPU 加速 (llama.cpp)"))

    # Disk space
    free = _disk_free(root / "data")
    checks.append(("磁盘空间(根)", root, free, "至少 10 GB 空闲"))

    # ---- print report ----
    print(f"\n{'component':<22} {'status':<8} {'version':<20}  detail")
    print("-" * 72)
    ok = miss = warn = 0
    for name, path, version, hint in checks:
        if path.is_file() or (name == "dsh (node_modules)" and dsh_ok) or \
           name == "磁盘空间(根)":
            status = "✅"
            ok += 1
        elif name == "CUDA runtime":
            status = "⚠️"
            warn += 1
        else:
            status = "❌"
            miss += 1
        v = version[:20] if version else "?"
        print(f"  {name:<20} {status:<8} {v:<20}  {hint}")

    print("-" * 72)
    print(f"  ✅ {ok} available  ⚠️ {warn} warnings  ❌ {miss} missing")
    if miss:
        print()
        print("  修复方法:")
        print("    1) 首次部署: python scripts/fetch-upstreams.py")
        print("    2) 增量补缺: python scripts/setup-native.py --check")
        print("    3) 缺 dsh:  cd runtime/dsh && npm install")
        print("    4) 缺模型: 下载 bge-m3-q8_0.gguf 放入 core/memory_v5/models/")
    print("=" * 72)
    return 1 if miss else 0


def dispatch(argv: Sequence[str]) -> int:
    """Dispatch one launcher subcommand and return an exit code."""
    if not argv:
        return _usage_error()
    command = argv[0].lower()
    args = tuple(argv[1:])
    root = resolve_ikaros_root()

    if command == "web":
        return start_component(root, "dsh", ("web",))
    if command == "tree":
        return start_component(root, "conversation-tree")
    if command == "embed":
        return start_component(root, "embedding")
    if command == "all":
        return start_all(root)
    if command == "doctor":
        return doctor(root)
    if command in ("check", "runtime-check"):
        return _cmd_runtime_check(root)
    if command == "status":
        return status(root)
    if command == "ps":
        return _cmd_ps(root)
    if command == "logs":
        if not args:
            return _usage_error("ikaros logs <component> [--follow] [--rotate] [--lines N]")
        component_id = args[0]
        log_args = args[1:]
        follow = "--follow" in log_args or "-f" in log_args
        rotate = "--rotate" in log_args
        lines = 30
        if "--lines" in log_args:
            idx = log_args.index("--lines")
            if idx + 1 < len(log_args):
                try:
                    lines = int(log_args[idx + 1])
                except ValueError:
                    return _usage_error("ikaros logs --lines N (N must be integer)")
        elif "-n" in log_args:
            idx = log_args.index("-n")
            if idx + 1 < len(log_args):
                try:
                    lines = int(log_args[idx + 1])
                except ValueError:
                    return _usage_error("ikaros logs -n N (N must be integer)")
        return _cmd_logs(root, component_id, follow, rotate, lines)
    if command == "stop":
        if not args:
            return _usage_error("ikaros stop <component>")
        return stop_component(root, args[0])
    if command == "restart":
        if not args:
            return _usage_error("ikaros restart <component>")
        component_id = args[0]
        stop_component(root, component_id)
        # dsh 防递归需要 (args) 参数触发
        restart_args = ("web",) if component_id == "dsh" else ()
        return start_component(root, component_id, restart_args)
        if not args:
            return _usage_error("ikaros restart <component>")
        component_id = args[0]
        stop_component(root, component_id)
        # dsh 防递归需要 (args) 参数触发
        restart_args = ("web",) if component_id == "dsh" else ()
        return start_component(root, component_id, restart_args)
    if command == "update":
        return _cmd_update(root)
    raise LauncherError(f"unknown subcommand: {command}")


def _usage_error(message: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ikaros",
        description="Unified Ikaros component launcher",
    )
    parser.add_argument(
        "subcommand",
        choices=(
            "web",
            "tree",
            "embed",
            "all",
            "doctor",
            "check",
            "runtime-check",
            "update",
            "status",
            "ps",
            "logs",
            "stop",
            "restart",
        ),
    )
    parser.add_argument("component", nargs="?")
    parser.print_usage(sys.stderr)
    if message:
        parser.error(message)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point with stable error-to-exit-code handling."""
    try:
        return dispatch(sys.argv[1:] if argv is None else argv)
    except (LauncherError, OSError, ValueError) as exc:
        print(f"[ikaros] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
