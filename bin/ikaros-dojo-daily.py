#!/usr/bin/env python3
"""ikaros-dojo-daily.py — 每日 dojo 自动循环

由 watchdog 每 24h 触发（或用户手动：`python bin/ikaros-dojo-daily.py`）。
设计原则（Ikaros 路径 2）：
  1. 只读分析 + tracker save —— 永不自动 apply/evolve
  2. 写一个 daily note 到 data/hermes-agent/memories/ikaros/dojo-YYYY-MM-DD.md
  3. 失败 top-3 写入 .proposals/dojo-YYYY-MM-DD.md，等用户确认才 mint
  4. exit code: 0=ok, 1=analyzer error, 2=DB unreadable
"""
import os, sys, json, subprocess, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent.parent
HERMES_HOME = ROOT / "data" / "hermes-agent"
DOJO = HERMES_HOME / "skills" / "autonomous-ai-agents" / "hermes-dojo"
MEM = HERMES_HOME / "memories" / "ikaros"
PROP = HERMES_HOME / "skills" / ".proposals"

os.environ["HERMES_HOME"] = str(HERMES_HOME)


def run(cmd, timeout=60):
    """Run a subprocess, return (rc, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"=== Ikaros dojo-daily {today} ===", flush=True)

    # 1) monitor.py (per-tool success rates)
    rc, out, err = run(
        ["portable-python/python.exe",
         str(DOJO / "scripts" / "monitor.py"),
         "--days", "7", "--json"],
        timeout=60,
    )
    if rc != 0:
        print(f"  [FAIL] monitor.py rc={rc}: {err[:200]}")
        return 1
    try:
        report = json.loads(out)
    except json.JSONDecodeError as e:
        print(f"  [FAIL] monitor.json decode: {e}")
        return 1

    sessions = report.get("sessions_analyzed", 0)
    calls = report.get("total_tool_calls", 0)
    rate = report.get("overall_success_rate", 0)
    weak = report.get("weakest_tools", [])[:3]
    print(f"  sessions={sessions}  calls={calls}  success={rate}%")

    # 2) tracker.py save (learning curve)
    rc, out, err = run(
        ["portable-python/python.exe",
         str(DOJO / "scripts" / "tracker.py"), "save"],
        timeout=30,
    )
    tracker_ok = (rc == 0)
    print(f"  tracker.save: {'ok' if tracker_ok else 'fail'}")

    # 3) Build the daily note (read-only analysis, never proposes mutations)
    MEM.mkdir(parents=True, exist_ok=True)
    note = MEM / f"dojo-{today}.md"
    with note.open("w", encoding="utf-8") as f:
        f.write(f"# dojo daily — {today}\n\n")
        f.write(f"**Sessions analyzed**: {sessions}  \n")
        f.write(f"**Total tool calls**: {calls}  \n")
        f.write(f"**Overall success rate**: {rate}%  \n\n")
        f.write("## Top 3 weakest tools\n\n")
        if not weak:
            f.write("(no data — recent sessions may be empty)\n\n")
        for i, w in enumerate(weak, 1):
            err_short = (w.get("top_error") or "")[:120].replace("\n", " ")
            f.write(f"### {i}. `{w.get('tool')}` — {w.get('success_rate', 0)}%\n")
            f.write(f"- total: {w.get('total')}, errors: {w.get('errors')}\n")
            f.write(f"- top error: `{err_short}`\n\n")
        f.write("---\n")
        f.write("**This is a read-only report.** No skills were modified, no ")
        f.write("proposals auto-applied. Run `hermes-dojo` workflow manually ")
        f.write("(`fixer.py` → review → `fixer.py --apply`) to act on findings.\n")
    print(f"  daily note: {note.relative_to(ROOT)}")

    # 4) Emit a heartbeat event so Ikaros timeline sees the dojo tick
    heartbeat = ROOT / "data" / "logs" / "ikaros-heartbeat.jsonl"
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "event": "dojo_daily",
        "ts": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "sessions": sessions,
        "calls": calls,
        "success_rate": rate,
        "weakest_top3": [{"tool": w.get("tool"), "rate": w.get("success_rate")}
                          for w in weak],
        "note_path": str(note.relative_to(ROOT)),
        "tracker_saved": tracker_ok,
        "auto_apply": False,
    }
    with heartbeat.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(f"  heartbeat: dojo_daily event emitted")
    print(f"=== done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())