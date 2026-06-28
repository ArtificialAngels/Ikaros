#!/usr/bin/env python3
"""
bin/ikaros-self-explore.py — Ikaros self-understanding via GitNexus.

A meta-tool that wraps the GitNexus MCP-equivalent CLI (gitnexus query/cypher/
context/impact/trace/list) into a single command, so the agent (or a human)
can run a battery of self-exploration queries without having to remember the
17 MCP tool names.

Usage:
    python bin/ikaros-self-explore.py report              # full self-portrait
    python bin/ikaros-self-explore.py hub                  # top callers (Python)
    python bin/ikaros-self-explore.py impact <name>        # blast radius
    python bin/ikaros-self-explore.py trace <from> <to>     # shortest call path
    python bin/ikaros-self-explore.py score <skill-dir>    # skill quality
    python bin/ikaros-self-explore.py score-all            # score all our skills

Prereqs:
    GitNexus 1.6.7+ installed at ~/.local/share/gitnexus/gitnexus/
    Skill quality scorer at data/hermes-agent/skills/skill-quality-scorer/

Outputs are JSON-ish printed tables. The point is to give a fresh agent (or
a fresh session) a one-shot way to re-orient itself on the codebase without
having to re-derive all the cypher queries from memory.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERMES_ROOT = Path(__file__).resolve().parent.parent
GITNEXUS = Path.home() / ".local/share/gitnexus/gitnexus/dist/cli/index.js"
NODE = shutil.which("node") or sys.executable
SCORER = HERMES_ROOT / "data/hermes-agent/skills/skill-quality-scorer/scripts/static_audit.py"
SKILLS_ROOT = HERMES_ROOT / "data/hermes-agent/skills"

OUR_SKILLS = [
    "productivity/cad-file-inventory",
    "productivity/file-inventory-excel",
    "productivity/ikaros-meta-skill-miner",
    "productivity/meta-cad-inventory-pipeline",
    "software-development/hermes-windows-runtime",
    "software-development/hermes-agent-skill-authoring",
    "data-science/dxf-table-extract",
    "autonomous-ai-agents/hermes-dojo",
    "autonomous-ai-agents/ikaros-self-audit",
    "autonomous-ai-agents/ikaros-self-orientation",
]


def call_gitnexus(args: list[str], timeout: int = 30) -> str:
    """Run gitnexus CLI and return stdout."""
    cmd = [NODE, str(GITNEXUS), *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={
        **__import__("os").environ, "GITNEXUS_LBUG_EXTENSION_INSTALL": "load-only"
    })
    return r.stdout


def call_cypher(stmt: str) -> str:
    """Run a cypher query and return the markdown table."""
    r = subprocess.run([NODE, str(GITNEXUS), "cypher", stmt],
                       capture_output=True, text=True, timeout=20, env={
        **__import__("os").environ, "GITNEXUS_LBUG_EXTENSION_INSTALL": "load-only"
    })
    try:
        d = json.loads(r.stdout)
        return d.get("markdown") or r.stdout
    except json.JSONDecodeError:
        return r.stdout


def call_impact(target: str, direction: str = "upstream", file_path: str = "") -> str:
    args = ["impact", target, "--direction", direction, "--summaryOnly"]
    if file_path:
        args += ["--file-path", file_path]
    r = subprocess.run([NODE, str(GITNEXUS), *args],
                       capture_output=True, text=True, timeout=15, env={
        **__import__("os").environ, "GITNEXUS_LBUG_EXTENSION_INSTALL": "load-only"
    })
    return r.stdout


def call_trace(from_sym: str, to_sym: str) -> str:
    r = subprocess.run([NODE, str(GITNEXUS), "trace", from_sym, to_sym],
                       capture_output=True, text=True, timeout=15, env={
        **__import__("os").environ, "GITNEXUS_LBUG_EXTENSION_INSTALL": "load-only"
    })
    return r.stdout


def score_skill(skill_dir: Path) -> dict:
    """Run skill-quality-scorer on a skill directory."""
    if not skill_dir.exists():
        return {"error": f"not found: {skill_dir}"}
    r = subprocess.run([sys.executable, str(SCORER), str(skill_dir)],
                       capture_output=True, text=True, timeout=15)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": r.stdout[:200]}


def cmd_report() -> None:
    """Full self-portrait: top communities, hub symbols, Hermes core, our skills."""
    print("=" * 78)
    print("ICARUS SELF-PORTRAIT")
    print("=" * 78)

    print("\n## 1. Top non-Static communities (real code clusters)\n")
    print(call_cypher(
        "MATCH (c:Community) WHERE c.heuristicLabel IS NOT NULL AND c.symbolCount > 5 "
        "AND c.heuristicLabel <> 'Static' "
        "RETURN c.heuristicLabel AS name, c.symbolCount AS syms, c.cohesion AS coh "
        "ORDER BY syms DESC LIMIT 12"))

    print("\n## 2. Python core hub (top 10 most-called, excluding frontend)\n")
    print(call_cypher(
        "MATCH (f:Function)-[r:CodeRelation {type: 'CALLS'}]->(g:Function) "
        "WHERE (f.filePath STARTS WITH 'hermes/' OR f.filePath STARTS WITH 'bridge/') "
        "AND NOT f.filePath CONTAINS 'static' "
        "RETURN g.name AS callee, g.filePath AS path, count(f) AS callers "
        "ORDER BY callers DESC LIMIT 10"))

    print("\n## 3. Hermes Python core (highest-cohesion community)\n")
    print(call_cypher(
        "MATCH (c:Community) WHERE c.heuristicLabel = 'Hermes' "
        "MATCH (f:Function)-[:CodeRelation {type: 'MEMBER_OF'}]->(c) "
        "RETURN f.name AS name, f.filePath AS path, f.startLine AS line "
        "ORDER BY f.filePath"))

    print("\n## 4. Cross-community processes (key execution flows)\n")
    print(call_cypher(
        "MATCH (p:Process) WHERE p.heuristicLabel IS NOT NULL "
        "AND p.processType = 'cross_community' "
        "RETURN p.heuristicLabel AS process, p.stepCount AS steps "
        "ORDER BY p.stepCount DESC LIMIT 10"))

    print("\n## 5. Our skill quality scores\n")
    print("Loading static_audit.py for each of", len(OUR_SKILLS), "skills...\n")
    print(f"{'skill':<50} {'score':<10} {'pct':<6} {'desc':<6} {'body':<6} {'flags'}")
    print("-" * 100)
    for s in OUR_SKILLS:
        d = score_skill(SKILLS_ROOT / s)
        if "error" in d:
            print(f"{s:<50} ERROR: {d['error'][:40]}")
            continue
        auto = d.get("auto_scores", {})
        total = sum(auto.values())
        max_total = len(auto) * 2 if auto else 0
        pct = round(total / max_total * 100) if max_total else 0
        dl = d["metrics"]["description_len"]
        bl = d["metrics"]["body_lines"]
        fl = len(d.get("flags", []))
        print(f"{s:<50} {total}/{max_total:<8} {pct}%{'':<3} {dl:<6} {bl:<6} {fl}")


def cmd_hub() -> None:
    """Top hub symbols across the codebase (Python only by default)."""
    print(call_cypher(
        "MATCH (f:Function)-[r:CodeRelation {type: 'CALLS'}]->(g:Function) "
        "WHERE (f.filePath STARTS WITH 'hermes/' OR f.filePath STARTS WITH 'bridge/') "
        "AND NOT f.filePath CONTAINS 'static' "
        "RETURN g.name AS callee, g.filePath AS path, count(f) AS callers "
        "ORDER BY callers DESC LIMIT 15"))


def cmd_impact(target: str) -> None:
    """Blast radius for a target symbol."""
    print(f"Impact analysis: {target}\n")
    print(call_impact(target, "upstream"))


def cmd_trace(from_sym: str, to_sym: str) -> None:
    """Shortest path between two symbols."""
    print(f"Trace: {from_sym} -> {to_sym}\n")
    print(call_trace(from_sym, to_sym))


def cmd_score(skill_dir: str) -> None:
    """Score a single skill directory."""
    p = Path(skill_dir) if Path(skill_dir).is_absolute() else (SKILLS_ROOT / skill_dir)
    d = score_skill(p)
    print(json.dumps(d, indent=2, ensure_ascii=False))


def cmd_score_all() -> None:
    """Score all our skills (mirrored in cmd_report)."""
    print(f"{'skill':<50} {'score':<10} {'pct':<6} {'desc':<6} {'body':<6} {'flags'}")
    print("-" * 100)
    for s in OUR_SKILLS:
        d = score_skill(SKILLS_ROOT / s)
        if "error" in d:
            print(f"{s:<50} ERROR: {d['error'][:40]}")
            continue
        auto = d.get("auto_scores", {})
        total = sum(auto.values())
        max_total = len(auto) * 2 if auto else 0
        pct = round(total / max_total * 100) if max_total else 0
        dl = d["metrics"]["description_len"]
        bl = d["metrics"]["body_lines"]
        fl = len(d.get("flags", []))
        print(f"{s:<50} {total}/{max_total:<8} {pct}%{'':<3} {dl:<6} {bl:<6} {fl}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]
    if cmd == "report":
        cmd_report()
    elif cmd == "hub":
        cmd_hub()
    elif cmd == "impact":
        if len(sys.argv) < 3:
            print("usage: ikaros-self-explore.py impact <target>")
            return 2
        cmd_impact(sys.argv[2])
    elif cmd == "trace":
        if len(sys.argv) < 4:
            print("usage: ikaros-self-explore.py trace <from> <to>")
            return 2
        cmd_trace(sys.argv[2], sys.argv[3])
    elif cmd == "score":
        if len(sys.argv) < 3:
            print("usage: ikaros-self-explore.py score <skill-dir>")
            return 2
        cmd_score(sys.argv[2])
    elif cmd == "score-all":
        cmd_score_all()
    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
