#!/usr/bin/env python3
"""
bin/icarus-timeline.py — Reconstruct icarus's awake / asleep timeline
from the heartbeat log. Answers: when was I alive, when was I asleep,
when did services die, when did the machine change.

Usage:
    python bin/icarus-timeline.py
    python bin/icarus-timeline.py --since 2026-06-18T00:00
    python bin/icarus-timeline.py --gap-threshold 60  # gaps > 60s = sleep
    python bin/icarus-timeline.py --json              # machine-readable output
    python bin/icarus-timeline.py --events restart    # only show events
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Default heartbeat location: $HERMES_ROOT/data/logs/icarus-heartbeat.jsonl
# We resolve via hermes-root.py if available, else fall back to CWD-relative.
def _resolve_heartbeat() -> Path:
    here = Path(__file__).resolve()
    repo_root = here.parent.parent
    candidates = [
        repo_root / "data" / "logs" / "icarus-heartbeat.jsonl",
    ]
    # Also try hermes-root.py device-info for absolute resolution
    hermes_root_py = repo_root / "bin" / "hermes-root.py"
    py = repo_root / "portable-python" / "python.exe"
    if hermes_root_py.is_file() and py.is_file():
        import subprocess
        try:
            r = subprocess.run([str(py), str(hermes_root_py), "resolve"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                root = Path(r.stdout.strip())
                candidates.insert(0, root / "data" / "logs" / "icarus-heartbeat.jsonl")
        except Exception:
            pass
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def _parse_ts(ts: str) -> datetime:
    # Format: 2026-06-18T19:30:38+0800
    # Strip the +0800 offset and use it.
    if len(ts) >= 5 and ts[-5] in "+-" and ts[-4:].isdigit():
        sign = 1 if ts[-5] == "+" else -1
        hh = int(ts[-4:-2])
        mm = int(ts[-2:])
        offset = timedelta(hours=sign * hh, minutes=sign * mm)
        naive = datetime.fromisoformat(ts[:-5])
        return naive - offset  # normalize to UTC
    return datetime.fromisoformat(ts)


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def parse_heartbeat(path: Path) -> list[dict]:
    """Read the heartbeat JSONL into a list of dicts, sorted by ts."""
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
        except Exception as e:
            print(f"[warn] skipping bad line: {e}: {line[:80]}", file=sys.stderr)
    out.sort(key=lambda r: r["_dt"])
    return out


def reconstruct_timeline(records: list[dict], gap_threshold_s: int) -> list[dict]:
    """Convert flat records into intervals. Each interval has:
        - start: wake dt
        - end:   sleep_final dt (or "ongoing")
        - duration_s
        - events: list of events during this awake period

    'quarterly' events (added by icarus-heartbeat-archive.py for the
    fuzzy window) are bucketed separately. They represent
    pre-summarised activity from years ago and are not part of the
    active awake timeline. We return them in `quarterlies` so the
    caller can show a "memory" section.
    """
    if not records:
        return [], []

    intervals = []
    quarterlies = []  # separate list for the fuzzy window
    current = None  # the open interval dict

    for r in records:
        ev = r.get("event")
        if ev == "quarterly":
            # Synthesised summary from the archiver. Surface in a
            # separate list so timeline viewers can show it as
            # "long-term memory" rather than weaving it into the
            # active wake/sleep narrative.
            quarterlies.append({
                "quarter": r.get("quarter"),
                "host": r.get("host"),
                "uptime_pct": r.get("uptime_pct"),
                "tick_count": r.get("tick_count"),
                "wake_count": r.get("wake_count"),
                "restarts": r.get("restarts"),
                "system_changes": r.get("system_changes"),
                "first_wake": r.get("first_wake"),
                "last_sleep": r.get("last_sleep"),
                "ts": r["_dt"],
            })
            continue
        if ev == "wake":
            if current is not None:
                # Previous interval never had a sleep event — close it
                # at the last seen timestamp.
                current["end"] = current["events"][-1]["_dt"] if current["events"] else current["start"]
                current["end_kind"] = "implicit (no sleep event)"
                current["duration_s"] = int((current["end"] - current["start"]).total_seconds())
                intervals.append(current)
            current = {
                "start": r["_dt"],
                "watchdog_pid": r.get("watchdog_pid"),
                "hermes_root": r.get("hermes_root"),
                "host": r.get("host"),
                "user": r.get("user"),
                "os": r.get("os"),
                "events": [],
            }
        elif ev in ("sleep", "sleep_final"):
            if current is not None:
                current["end"] = r["_dt"]
                current["end_kind"] = r.get("reason", "sleep")
                current["duration_s"] = int((current["end"] - current["start"]).total_seconds())
                current["events"].append(r)
                intervals.append(current)
                current = None
        else:
            if current is not None:
                current["events"].append(r)
            # If current is None (record before first wake), skip — orphan.

    # If we ended with an open interval, close it.
    if current is not None:
        last_dt = current["events"][-1]["_dt"] if current["events"] else current["start"]
        current["end"] = last_dt
        current["end_kind"] = "still running"
        current["duration_s"] = int((current["end"] - current["start"]).total_seconds())
        intervals.append(current)

    return intervals, quarterlies


def find_gaps(records: list[dict], threshold_s: int) -> list[dict]:
    """Find gaps in the tick stream > threshold_s. These are 'asleep' periods
    even without explicit sleep event (e.g. crash, power loss, USB eject).

    A gap is the time between the last record of one awake session and the
    first record of the next. We classify by what bookends the gap:
        - 'wake_bookend' means a wake event starts after the gap
        - 'restart_bookend' means no wake event but a restart happened
        - 'orphan' means neither — likely a partial truncation
    """
    if len(records) < 2:
        return []
    gaps = []
    for i in range(1, len(records)):
        prev = records[i - 1]
        cur = records[i]
        delta = (cur["_dt"] - prev["_dt"]).total_seconds()
        if delta > threshold_s:
            gaps.append({
                "from": prev["_dt"],
                "to": cur["_dt"],
                "duration_s": int(delta),
                "before_event": prev.get("event"),
                "after_event": cur.get("event"),
                "after_watchdog_pid": cur.get("watchdog_pid"),
            })
    return gaps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "timeline")
    ap.add_argument("--heartbeat", type=Path, default=None,
                    help="path to icarus-heartbeat.jsonl (default: auto-resolve)")
    ap.add_argument("--since", type=str, default=None,
                    help="only show events after this ISO timestamp")
    ap.add_argument("--gap-threshold", type=int, default=60,
                    help="seconds — gaps larger than this count as 'asleep' (default 60)")
    ap.add_argument("--events", type=str, nargs="*", default=None,
                    help="only show records with these event types (e.g. restart system_change)")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable JSON output")
    args = ap.parse_args()

    hb_path = args.heartbeat or _resolve_heartbeat()
    if not hb_path.is_file():
        print(f"[error] heartbeat not found: {hb_path}", file=sys.stderr)
        print("        watchdog has not run yet, or HERMES_ROOT is wrong.", file=sys.stderr)
        return 2

    records = parse_heartbeat(hb_path)
    if args.since:
        since_dt = _parse_ts(args.since)
        records = [r for r in records if r["_dt"] >= since_dt]

    if args.json:
        # JSON output: pass intervals + gaps + quarterlies as a flat structure.
        intervals, quarterlies = reconstruct_timeline(records, args.gap_threshold)
        gaps = find_gaps(records, args.gap_threshold)
        out = {
            "heartbeat_path": str(hb_path),
            "since": args.since,
            "record_count": len(records),
            "intervals": [
                {**{k: (_fmt_ts(v) if isinstance(v, datetime) else v)
                     for k, v in it.items() if k != "events"},
                 "event_count": len(it.get("events", []))}
                for it in intervals
            ],
            "quarterly_summaries": [
                {**{k: (_fmt_ts(v) if isinstance(v, datetime) else v)
                     for k, v in q.items()}}
                for q in quarterlies
            ],
            "asleep_gaps": [
                {**{k: (_fmt_ts(v) if isinstance(v, datetime) else v) for k, v in g.items()}}
                for g in gaps
            ],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    # Human-readable output.
    print(f"🧠  Ikaros Timeline — {hb_path}")
    print(f"    records: {len(records)}")
    if not records:
        return 0

    intervals, quarterlies = reconstruct_timeline(records, args.gap_threshold)
    print(f"    awake intervals: {len(intervals)}")
    if quarterlies:
        print(f"    quarterly summaries (fuzzy memory): {len(quarterlies)}")
    print()

    print("─" * 78)
    print("📍  Awake intervals")
    print("─" * 78)
    for i, it in enumerate(intervals, 1):
        dur = it["duration_s"]
        dur_str = str(timedelta(seconds=dur)) if dur > 0 else "0s"
        host_info = []
        if it.get("host"): host_info.append(f"host={it['host']}")
        if it.get("user"): host_info.append(f"user={it['user']}")
        if it.get("os"): host_info.append(f"os={it['os'][:30]}")
        print(f"  #{i}  {_fmt_ts(it['start'])}  →  "
              + (f"{_fmt_ts(it['end'])}" if it['end_kind'] != 'still running' else "now")
              + f"   ({dur_str})")
        if host_info:
            print(f"        🖥  {' · '.join(host_info)}")
        if it.get("watchdog_pid"):
            print(f"        🐕 watchdog pid {it['watchdog_pid']}")
        if it["end_kind"] != "still running":
            tag = it["end_kind"]
            print(f"        💤 ended: {tag}")
        # Event summary
        ev_counts: dict[str, int] = {}
        for ev in it.get("events", []):
            ev_counts[ev.get("event", "?")] = ev_counts.get(ev.get("event", "?"), 0) + 1
        if ev_counts:
            ev_str = ", ".join(f"{k}×{v}" for k, v in sorted(ev_counts.items()) if k != "tick")
            tick_n = ev_counts.get("tick", 0)
            if ev_str:
                ev_str += f", tick×{tick_n}"
            else:
                ev_str = f"tick×{tick_n}"
            print(f"        📊 {ev_str}")
        # Show non-tick events of interest
        interesting = [e for e in it.get("events", [])
                       if e.get("event") in ("service_down", "restart", "system_change")]
        for e in interesting:
            ts = _fmt_ts(e["_dt"])
            if e.get("event") == "system_change":
                chg = e.get("changed", [])
                prev = e.get("prev", {})
                cur = e.get("cur", {})
                detail = ", ".join(f"{k}: {prev.get(k,'?')}→{cur.get(k,'?')}" for k in chg)
                print(f"        🔄  {ts}  system_change: {detail}")
            elif e.get("event") == "service_down":
                print(f"        ⚠️   {ts}  {e.get('service')} DOWN (port {e.get('port')})")
            elif e.get("event") == "restart":
                ok = "✓" if e.get("ok") else "✗"
                print(f"        🔧  {ts}  restart {e.get('service')} (:{e.get('port')}) {ok}")

    print()
    print("─" * 78)
    print(f"😴  Asleep gaps (> {args.gap_threshold}s)")
    print("─" * 78)
    gaps = find_gaps(records, args.gap_threshold)
    if not gaps:
        print("    (none — no gaps longer than threshold)")
    else:
        for g in gaps:
            dur = str(timedelta(seconds=g["duration_s"]))
            print(f"  ⏸   {_fmt_ts(g['from'])}  →  {_fmt_ts(g['to'])}   "
                  f"({dur})  [was {g['before_event']}, then {g['after_event']}]")

    if quarterlies:
        print()
        print("─" * 78)
        print("🧠  Fuzzy memory (quarterly summaries — older than 1 year)")
        print("─" * 78)
        for q in quarterlies:
            qkey = q.get("quarter", "?")
            host = q.get("host") or "?"
            up = q.get("uptime_pct")
            up_str = f"{up}%" if isinstance(up, (int, float)) else str(up)
            print(f"  {qkey}  host={host}  uptime={up_str}  "
                  f"wake={q.get('wake_count', 0)}  "
                  f"restarts={q.get('restarts', 0)}  "
                  f"sys_changes={q.get('system_changes', 0)}")
            if q.get("first_wake"):
                print(f"        first_wake: {q['first_wake']}")
            if q.get("last_sleep"):
                print(f"        last_sleep: {q['last_sleep']}")

    if args.events:
        print()
        print("─" * 78)
        print(f"🎯  Filtered events: {args.events}")
        print("─" * 78)
        for r in records:
            if r.get("event") in args.events:
                print(f"  {_fmt_ts(r['_dt'])}  {r.get('event')}  "
                      f"{json.dumps({k: v for k, v in r.items() if k not in ('_dt', 'ts', 'event')}, ensure_ascii=False)[:200]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
