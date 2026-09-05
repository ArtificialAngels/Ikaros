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
            # `ikaros dsh sync` (see _dsh_sync() below).
            # --no-open 防止 dsh 自动开系统默认浏览器（用户偏好：自己用 ikaros dsh open 开 Chrome --app，
            # 避免 Edge/Chrome 重复窗口）。
            argv = [
                str(node), str(dsh_bin), "web",
                "--port", web_port,
                "--no-open",
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

    # 动态端口组件: healthcheck.type == "port_file" — 从端口文件读实际端口再探测。
    # server.py (--port 0) 绑定后把 OS 分配的端口写进 tmp/ct-port.json。
    hc = component.healthcheck or {}
    if hc.get("type") == "port_file" and start_wait_timeout > 0:
        port_file = root / hc.get("endpoint", "tmp/ct-port.json")
        deadline = _time.monotonic() + start_wait_timeout
        actual_port: int | None = None
        while _time.monotonic() < deadline:
            if process.poll() is not None:
                raise LauncherError(
                    f"{component_id!r} exited immediately after start "
                    f"(rc={process.returncode}, argv={argv!r})"
                )
            try:
                import json as _json
                actual_port = _json.loads(port_file.read_text(encoding="utf-8")).get("port")
            except (OSError, ValueError):
                actual_port = None
            if actual_port and _port_is_open(actual_port):
                break
            _time.sleep(0.5)
        if not (actual_port and _port_is_open(actual_port)):
            raise LauncherError(
                f"{component_id!r} did not report a working port via {port_file} "
                f"within {start_wait_timeout:.0f}s after start (argv={argv!r})"
            )
        print(f"[ikaros] {component.id} listening on dynamic port {actual_port}")
    elif component.port is not None and start_wait_timeout > 0:
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


def _read_port_from_file(root: Path, component: ComponentSpec) -> int | None:
    """Read dynamic port from healthcheck (type=port_file, endpoint=<rel_path>).
    healthcheck shape: {type: 'port_file', endpoint: 'tmp/ct-port.json'}.
    Returns port int if file exists and contains valid JSON {port: N}, else None."""
    healthcheck = component.healthcheck or {}
    if healthcheck.get("type") != "port_file":
        return None
    rel = healthcheck.get("endpoint")  # path is in 'endpoint' field, not 'port_file'
    if not rel:
        return None
    path = root / rel
    if not path.is_file():
        return None
    try:
        import json as _json
        with path.open(encoding="utf-8") as f:
            data = _json.load(f)
        port = data.get("port")
        return int(port) if isinstance(port, (int, float)) else None
    except (OSError, ValueError, _json.JSONDecodeError):
        return None


def _pids_for_port(port: int) -> list[int]:
    """Return list of unique PIDs listening on the given TCP port (Windows)."""
    if os.name != "nt":
        return []
    script = (
        "$p = Get-NetTCPConnection -LocalPort "
        f"{port} -State Listen -ErrorAction SilentlyContinue; "
        "$p | Select-Object -ExpandProperty OwningProcess -Unique"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10, check=False,
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
    # 2026-09-05 修复: 支持 healthcheck.type=port_file (conversation-tree 用):
    # 动态端口, 不能用 component.port 硬检查. 从 port_file 读实际端口再探.
    healthcheck = component.healthcheck or {}
    if healthcheck.get("type") == "port_file":
        port = _read_port_from_file(root, component)
        if port is not None and _port_is_open(port):
            # 用读出的端口找 PID (同 component.port 路径, 走 Get-NetTCPConnection)
            pids = _pids_for_port(port)
            return ("running", pids)
        return "stopped", []
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
    # 2026-09-05 修复: Stop-Process 在 server.py 有 self-respawn watchdog 时停不下来
    # (它会立刻再起). 用 taskkill /T /F 强制杀进程树. 父进程已 detached spawn,
    # 树杀才能真正清理.
    if os.name != "nt":
        return 0
    for pid in pids:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True, check=False, timeout=10,
        )
    print(f"[ikaros] stopped {component.id} (tree-killed: {','.join(str(p) for p in pids)})")
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


def _cmd_dsh(root: Path, args: tuple[str, ...]) -> int:
    """`ikaros dsh <status|open|sync|restart|stop>` — dsh web 配套管理.

    status: 3080 + CT 端口 + node 进程 + client.js URL 同步状态
    open:   自动开 Chrome --app 窗口（探测已有窗口则前置）
    sync:   cordis.patch.yml → ~/.dsh/profiles/web/cordis.patch.yml
    restart: stop + start_component('dsh', ('web',))  (与 `ikaros restart dsh` 等价)
    stop:   杀 dsh web 进程
    """
    sub = (args[0].lower() if args else "status")
    if sub in ("-h", "--help", "help"):
        print("ikaros dsh <status|open|sync|restart|stop>")
        return 0
    if sub == "status":
        return _dsh_status(root)
    if sub == "open":
        return _dsh_open(root)
    if sub == "sync":
        return _dsh_sync(root)
    if sub == "restart":
        stop_component(root, "dsh")
        return start_component(root, "dsh", ("web",))
    if sub == "stop":
        return stop_component(root, "dsh")
    return _usage_error("ikaros dsh <status|open|sync|restart|stop>")


def _dsh_status(root: Path) -> int:
    """一屏看清 dsh 状态: 3080 监听、CT 端口、node 进程、client.js URL 同步."""
    import json
    web_port = int(os.environ.get("IKAROS_DSH_WEB_PORT") or 3080)
    # 1) 3080 监听
    listening = _port_listening(web_port)
    print(f"[1] :{web_port} dsh web  -> {'OK' if listening else 'down'}")
    # 2) CT 端口文件
    port_file = root / "tmp" / "ct-port.json"
    ct_port = None
    if port_file.is_file():
        try:
            ct_port = json.loads(port_file.read_text(encoding="utf-8")).get("port")
        except Exception:  # noqa: BLE001
            pass
    print(f"[2] CT port file     -> {ct_port or 'N/A'}  ({port_file})")
    # 3) node 进程
    pids = _dsh_pids()
    if pids:
        print(f"[3] dsh node pids   -> {', '.join(str(p) for p in pids)}")
    else:
        print("[3] dsh node pids   -> none")
    # 4) client.js URL 同步
    client_js = (
        Path.home() / ".dsh" / "profiles" / "web" / "node_modules"
        / "@ikaros" / "dsh-conversation-tree" / "dist" / "client.js"
    )
    url_in_client = None
    if client_js.is_file():
        m = re.search(r"http://127\.0\.0\.1:(\d+)/", client_js.read_text(encoding="utf-8", errors="ignore"))
        if m:
            url_in_client = int(m.group(1))
    sync_ok = url_in_client == ct_port
    print(f"[4] client.js URL   -> {url_in_client}  (sync={'OK' if sync_ok else 'MISMATCH'})")
    # 5) CT HTTP
    if ct_port:
        code = _http_code(ct_port)
        print(f"[5] :{ct_port} CT      -> HTTP {code}")
    return 0 if (listening and sync_ok) else 1


def _dsh_open(root: Path) -> int:
    """开 Chrome --app=http://localhost:3080/ 窗口; 3080 未监听则自动拉起 dsh."""
    import json
    web_port = int(os.environ.get("IKAROS_DSH_WEB_PORT") or 3080)
    if not _port_listening(web_port):
        print(f"[dsh-open] :{web_port} 未监听, 自动拉起 dsh web ...")
        rc = start_component(root, "dsh", ("web",))
        if rc != 0:
            print(f"[dsh-open] 自动拉起失败 (rc={rc}), 放弃开窗")
            return rc
        # 等待端口起来 (最多 30s)
        for _ in range(30):
            if _port_listening(web_port):
                break
            import time as _t; _t.sleep(1)
        else:
            print(f"[dsh-open] :{web_port} 仍未监听, 放弃开窗")
            return 1
        print(f"[dsh-open] :{web_port} 已起来")
    # 探测 Chrome 路径
    candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
    ]
    chrome = next((p for p in candidates if p.is_file()), None)
    if not chrome:
        print("[dsh-open] Chrome 未找到, 请手动打开 http://localhost:3080/")
        return 1
    url = f"http://localhost:{web_port}/"
    # 查 dsh 实际端口（CT 同步用，但浏览器只看 dsh）
    port_file = root / "tmp" / "ct-port.json"
    if port_file.is_file():
        try:
            json.loads(port_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    # 优先前置已有窗口, 否则新建
    if os.name == "nt":
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Start-Process -FilePath '{chrome}' -ArgumentList '--app={url}','--window-size=1400,900'"],
            check=False,
        )
    else:
        subprocess.Popen([str(chrome), f"--app={url}", "--window-size=1400,900"])
    print(f"[dsh-open] opened {url}")
    return 0


def _dsh_sync(root: Path) -> int:
    """Sync cordis.patch.yml -> ~/.dsh/profiles/web/cordis.patch.yml (only entry point for this operation)."""
    src = root / "core" / "ikaros-dsh" / "cordis.patch.yml"
    dst_dir = Path.home() / ".dsh" / "profiles" / "web"
    dst = dst_dir / "cordis.patch.yml"
    if not src.is_file():
        print(f"[dsh-sync] source not found: {src}")
        return 1
    dst_dir.mkdir(parents=True, exist_ok=True)
    # shutil.copy2 保留 mtime, 便于 debug
    import shutil
    shutil.copy2(src, dst)
    print(f"[dsh-sync] OK -> {dst}")
    return 0


def _port_listening(port: int) -> bool:
    """跨平台探测端口是否在 listen."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            return s.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            return False


def _dsh_pids() -> list[int]:
    """返回正在运行 dsh web 的 node 进程 PID 列表 (仅 Windows)."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'dsh.*bin\\.js.*web' } | Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
        return [int(line.strip()) for line in out.splitlines() if line.strip().isdigit()]
    except Exception:  # noqa: BLE001
        return []


def _http_code(port: int) -> int:
    """HTTP GET 拿状态码, 失败返回 0."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
            return r.status
    except Exception:  # noqa: BLE001
        return 0


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
    # P1 fix (2026-09-05): --help / -h 走 usage, 之前 raise LauncherError
    # 让人以为"unknown subcommand: --help" 是 bug. 用户问过"ikaros.bat 缺 stop/restart"
    # 根因是 usage 不可见, 不是真的缺.
    if not argv or argv[0] in ("--help", "-h"):
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
    if command == "dsh":
        return _cmd_dsh(root, args)
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
            "dsh",
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
