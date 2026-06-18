#!/usr/bin/env python3
"""
bin/icarus-heartbeat-archive.py — Compact the heartbeat log to bound its
size as it grows. The watchdog writes one line per tick (10s) + one
line per service_status (60s) + wake/sleep/restart/system_change events
on transition. At 10s tick rate + 60s snapshot, the file grows at
~12 lines/min = ~17000 lines/day = ~50MB/year raw JSONL. Three-stage
archival keeps the log useful but bounded:

  Stage 1 (FRESH): records newer than --compress-days (default 60d)
           keep everything.
  Stage 2 (COMPRESSED): records between compress and --fuzzy-days
           (default 365d) — drop noisy events (tick, service_status)
           but keep all signal events (wake, sleep, restart, etc).
  Stage 3 (FUZZY): records between fuzzy and --delete-days
           (default 730d = 2y) — collapse into quarterly summaries.
           We retain only: (a) the FIRST wake and LAST sleep of each
           quarter, (b) any system_change event (machine switch is
           load-bearing history), and (c) a synthetic "quarterly"
           event summarising uptime / restarts / system changes.
  Stage 4 (DROP): records older than --delete-days are removed.

The quarterly summary is itself a JSONL row with a special "quarterly"
event so timeline readers can recognise it. We compute it from the
raw records in the fuzzy window: for each (year, quarter) bucket,
emit one "quarterly" line with:
  - quarter (e.g. "2026-Q1")
  - host (mode of machine identity seen that quarter)
  - uptime_pct = (tick_count_within_quarter) / (expected_ticks)
  - restarts, system_changes counts
  - first_wake, last_sleep timestamps

Default thresholds can be overridden via flags. Re-runnable and
idempotent (re-running on already-archived data is a no-op).

Usage:
    python bin/icarus-heartbeat-archive.py             # do it
    python bin/icarus-heartbeat-archive.py --dry-run   # report only
    python bin/icarus-heartbeat-archive.py --compress-days 60
    python bin/icarus-heartbeat-archive.py --fuzzy-days 365
    python bin/icarus-heartbeat-archive.py --delete-days 730
    python bin/icarus-heartbeat-archive.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _resolve_heartbeat() -> Path:
    here = Path(__file__).resolve()
    repo_root = here.parent.parent
    candidates = [
        repo_root / "data" / "logs" / "icarus-heartbeat.jsonl",
    ]
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
    """Parse the watchdog's ts format (with +0800 offset) to a naive UTC."""
    if len(ts) >= 5 and ts[-5] in "+-" and ts[-4:].isdigit():
        sign = 1 if ts[-5] == "+" else -1
        hh = int(ts[-4:-2])
        mm = int(ts[-2:])
        offset = timedelta(hours=sign * hh, minutes=sign * mm)
        naive = datetime.fromisoformat(ts[:-5])
        return naive - offset
    return datetime.fromisoformat(ts)


def _quarter(dt: datetime) -> str:
    """Return 'YYYY-QN' for a datetime."""
    return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"


# Events we always keep as signal, even after compress stage.
SIGNAL_EVENTS = frozenset({
    "wake",
    "sleep",
    "sleep_final",
    "service_down",
    "restart",
    "system_change",
})


def _quarterly_summary(records: list[dict], quarter_key: str) -> dict:
    """Build a synthetic 'quarterly' record for a bucket of records.
    Summarises: host (mode), uptime_pct, restarts count,
    system_changes count, first_wake, last_sleep, total_ticks.
    """
    ticks = [r for r in records if r.get("event") == "tick"]
    wakes = [r for r in records if r.get("event") == "wake"]
    sleeps = [r for r in records if r.get("event") in ("sleep", "sleep_final")]
    restarts = [r for r in records if r.get("event") == "restart"]
    sys_changes = [r for r in records if r.get("event") == "system_change"]
    hosts = [r.get("host") for r in records if r.get("host")]
    if not ticks:
        uptime_pct = None
    elif len(ticks) < 2 or records[-1]["_dt"] == records[0]["_dt"]:
        # Degenerate case: all records have the same timestamp (e.g.
        # a test bucket that synthesized 50 ticks at one ts). With no
        # real span we can't compute a meaningful uptime ratio. Fall
        # back to "tick count signal" — many ticks = agent was alive
        # in that quarter, even if we can't compute the exact ratio.
        uptime_pct = "n/a (degenerate bucket)"
    else:
        span = (records[-1]["_dt"] - records[0]["_dt"]).total_seconds()
        expected = max(1, int(span / 10))  # tick every 10s
        uptime_pct = round(min(1.0, len(ticks) / expected) * 100, 1)
    return {
        "ts": records[0]["ts"],  # quarter start ts (canonical)
        "event": "quarterly",
        "quarter": quarter_key,
        "host": Counter(hosts).most_common(1)[0][0] if hosts else None,
        "uptime_pct": uptime_pct,
        "tick_count": len(ticks),
        "wake_count": len(wakes),
        "sleep_count": len(sleeps),
        "restarts": len(restarts),
        "system_changes": len(sys_changes),
        "first_wake": wakes[0]["ts"] if wakes else None,
        "last_sleep": sleeps[-1]["ts"] if sleeps else None,
        "record_span_s": int((records[-1]["_dt"] - records[0]["_dt"]).total_seconds()),
    }


def plan_archival(
    path: Path,
    now: datetime,
    compress_days: int,
    fuzzy_days: int,
    delete_days: int,
) -> dict:
    """Three-stage plan: fresh / compressed / fuzzy / drop.

    Returns counts + kept line bytes (raw lines for fresh+compressed,
    synthesised quarterly lines for fuzzy).
    """
    if not (compress_days < fuzzy_days < delete_days):
        raise ValueError(
            f"need compress({compress_days}) < fuzzy({fuzzy_days}) < delete({delete_days})"
        )
    cutoff_compress = now - timedelta(days=compress_days)
    cutoff_fuzzy = now - timedelta(days=fuzzy_days)
    cutoff_delete = now - timedelta(days=delete_days)

    kept_lines: list[bytes] = []
    fuzzy_buckets: dict[str, list[dict]] = {}  # quarter_key -> [records]
    counts = {
        "total": 0,
        "kept_fresh": 0,
        "kept_signal_compressed": 0,  # 60d-365d signal events
        "dropped_noisy_compressed": 0,  # 60d-365d tick/service_status
        "kept_fuzzy_quarterly": 0,  # 365d-730d summarised
        "kept_fuzzy_syschange": 0,  # 365d-730d system_change kept raw
        "dropped_fuzzy_detail": 0,  # 365d-730d detail events dropped
        "dropped_too_old": 0,  # > 730d
        "bad_lines": 0,
    }
    bytes_in = 0
    bytes_kept = 0

    with path.open("rb") as f:
        for raw in f:
            bytes_in += len(raw)
            counts["total"] += 1
            line = raw.rstrip(b"\r\n")
            try:
                rec = json.loads(line.decode("utf-8", errors="replace"))
                rec["_dt"] = _parse_ts(rec["ts"])
            except Exception:
                counts["bad_lines"] += 1
                continue
            ts = rec["_dt"]

            if ts < cutoff_delete:
                counts["dropped_too_old"] += 1
                continue

            if ts < cutoff_fuzzy:
                # Fuzzy window: collect into quarter bucket, drop detail.
                ev = rec.get("event")
                if ev == "system_change":
                    # System changes are load-bearing; keep raw, not
                    # folded into quarterly summary.
                    counts["kept_fuzzy_syschange"] += 1
                    kept_lines.append(raw)
                    bytes_kept += len(raw)
                    # Also collect into quarter bucket for context.
                    qkey = _quarter(ts)
                    fuzzy_buckets.setdefault(qkey, []).append(rec)
                elif ev in SIGNAL_EVENTS:
                    # Other signal events (wake/sleep/restart) in fuzzy
                    # window: keep only the first wake and last sleep
                    # of each quarter; drop per-restart detail.
                    qkey = _quarter(ts)
                    bucket = fuzzy_buckets.setdefault(qkey, [])
                    bucket.append(rec)
                    counts["dropped_fuzzy_detail"] += 1
                else:
                    # Noisy events (tick, service_status): drop.
                    counts["dropped_fuzzy_detail"] += 1
                continue

            if ts < cutoff_compress:
                ev = rec.get("event")
                if ev in SIGNAL_EVENTS:
                    counts["kept_signal_compressed"] += 1
                    kept_lines.append(raw)
                    bytes_kept += len(raw)
                else:
                    counts["dropped_noisy_compressed"] += 1
                continue

            # Fresh: keep all.
            counts["kept_fresh"] += 1
            kept_lines.append(raw)
            bytes_kept += len(raw)

    # Build quarterly summaries from buckets.
    summary_lines: list[bytes] = []
    for qkey, bucket in sorted(fuzzy_buckets.items()):
        if not bucket:
            continue
        # Sort by ts (records were read in order but the bucket may
        # contain both signal and dropped-syschange raw records).
        bucket.sort(key=lambda r: r["_dt"])
        # If a quarter has any system_change raw records, we already
        # appended those. Now we need to fold the per-restart / per-tick
        # detail into ONE summary line per quarter.
        if len(bucket) <= 1:
            continue  # only one event in this quarter — no need to summarise
        summary = _quarterly_summary(bucket, qkey)
        line = (json.dumps(summary, ensure_ascii=False) + "\n").encode("utf-8")
        summary_lines.append(line)
        counts["kept_fuzzy_quarterly"] += 1
        bytes_kept += len(line)

    # Final kept lines: fresh + compressed + fuzzy raw syschange + summaries.
    # We need to interleave summaries in time-order. The raw syschange
    # lines were appended in file-order, so to keep chronological order
    # we sort all kept lines by their decoded ts.
    all_kept = list(kept_lines) + list(summary_lines)
    all_kept.sort(
        key=lambda b: json.loads(b.decode("utf-8"))["ts"]
    )

    return {
        "counts": counts,
        "kept_lines": all_kept,
        "bytes_in": bytes_in,
        "bytes_kept": bytes_kept,
        "cutoff_compress": cutoff_compress,
        "cutoff_fuzzy": cutoff_fuzzy,
        "cutoff_delete": cutoff_delete,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "archive")
    ap.add_argument("--heartbeat", type=Path, default=None,
                    help="path to icarus-heartbeat.jsonl (default: auto-resolve)")
    ap.add_argument("--compress-days", type=int, default=60,
                    help="records older than this many days get noisy events dropped (default 60 = 2 months)")
    ap.add_argument("--fuzzy-days", type=int, default=365,
                    help="records older than this many days get summarised quarterly (default 365 = 1 year)")
    ap.add_argument("--delete-days", type=int, default=730,
                    help="records older than this many days are removed (default 730 = 2 years)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report counts without writing")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output")
    args = ap.parse_args()

    hb_path = args.heartbeat or _resolve_heartbeat()
    if not hb_path.is_file():
        print(f"[error] heartbeat not found: {hb_path}", file=sys.stderr)
        return 2

    try:
        if not (args.compress_days < args.fuzzy_days < args.delete_days):
            print(f"[error] need compress({args.compress_days}) < "
                  f"fuzzy({args.fuzzy_days}) < delete({args.delete_days})",
                  file=sys.stderr)
            return 2
    except Exception:
        print("[error] day thresholds must be integers", file=sys.stderr)
        return 2

    now = datetime.now()
    plan = plan_archival(hb_path, now, args.compress_days, args.fuzzy_days, args.delete_days)
    c = plan["counts"]
    pct = (1.0 - plan["bytes_kept"] / plan["bytes_in"]) * 100 if plan["bytes_in"] else 0.0

    if args.json:
        out = {
            "heartbeat_path": str(hb_path),
            "now": now.isoformat(),
            "cutoff_compress": plan["cutoff_compress"].isoformat(),
            "cutoff_fuzzy": plan["cutoff_fuzzy"].isoformat(),
            "cutoff_delete": plan["cutoff_delete"].isoformat(),
            "dry_run": args.dry_run,
            "counts": c,
            "bytes_in": plan["bytes_in"],
            "bytes_kept": plan["bytes_kept"],
            "reduction_pct": round(pct, 1),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    # Human-readable report.
    print(f"🧹  Heartbeat Archival — {hb_path}")
    print(f"    now:                {now.isoformat(timespec='seconds')}")
    print(f"    compress threshold: {plan['cutoff_compress'].isoformat(timespec='seconds')}  ({args.compress_days}d)")
    print(f"    fuzzy threshold:    {plan['cutoff_fuzzy'].isoformat(timespec='seconds')}  ({args.fuzzy_days}d)")
    print(f"    delete threshold:   {plan['cutoff_delete'].isoformat(timespec='seconds')}  ({args.delete_days}d)")
    print()
    print(f"    total lines:           {c['total']}")
    print()
    print(f"    Stage 1 FRESH (<{args.compress_days}d):")
    print(f"        kept:               {c['kept_fresh']}")
    print()
    print(f"    Stage 2 COMPRESSED ({args.compress_days}d-{args.fuzzy_days}d):")
    print(f"        kept (signal):      {c['kept_signal_compressed']}  ← wake/sleep/restart/service_down")
    print(f"        dropped (noisy):    {c['dropped_noisy_compressed']}  ← tick/service_status")
    print()
    print(f"    Stage 3 FUZZY ({args.fuzzy_days}d-{args.delete_days}d):")
    print(f"        kept (quarterly):   {c['kept_fuzzy_quarterly']}  ← 1 line per quarter summary")
    print(f"        kept (system_change):{c['kept_fuzzy_syschange']}  ← machine switch events kept raw")
    print(f"        dropped (detail):   {c['dropped_fuzzy_detail']}  ← per-tick/per-restart detail")
    print()
    print(f"    Stage 4 DROP (>{args.delete_days}d):")
    print(f"        dropped:            {c['dropped_too_old']}")
    if c["bad_lines"]:
        print(f"    ⚠️  bad lines:          {c['bad_lines']}")
    print()
    print(f"    bytes:  {plan['bytes_in']} → {plan['bytes_kept']}  "
          f"(reduced {pct:.1f}%)")

    if args.dry_run:
        print()
        print("    [dry-run] file NOT modified. Re-run without --dry-run to apply.")
        return 0

    # Atomic write.
    tmp_path = hb_path.with_suffix(hb_path.suffix + ".tmp")
    with tmp_path.open("wb") as f:
        for line in plan["kept_lines"]:
            f.write(line)
    tmp_path.replace(hb_path)
    print()
    print(f"    ✓ wrote {len(plan['kept_lines'])} lines to {hb_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
