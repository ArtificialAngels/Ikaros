#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ikaros Bug Collector — 启动诊断 & Bug 回收进程
===============================================

运行 ikaros-start.bat，收集启动过程中遇到的所有异常：
  1. 捕获 ikaros-start.bat 的 stdout/stderr
  2. 扫描各模块日志（data/logs/*.log, *.err）中的错误/警告
  3. 检测服务端口健康状态
  4. 生成结构化 JSON 报告

Usage:
    python bin/ikaros-bug-collect.py                  # 完整启动 + 收集
    python bin/ikaros-bug-collect.py --no-start       # 只收集（不启动，假设已运行）
    python bin/ikaros-bug-collect.py --report-only     # 只生成报告（从已有日志）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============================================================
# Paths
# ============================================================
HERE = Path(__file__).resolve()
ROOT = HERE.parent.parent
LOG_DIR = ROOT / "data" / "logs"
PYTHON = ROOT / "portable-python" / "python.exe"
SUPERVISOR = ROOT / "bin" / "hermes-supervisor.py"

# Services to probe (name -> port)
SERVICES = {
    "llm_engine": 8080,
    "bridge": 7860,
}

# Error patterns to scan in log files
ERROR_PATTERNS = [
    re.compile(r"(?i)\b(error|exception|traceback|fatal|critical|fail(?:ed|ure)?)\b"),
    re.compile(r"(?i)\b(panic|abort|cannot|unable to|refused|denied|timeout)\b"),
    re.compile(r"(?i)\b(warning|warn)\b"),
    re.compile(r"(?i)E\d{4}"),  # Windows error codes
    re.compile(r"OSError|IOError|PermissionError|FileNotFoundError"),
    re.compile(r"ModuleNotFoundError|ImportError"),
    re.compile(r"ConnectionRefusedError|ConnectionResetError"),
    re.compile(r"socket\.timeout|timed out"),
    re.compile(r"exit code [1-9]"),  # non-zero exit codes
    re.compile(r"rc=[1-9]\d*"),  # non-zero return codes (not rc=0)
]

WARNING_PATTERNS = [
    re.compile(r"(?i)\b(warn|warning|deprecated|obsolete|stale)\b"),
    re.compile(r"(?i)\bretry\b"),
    re.compile(r"(?i)\bfallback\b"),
]

# Files to skip (binary / too large / not relevant)
SKIP_FILES = {
    "ikaros-monitor.jsonl",
    "icarus-heartbeat.jsonl",
    "ikaros-heartbeat.jsonl",
    "icarus-prompter.log",
    "icarus-pet.log",
    "ikaros-pet.log",
    "tts-debug-last.pcm",
    "tts-debug-last.mp3",
    "tts-direct-test.mp3",
    "patch3_debug.log",
    "voice_worker.log",
    "bridge-rs.patience.log",
    "bridge-rs.patience.err",
    "bridge-rs.c1.log",
    "bridge-rs-test-out.log",
    "bridge-rs-test-err.log",
    "supervisor-state.json",  # state file, not a log
    "cuda-active.json",  # config
    "model-registry.json",  # config
    "router-preset-abs.ini",  # config
    "bridge-last-launch.json",  # metadata
    "llm-engine-last-launch.json",  # metadata
    "voice-last-model.json",  # metadata
    "telemetry.json",  # telemetry data
    "activity-log.2026-06-28.txt",  # old activity log
}

# Max file size to scan (bytes)
MAX_SCAN_SIZE = 2 * 1024 * 1024  # 2 MB


# ============================================================
# Data model
# ============================================================

class BugReport:
    """Structured bug report collector."""

    def __init__(self):
        self.timestamp = datetime.now().isoformat()
        self.startup_output: List[str] = []
        self.startup_errors: List[str] = []
        self.service_status: Dict[str, Dict[str, Any]] = {}
        self.log_issues: List[Dict[str, Any]] = []
        self.supervisor_state: Dict[str, Any] = {}
        self.summary: Dict[str, int] = {
            "critical": 0,
            "error": 0,
            "warning": 0,
            "info": 0,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "startup_output": self.startup_output,
            "startup_errors": self.startup_errors,
            "service_status": self.service_status,
            "log_issues": self.log_issues,
            "supervisor_state": self.supervisor_state,
            "summary": self.summary,
        }


# ============================================================
# Collectors
# ============================================================

def check_port(host: str, port: int, timeout: float = 1.0) -> bool:
    """TCP probe: is the port listening?"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def check_http(host: str, port: int, endpoint: str = "/health", timeout: float = 3.0) -> Optional[int]:
    """HTTP probe: return status code or None."""
    import http.client
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", endpoint)
        resp = conn.getresponse()
        conn.close()
        return resp.status
    except Exception:
        return None


def collect_startup(report: BugReport, do_start: bool = True) -> None:
    """Run ikaros-start.bat and capture output."""
    if not do_start:
        report.startup_output.append("[SKIP] --no-start flag, skipping startup")
        return

    bat = ROOT / "bin" / "ikaros-start.bat"
    if not bat.is_file():
        report.startup_errors.append(f"FATAL: {bat} not found")
        return

    print(f"[bug-collect] Running {bat.name}...")
    print(f"[bug-collect] This may take 30-120 seconds...")

    try:
        proc = subprocess.run(
            ["cmd", "/c", str(bat)],
            capture_output=True,
            text=True,
            timeout=300,  # 5 min max
            cwd=str(ROOT),
            encoding="utf-8",
            errors="replace",
        )
        report.startup_output = proc.stdout.splitlines()
        if proc.stderr:
            report.startup_output.extend(
                f"[STDERR] {line}" for line in proc.stderr.splitlines()
            )
        if proc.returncode != 0:
            report.startup_errors.append(
                f"ikaros-start.bat exited with code {proc.returncode}"
            )
    except subprocess.TimeoutExpired:
        report.startup_errors.append("ikaros-start.bat timed out (>300s)")
    except Exception as e:
        report.startup_errors.append(f"Failed to run ikaros-start.bat: {e}")


def collect_service_health(report: BugReport) -> None:
    """Check each service's port and HTTP health."""
    print("[bug-collect] Checking service health...")
    for name, port in SERVICES.items():
        tcp_ok = check_port("127.0.0.1", port, timeout=2.0)
        http_code = check_http("127.0.0.1", port) if tcp_ok else None

        status: Dict[str, Any] = {
            "port": port,
            "tcp": tcp_ok,
            "http_status": http_code,
        }

        if not tcp_ok:
            status["issue"] = "CRITICAL: port not listening"
            report.summary["critical"] += 1
        elif http_code is None:
            status["issue"] = "ERROR: TCP ok but HTTP health failed"
            report.summary["error"] += 1
        elif http_code != 200:
            status["issue"] = f"WARNING: HTTP {http_code} (expected 200)"
            report.summary["warning"] += 1
        else:
            status["issue"] = "OK"

        report.service_status[name] = status


def collect_supervisor_state(report: BugReport) -> None:
    """Read supervisor-state.json for last start results."""
    state_file = LOG_DIR / "supervisor-state.json"
    if state_file.is_file():
        try:
            report.supervisor_state = json.loads(
                state_file.read_text(encoding="utf-8")
            )
            failed = report.supervisor_state.get("failed", [])
            if failed:
                for f in failed:
                    report.startup_errors.append(f"Supervisor failed to start: {f}")
                    report.summary["critical"] += 1
        except Exception as e:
            report.supervisor_state = {"error": str(e)}


def classify_line(line: str) -> Optional[str]:
    """Classify a log line as error/warning/info or None."""
    for pat in ERROR_PATTERNS:
        if pat.search(line):
            # Check if it's just a warning pattern that also matches error
            for wpat in WARNING_PATTERNS:
                if wpat.search(line) and not any(
                    ep.search(line) for ep in ERROR_PATTERNS[:3]  # error/exception/traceback/fatal
                ):
                    return "warning"
            return "error"
    for pat in WARNING_PATTERNS:
        if pat.search(line):
            return "warning"
    return None


def collect_log_issues(report: BugReport) -> None:
    """Scan all log files for errors and warnings."""
    print("[bug-collect] Scanning log files...")
    if not LOG_DIR.is_dir():
        return

    # Only scan recent files (modified in last 24 hours)
    cutoff = time.time() - 86400

    for f in sorted(LOG_DIR.iterdir()):
        if not f.is_file():
            continue
        if f.name in SKIP_FILES:
            continue
        if f.name.startswith("bug-report-"):
            continue  # skip previous bug reports
        if f.suffix not in (".log", ".err", ".txt"):
            continue

        try:
            stat = f.stat()
            if stat.st_mtime < cutoff:
                continue
            if stat.st_size > MAX_SCAN_SIZE:
                report.log_issues.append({
                    "file": f.name,
                    "severity": "info",
                    "message": f"Skipped (too large: {stat.st_size // 1024} KB)",
                })
                continue
            if stat.st_size == 0:
                continue
        except OSError:
            continue

        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        lines = content.splitlines()
        errors_found = 0
        warnings_found = 0
        sample_errors: List[str] = []
        sample_warnings: List[str] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            cls = classify_line(line)
            if cls == "error":
                errors_found += 1
                if len(sample_errors) < 5:
                    sample_errors.append(line[:200])
            elif cls == "warning":
                warnings_found += 1
                if len(sample_warnings) < 3:
                    sample_warnings.append(line[:200])

        if errors_found > 0:
            report.log_issues.append({
                "file": f.name,
                "severity": "error",
                "count": errors_found,
                "samples": sample_errors,
            })
            report.summary["error"] += errors_found

        if warnings_found > 0:
            report.log_issues.append({
                "file": f.name,
                "severity": "warning",
                "count": warnings_found,
                "samples": sample_warnings,
            })
            report.summary["warning"] += warnings_found


def collect_zombie_processes(report: BugReport) -> None:
    """Detect zombie/duplicate processes (e.g. multiple llama-server on same port)."""
    print("[bug-collect] Checking for zombie processes...")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-WmiObject Win32_Process | "
             "Where-Object { $_.Name -match 'llama-server|hermes-bridge|node' } | "
             "Select-Object ProcessId, ParentProcessId, Name, CommandLine | "
             "ConvertTo-Json -Depth 2"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return

        procs = json.loads(result.stdout)
        if isinstance(procs, dict):
            procs = [procs]

        # Group by name
        by_name: Dict[str, List[Dict]] = {}
        for p in procs:
            name = p.get("Name", "unknown")
            by_name.setdefault(name, []).append(p)

        # Check for duplicates (potential zombies)
        for name, plist in by_name.items():
            if len(plist) > 1:
                pids = [str(p.get("ProcessId", "?")) for p in plist]
                report.log_issues.append({
                    "file": "(zombie check)",
                    "severity": "warning",
                    "message": f"Multiple {name} processes: PIDs {', '.join(pids)}",
                    "samples": [
                        f"PID {p.get('ProcessId')} (parent={p.get('ParentProcessId')}): "
                        f"{(p.get('CommandLine') or '')[:100]}"
                        for p in plist
                    ],
                })
                report.summary["warning"] += 1

    except Exception as e:
        report.log_issues.append({
            "file": "(zombie check)",
            "severity": "info",
            "message": f"Could not check: {e}",
        })


def collect_process_info(report: BugReport) -> None:
    """Check for zombie/orphan processes."""
    print("[bug-collect] Checking process list...")
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        relevant = []
        for line in result.stdout.splitlines():
            lower = line.lower()
            if any(kw in lower for kw in [
                "llama-server", "python", "node", "hermes", "ikaros",
                "bridge", "webui", "supervisor", "watchdog"
            ]):
                relevant.append(line.strip())

        if relevant:
            report.log_issues.append({
                "file": "(process list)",
                "severity": "info",
                "message": f"{len(relevant)} relevant processes found",
                "samples": relevant[:20],
            })
            report.summary["info"] += 1
    except Exception:
        pass


# ============================================================
# Report generation
# ============================================================

def generate_report(report: BugReport) -> Path:
    """Write the bug report to a JSON file."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = ROOT / "data" / "logs" / f"bug-report-{ts}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    data = report.to_dict()

    # Add a human-readable summary at the top
    summary_lines = [
        f"Ikaros Bug Report — {report.timestamp}",
        "=" * 60,
        "",
        f"  Critical: {report.summary['critical']}",
        f"  Errors:   {report.summary['error']}",
        f"  Warnings: {report.summary['warning']}",
        f"  Info:     {report.summary['info']}",
        "",
    ]

    if report.startup_errors:
        summary_lines.append("  Startup Errors:")
        for e in report.startup_errors:
            summary_lines.append(f"    - {e}")
        summary_lines.append("")

    if report.service_status:
        summary_lines.append("  Service Status:")
        for name, status in report.service_status.items():
            tag = "UP" if status.get("tcp") else "DOWN"
            summary_lines.append(f"    {name:15} :{status['port']:5}  [{tag}]  {status.get('issue', '')}")
        summary_lines.append("")

    if report.log_issues:
        summary_lines.append("  Log Issues (top entries):")
        for issue in sorted(report.log_issues, key=lambda x: x.get("count", 0), reverse=True)[:15]:
            sev = issue["severity"].upper()
            cnt = issue.get("count", "")
            summary_lines.append(f"    [{sev:7}] {issue['file']} ({cnt})")
            for s in issue.get("samples", [])[:2]:
                summary_lines.append(f"            {s[:100]}")
        summary_lines.append("")

    # Write text summary + JSON
    text_summary = "\n".join(summary_lines)

    # Prepend text summary to JSON as a comment-like field
    data["_summary_text"] = text_summary

    report_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return report_path


def print_summary(report: BugReport) -> None:
    """Print a human-readable summary to stdout."""
    print()
    print("=" * 60)
    print(f"  IKAROS BUG REPORT — {report.timestamp}")
    print("=" * 60)
    print()
    print(f"  Critical: {report.summary['critical']}")
    print(f"  Errors:   {report.summary['error']}")
    print(f"  Warnings: {report.summary['warning']}")
    print(f"  Info:     {report.summary['info']}")
    print()

    if report.startup_errors:
        print("  [STARTUP ERRORS]")
        for e in report.startup_errors:
            print(f"    ! {e}")
        print()

    if report.service_status:
        print("  [SERVICE STATUS]")
        for name, status in report.service_status.items():
            tcp = "UP" if status.get("tcp") else "DOWN"
            color = ""
            if not status.get("tcp"):
                color = "[CRITICAL] "
            elif status.get("http_status") != 200:
                color = "[WARN] "
            print(f"    {color}{name:15} :{status['port']:5}  [{tcp}]")
        print()

    if report.log_issues:
        print("  [TOP LOG ISSUES]")
        for issue in sorted(report.log_issues, key=lambda x: x.get("count", 0), reverse=True)[:10]:
            sev = issue["severity"].upper()
            cnt = issue.get("count", "")
            print(f"    [{sev:7}] {issue['file']} ({cnt})")
            for s in issue.get("samples", [])[:1]:
                print(f"            {s[:120]}")
        print()

    print("=" * 60)


# ============================================================
# Main
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ikaros Bug Collector — 启动诊断 & Bug 回收",
    )
    parser.add_argument(
        "--no-start", action="store_true",
        help="Don't run ikaros-start.bat (assume services already running)",
    )
    parser.add_argument(
        "--report-only", action="store_true",
        help="Only scan existing logs, don't start services",
    )
    args = parser.parse_args()

    report = BugReport()

    # Phase 1: Startup
    if not args.report_only:
        collect_startup(report, do_start=not args.no_start)
        # Give services a moment to stabilize
        if not args.no_start:
            print("[bug-collect] Waiting 5s for services to stabilize...")
            time.sleep(5)

    # Phase 2: Health checks
    collect_service_health(report)
    collect_supervisor_state(report)

    # Phase 3: Log scanning
    collect_log_issues(report)

    # Phase 4: Process check
    collect_process_info(report)

    # Phase 4b: Zombie process detection
    collect_zombie_processes(report)

    # Phase 5: Generate report
    report_path = generate_report(report)
    print_summary(report)
    print(f"\n  Report saved to: {report_path}")
    print()

    # Return non-zero if critical issues found
    if report.summary["critical"] > 0:
        return 2
    if report.summary["error"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[bug-collect] Interrupted.")
        sys.exit(130)
