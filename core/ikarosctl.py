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
WORKER_DEFAULT_ARGS = {
    "herdr": ("--model", "go-deepseek/deepseek-v4-pro"),
    "omp": ("--model", "go-deepseek/deepseek-v4-pro"),
}
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
        overlay = root / "core" / "ikaros-dsh" / "cordis.patch.yml"
        web_port = os.environ.get("IKAROS_DSH_WEB_PORT") or str(component.port or 3080)
        argv = [
            str(node), str(dsh_bin), args[0],
            "--port", web_port, "--patch", str(overlay),
        ]
        # headless 接受 task 字符串作为后续 arg
        if len(args) > 1:
            argv.extend(list(args[1:]))

    env = os.environ.copy()
    env["IKAROS_ROOT"] = str(root)
    if component_id in {"dsh", "herdr", "omp"}:
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
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(argv, **popen_kwargs)
    except OSError as exc:
        raise LauncherError(
            f"failed to start {component_id!r} with {argv!r}: {exc}"
        ) from exc

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
        "runtime/herdr/herdr.exe",
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
        print(
            f"{component.id} | {state} | "
            f"port={port}; pid={pid_text}; marker={component.process_marker}"
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
    """Start registered web-stack components in dependency order."""
    components = _topological_components(root)
    started: list[str] = []
    failed: list[str] = []
    for component in components:
        if component.id not in START_COMPONENTS:
            continue
        try:
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
        "(herdr is opt-in: ikaros herdr)"
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


def _cmd_logs(root: Path, component_id: str) -> int:
    component = _component_or_raise(root, component_id)
    candidates = [
        root / "data" / "logs" / f"{component.id}.out.log",
        Path.home() / ".dsh" / "ikaros-dsh-web.out.log",
    ]
    for path in candidates:
        if path.is_file():
            print(f"==> {path} (last 30 lines) <==")
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-30:]:
                print(line)
            return 0
    print(f"[ikaros] no log found for {component.id}")
    return 0


def _cmd_update(root: Path) -> int:
    print(
        "[ikaros] update is reserved for a later launcher version; "
        f"no git fetch was performed ({root})"
    )
    return 0


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
    if command == "status":
        return status(root)
    if command == "ps":
        return _cmd_ps(root)
    if command == "logs":
        if not args:
            return _usage_error("ikaros logs <component>")
        return _cmd_logs(root, args[0])
    if command == "stop":
        if not args:
            return _usage_error("ikaros stop <component>")
        return stop_component(root, args[0])
    if command == "herdr":
        return start_component(root, "herdr")
    if command == "omp":
        return start_component(root, "herdr")
    if command == "restart":
        if not args:
            return _usage_error("ikaros restart <component>")
        component_id = args[0]
        stop_component(root, component_id)
        return start_component(root, component_id)
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
            "update",
            "status",
            "ps",
            "logs",
            "stop",
            "herdr",
            "omp",
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
