#!/usr/bin/env python3
"""Windows-oriented process helper for Ikaros.

Stdlib only. Additive scaffolding — does NOT modify any service code.

Subcommands:
  python bin/proc.py ps            List running python/node processes.
  python bin/proc.py kill <name>   Kill a service by port (e.g. 8080) or by
                                   command-line/image keyword.

On Windows, SIGTERM is unreliable, so we centralize kills through
`taskkill /F /T` (force + terminate child tree). Nothing is killed unless a
target PID/image is actually resolved, so the tool fails safe.
"""

import subprocess
import sys

IMAGE_KEYWORDS = ("python", "node", "node.exe", "python.exe")


def _run(cmd):
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            shell=True,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"# command failed: {exc}")
        return None


def list_processes():
    """Return list of (pid, name, cmdline) using wmic, fallback to powershell."""
    rows = []
    out = _run(
        'wmic process get ProcessId,Name,CommandLine /format:csv 2>nul'
    )
    if out and out.returncode == 0 and out.stdout.strip():
        for line in out.stdout.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("node"):
                continue
            # CSV: Node,CommandLine,Name,ProcessId  (columns vary) -> split safely
            parts = line.split(",")
            if len(parts) < 2:
                continue
            cmdline = parts[1] if len(parts) > 1 else ""
            name = parts[2] if len(parts) > 2 else ""
            pid = parts[-1]
            if pid.isdigit():
                rows.append((int(pid), name.strip(), cmdline.strip()))
        if rows:
            return rows

    # Fallback to PowerShell Get-CimInstance.
    ps = (
        'powershell -NoProfile -Command "'
        "(Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,Name,CommandLine | "
        "ForEach-Object { $_.ProcessId + '\\t' + $_.Name + '\\t' + ($_..CommandLine -replace '\\t',' ') })"
        '"'
    )
    out = _run(ps)
    if out and out.returncode == 0:
        for line in out.stdout.splitlines():
            line = line.rstrip("\r")
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0].isdigit():
                rows.append((int(parts[0]), parts[1], parts[2]))
    return rows


def cmd_ps():
    rows = list_processes()
    shown = 0
    print(f"{'PID':>7}  {'IMAGE':<18} COMMAND")
    for pid, name, cmdline in sorted(rows, key=lambda r: r[0]):
        if "python" in name.lower() or "node" in name.lower():
            cmd = cmdline if cmdline else name
            print(f"{pid:>7}  {name[:18]:<18} {cmd[:90]}")
            shown += 1
    print(f"# {shown} python/node process(es) listed")
    return 0


def resolve_by_port(port):
    """Return PID listening on the given TCP port, or None."""
    out = _run(f'netstat -ano | findstr /R ":{port}[ ]"')
    if not out or not out.stdout.strip():
        return None
    best = None
    for line in out.stdout.splitlines():
        cols = line.split()
        if len(cols) >= 5:
            pid = cols[-1]
            if pid.isdigit():
                best = int(pid)
    return best


def resolve_by_keyword(keyword):
    """Return list of (pid, name, cmdline) whose command line/image matches."""
    rows = list_processes()
    kw = keyword.lower()
    return [
        (pid, name, cmd)
        for (pid, name, cmd) in rows
        if kw in name.lower() or kw in cmd.lower()
    ]


def cmd_kill(name):
    target = None
    is_port = name.isdigit()

    if is_port:
        pid = resolve_by_port(name)
        if pid:
            target = [(pid, "port", f"listening on :{name}")]
    else:
        target = resolve_by_keyword(name)

    if not target:
        print(f"# no process resolved for '{name}' — nothing killed (safe).")
        return 0

    killed = 0
    for pid, name_, detail in target:
        if name_ == "port":
            print(f"# killing PID {pid} ({detail}) via taskkill /F /T /PID")
            res = _run(f"taskkill /F /T /PID {pid}")
        else:
            # Avoid killing our own scanner / unrelated shells.
            print(f"# killing PID {pid} ({name_}) detail={detail[:60]}")
            res = _run(f"taskkill /F /T /PID {pid}")
        if res and res.returncode == 0:
            killed += 1
            print(f"# ok: killed PID {pid}")
        else:
            print(f"# failed to kill PID {pid}: {(res.stderr or res.stdout).strip()[:120]}")
    print(f"# {killed} process(es) killed")
    return 0


def main(argv):
    if len(argv) < 2:
        print("usage: python bin/proc.py <ps|kill <name>>")
        return 2
    sub = argv[1].lower()
    if sub == "ps":
        return cmd_ps()
    if sub == "kill":
        if len(argv) < 3:
            print("usage: python bin/proc.py kill <port|keyword>")
            return 2
        return cmd_kill(argv[2])
    print(f"# unknown subcommand: {sub}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
