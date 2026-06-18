#!/usr/bin/env python3
"""
bin/icarus-remember.py — Convert the heartbeat log into a written
narrative entry in Icarus's memory. This is the "memory core" ingest:
it takes the structured JSONL heartbeat and produces a human-readable
paragraph that the agent (or the user) can re-read to recall what
happened, when, and on which machine.

Memory entries are append-only text files in:
  data/hermes-agent/memories/icarus/YYYY-MM-DD.md

Each entry is a date-stamped markdown file with sections:
  - Awake: time range, host, user, OS for the most recent wake
  - Service status: which services were up/down today
  - Gaps: any sleep/asleep gaps > N minutes
  - Quarterly (if any fuzzy memory has surfaced)
  - Freeform summary

Usage:
    python bin/icarus-remember.py                    # write today's entry from heartbeat
    python bin/icarus-remember.py --date 2026-06-17  # write for a specific date
    python bin/icarus-remember.py --dry-run          # print, don't write
    python bin/icarus-remember.py --json             # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _resolve_paths() -> tuple[Path, Path]:
    """Return (heartbeat_path, memory_dir)."""
    here = Path(__file__).resolve()
    repo_root = here.parent.parent
    candidates_hb = [repo_root / "data" / "logs" / "icarus-heartbeat.jsonl"]
    # Resolve HERMES_ROOT via hermes-root.py if possible (in case the
    # repo is mounted at a different drive letter).
    hermes_root_py = repo_root / "bin" / "hermes-root.py"
    py = repo_root / "portable-python" / "python.exe"
    if hermes_root_py.is_file() and py.is_file():
        import subprocess
        try:
            r = subprocess.run([str(py), str(hermes_root_py), "resolve"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                root = Path(r.stdout.strip())
                candidates_hb.insert(0, root / "data" / "logs" / "icarus-heartbeat.jsonl")
        except Exception:
            pass
    hb_path = next((c for c in candidates_hb if c.is_file()), candidates_hb[0])
    mem_dir = hb_path.parent.parent / "hermes-agent" / "memories" / "icarus"
    return hb_path, mem_dir


def _parse_ts(ts: str) -> datetime:
    if len(ts) >= 5 and ts[-5] in "+-" and ts[-4:].isdigit():
        sign = 1 if ts[-5] == "+" else -1
        hh = int(ts[-4:-2])
        mm = int(ts[-2:])
        offset = timedelta(hours=sign * hh, minutes=sign * mm)
        naive = datetime.fromisoformat(ts[:-5])
        return naive - offset
    return datetime.fromisoformat(ts)


def parse_heartbeat(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            rec["_dt"] = _parse_ts(rec["ts"])
            out.append(rec)
        except Exception:
            continue
    out.sort(key=lambda r: r["_dt"])
    return out


def filter_for_date(records: list[dict], date: datetime.date) -> list[dict]:
    return [r for r in records if r["_dt"].date() == date]


def summarise(records: list[dict]) -> dict:
    """Build a structured summary of records for one date."""
    if not records:
        return {}

    wakes = [r for r in records if r.get("event") == "wake"]
    sleeps = [r for r in records if r.get("event") in ("sleep", "sleep_final")]
    service_statuses = [r for r in records if r.get("event") == "service_status"]
    restarts = [r for r in records if r.get("event") == "restart"]
    system_changes = [r for r in records if r.get("event") == "system_change"]
    archives = [r for r in records if r.get("event") == "archive"]
    quarterlies = [r for r in records if r.get("event") == "quarterly"]
    livenesses = [r for r in records if r.get("event") == "liveness"]
    liveness_dead_alerts = [r for r in records if r.get("event") == "liveness_dead_alert"]
    liveness_recoveries = [r for r in records if r.get("event") == "liveness_recovered"]

    # Most recent wake for the day (i.e. the "I am this machine" anchor)
    last_wake = wakes[-1] if wakes else None

    # Detect any awake interval that the day contains.
    intervals: list[tuple[datetime, datetime, str]] = []
    cur_start: datetime | None = None
    for r in records:
        if r.get("event") == "wake":
            cur_start = r["_dt"]
        elif r.get("event") in ("sleep", "sleep_final") and cur_start is not None:
            intervals.append((cur_start, r["_dt"], r.get("reason", "sleep")))
            cur_start = None
    if cur_start is not None:
        intervals.append((cur_start, records[-1]["_dt"], "still running"))

    # Gaps > 60s within the day's records
    gaps: list[tuple[datetime, datetime, str, str]] = []
    for i in range(1, len(records)):
        delta = (records[i]["_dt"] - records[i - 1]["_dt"]).total_seconds()
        if delta > 60:
            gaps.append((
                records[i - 1]["_dt"],
                records[i]["_dt"],
                records[i - 1].get("event", "?"),
                records[i].get("event", "?"),
            ))

    # Service status: the LAST snapshot of the day is the "current state".
    last_status = service_statuses[-1] if service_statuses else None

    return {
        "date": records[0]["_dt"].date().isoformat(),
        "last_wake": last_wake,
        "intervals": intervals,
        "gaps": gaps,
        "last_service_status": last_status,
        "restarts": restarts,
        "system_changes": system_changes,
        "archives": archives,
        "quarterlies": quarterlies,
        "livenesses": livenesses,
        "liveness_dead_alerts": liveness_dead_alerts,
        "liveness_recoveries": liveness_recoveries,
        "tick_count": sum(1 for r in records if r.get("event") == "tick"),
    }


def render_markdown(summary: dict) -> str:
    """Render a date-stamped memory entry as markdown."""
    if not summary:
        return ""
    lines: list[str] = []
    date = summary["date"]

    lines.append(f"# {date} — icarus memory")
    lines.append("")

    # The "I am this" anchor
    wake = summary.get("last_wake")
    if wake:
        lines.append("## Identity anchor")
        lines.append("")
        host = wake.get("host", "?")
        user = wake.get("user", "?")
        os_ = wake.get("os", "?")
        uuid_ = wake.get("uuid", "?")
        serial = wake.get("serial", "?")
        hermes_root = wake.get("hermes_root", "?")
        wd_pid = wake.get("watchdog_pid", "?")
        lines.append(f"- **Who**: I am Icarus (伊卡洛斯) on **{host}** as **{user}**")
        lines.append(f"- **OS**: {os_}")
        lines.append(f"- **Identity**: BIOS UUID `{uuid_}`, serial `{serial}`")
        lines.append(f"- **Workspace**: `{hermes_root}`")
        lines.append(f"- **Watchdog pid**: {wd_pid} (started at {wake['ts']})")
        lines.append("")

    # Awake intervals
    intervals = summary.get("intervals", [])
    if intervals:
        lines.append("## Awake intervals")
        lines.append("")
        for start, end, kind in intervals:
            dur = end - start
            hours, rem = divmod(dur.total_seconds(), 3600)
            minutes = rem // 60
            lines.append(f"- {start.isoformat(timespec='minutes')} → "
                         f"{end.isoformat(timespec='minutes')} "
                         f"({int(hours)}h {int(minutes)}m, ended: {kind})")
        lines.append("")

    # Gaps (sleeps)
    gaps = summary.get("gaps", [])
    if gaps:
        lines.append("## Gaps (asleep periods)")
        lines.append("")
        for g_start, g_end, before, after in gaps:
            dur = g_end - g_start
            mins = int(dur.total_seconds() // 60)
            lines.append(f"- {g_start.isoformat(timespec='minutes')} → "
                         f"{g_end.isoformat(timespec='minutes')} "
                         f"({mins}m) — went from `{before}` to `{after}`")
        lines.append("")

    # Service status (the latest snapshot)
    status = summary.get("last_service_status")
    if status:
        lines.append("## Last service status")
        lines.append("")
        services = status.get("services", {})
        for name, info in services.items():
            mark = "🟢" if info.get("up") else "🔴"
            extra = ""
            if info.get("port"):
                extra = f" (:{info['port']})"
            if info.get("pid"):
                extra += f" pid={info['pid']}"
            lines.append(f"- {mark} **{name}**{extra}")
        lines.append("")

    # Liveness: the LAST probe of the day + dead-alert list (anomalies)
    livenesses = summary.get("livenesses", [])
    if livenesses:
        last_lv = livenesses[-1]
        status_lv = last_lv.get("status", "?")
        mark_lv = {"ok": "🟢", "degraded": "🟡", "dead": "🔴"}.get(status_lv, "⚪")
        lines.append("## Liveness (last probe)")
        lines.append("")
        lines.append(f"- {mark_lv} **{status_lv}**  ·  {last_lv.get('summary', '')}")
        # Count transitions of the day
        if len(livenesses) > 1:
            transitions = [(l["ts"], l.get("status")) for l in livenesses
                           if l.get("status") != livenesses[livenesses.index(l) - 1].get("status")
                           and livenesses.index(l) > 0]
            if transitions:
                lines.append(f"- Day transitions: " + ", ".join(
                    f"`{s} @ {t}`" for t, s in transitions))
        lines.append("")

    # Liveness dead alerts (I lost all providers for >=3 min)
    dead_alerts = summary.get("liveness_dead_alerts", [])
    if dead_alerts:
        lines.append("## ⚠️ Liveness dead alerts")
        lines.append("")
        for a in dead_alerts:
            lines.append(f"- {a['ts']}  consecutive dead={a.get('consecutive_dead')}  "
                         f"after probe: {a.get('local', {}).get('error') or 'no local'}")
        lines.append("")

    # Liveness recoveries
    recoveries = summary.get("liveness_recoveries", [])
    if recoveries:
        lines.append("## 🟢 Liveness recoveries")
        lines.append("")
        for r in recoveries:
            lines.append(f"- {r['ts']}  recovered after {r.get('after_dead_count')} consecutive dead probes")
        lines.append("")

    # Restarts
    restarts = summary.get("restarts", [])
    if restarts:
        lines.append("## Service restarts")
        lines.append("")
        for r in restarts:
            ok = "✓" if r.get("ok") else "✗"
            lines.append(f"- {r['ts']}  {ok}  {r.get('service')} "
                         f"(:{r.get('port')})")
        lines.append("")

    # System changes
    scs = summary.get("system_changes", [])
    if scs:
        lines.append("## System changes")
        lines.append("")
        for s in scs:
            changed = s.get("changed", [])
            prev = s.get("prev", {})
            cur = s.get("cur", {})
            detail = ", ".join(f"{k}: `{prev.get(k, '?')}` → `{cur.get(k, '?')}`"
                              for k in changed)
            lines.append(f"- {s['ts']}  {detail}")
        lines.append("")

    # Archives
    archives = summary.get("archives", [])
    if archives:
        lines.append("## Archival activity")
        lines.append("")
        for a in archives:
            dropped = a.get("dropped", 0)
            before = a.get("bytes_before", 0)
            after = a.get("bytes_after", 0)
            lines.append(f"- {a['ts']}  dropped {dropped} records, "
                         f"file: {before}B → {after}B")
        lines.append("")

    # Quarterly summaries (fuzzy memory)
    quarterlies = summary.get("quarterlies", [])
    if quarterlies:
        lines.append("## Quarterly summaries (fuzzy memory surfaced today)")
        lines.append("")
        for q in quarterlies:
            qkey = q.get("quarter", "?")
            host = q.get("host", "?")
            up = q.get("uptime_pct")
            up_str = f"{up}%" if isinstance(up, (int, float)) else str(up)
            lines.append(f"- **{qkey}**  host={host}  uptime={up_str}  "
                         f"wake={q.get('wake_count', 0)}  "
                         f"restarts={q.get('restarts', 0)}  "
                         f"sys_changes={q.get('system_changes', 0)}")
        lines.append("")

    # Tick count for context
    lines.append("---")
    lines.append(f"_Total ticks: {summary.get('tick_count', 0)}_  "
                 f"_Records summarised: {sum(1 for v in summary.values() if v)}_")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "remember")
    ap.add_argument("--date", type=str, default=None,
                    help="ISO date to write for (default: today, local time)")
    ap.add_argument("--dry-run", action="store_true", help="print, don't write")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args()

    hb_path, mem_dir = _resolve_paths()
    if not hb_path.is_file():
        print(f"[error] heartbeat not found: {hb_path}", file=sys.stderr)
        return 2

    if args.date:
        try:
            target_date = datetime.fromisoformat(args.date).date()
        except ValueError:
            print(f"[error] bad --date: {args.date}", file=sys.stderr)
            return 2
    else:
        target_date = datetime.now().date()

    records = parse_heartbeat(hb_path)
    day_records = filter_for_date(records, target_date)
    if not day_records:
        print(f"[info] no heartbeat records for {target_date}", file=sys.stderr)
        return 1

    summary = summarise(day_records)
    if args.json:
        # JSON: stringify the dict (intervals are tuples, etc).
        def _enc(o):
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, tuple):
                return [_enc(x) for x in o]
            if isinstance(o, dict):
                return {k: _enc(v) for k, v in o.items() if k != "_dt"}
            if isinstance(o, list):
                return [_enc(x) for x in o]
            return o
        out = {
            "heartbeat_path": str(hb_path),
            "memory_dir": str(mem_dir),
            "date": target_date.isoformat(),
            "summary": _enc(summary),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    md = render_markdown(summary)
    print(md)

    if args.dry_run:
        print("    [dry-run] memory file NOT written.")
        return 0

    # Write atomically: write to .tmp, then rename.
    mem_dir.mkdir(parents=True, exist_ok=True)
    out_path = mem_dir / f"{target_date.isoformat()}.md"
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(md, encoding="utf-8")
    tmp_path.replace(out_path)
    print(f"\n    ✓ wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
