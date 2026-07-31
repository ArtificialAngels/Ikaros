#!/usr/bin/env python3
"""Ikaros doc-drift linter (stdlib only).

Scans docs/ (recursively, excluding scripts/ archive + assets/) and the root
AGENTS.md / README.md for stale references that indicate the docs have drifted
from the current implementation:

  (a) deleted files:  think.py, supervisor_persist.py,
                      bin/v5-sync-persona.py
  (b) deleted ports:  :7870, :7871
  (b2) alive-but-confusable: :8642 Hermes API gateway is ACTIVE again
       (bin/hermes-api-server.py; used by dashboard + chat-tree) — NOT deleted
  (c) literal core/v5 (should be core/memory_v5)
  (d) hermes-agent/ as a *code* path (should be core/hermes; data/hermes-agent
      user-state dir is allowed and NOT flagged)

Historical reports may still contain the old names for traceability; they are
exempted when the line also carries an "2026-07-2" correction note.

Prints one `WARN: <file>:<line> <text>` per hit, otherwise `OK: no drift detected`.

Run:  python docs/lint.py
"""

import sys
from pathlib import Path

# --- patterns to flag -------------------------------------------------------
DELETED_FILES = (
    "think.py",
    "supervisor_persist.py",
    "bin/v5-sync-persona.py",
)
DELETED_PORTS = (":7870", ":7871")
OLD_CORE_PATH = "core/v5"  # should now be core/memory_v5
OLD_HERMES_CODE = "hermes-agent/"  # code path; data/hermes-agent is allowed

EXEMPT_DIRS = {"scripts", "assets", ".git"}
EXEMPT_FILENAMES = {"module-dependency-map.html", "architecture-overview.html",
                    "folder-tree.html"}
# files where think.py / old paths appear only as historical analysis (not drift)
THINK_EXEMPT_FILES = {
    "neko-deep-analysis.md",
    "conversation-flow-test-report.md",
}
# dated historical reports: keep original paths for traceability (noted at top)
HISTORICAL_EXEMPT_FILES = {
    "conversation-flow-fix-report-2026-07-25.md",
    "conversation-flow-upgrade-plan.md",
}
# historical patch manifest; legitimately references hermes-agent venv paths
# (it records Ikaros-specific patches against the upstream hermes-agent repo).
HERMES_PATCH_EXEMPT_FILES = {
    "hermes-ikaros-patches.md",
}
RESEARCH_DIR = "research"
# lines carrying a 2026-07-2x correction note keep old names for traceability
CORRECTION_MARK = "2026-07-2"
# lines that merely DESCRIBE a deletion / obsolete state are exempt
OBSOLETE_DESC_MARKS = ("🗑️", "已删除", "勿引用", "移除", "已移除", "NousResearch/hermes-agent",
                       "github.com", "📦", "🎛️", "lint.py", "重命名", "搬迁", "移出")


DOCS_DIR = Path(__file__).resolve().parent


def iter_targets() -> list[Path]:
    targets: list[Path] = []
    # docs tree (skip exempt dirs / html artifacts)
    for p in sorted(DOCS_DIR.rglob("*")):
        if not p.is_file() or p.suffix != ".md":
            continue
        if any(part in EXEMPT_DIRS for part in p.relative_to(DOCS_DIR).parts):
            continue
        if p.name in EXEMPT_FILENAMES:
            continue
        targets.append(p)
    # root project docs
    for name in ("AGENTS.md", "README.md"):
        root_doc = DOCS_DIR.parent / name
        if root_doc.is_file():
            targets.append(root_doc)
    return targets


def scan_file(path: Path) -> list[tuple[str, int, str]]:
    """Return list of (file, line_no, matched_text) hits for one file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    hits: list[tuple[str, int, str]] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\n")
        # exempt historical reports that carry a correction note on the line
        if CORRECTION_MARK in line:
            continue
        # exempt lines that only DESCRIBE a deletion / obsolete state / external repo
        if any(mark in line for mark in OBSOLETE_DESC_MARKS):
            continue
        matched: list[str] = []
        for pat in DELETED_FILES:
            if pat in line:
                matched.append(pat)
        for pat in DELETED_PORTS:
            if pat in line:
                matched.append(pat)
        if OLD_CORE_PATH in line:
            matched.append(OLD_CORE_PATH)
        if OLD_HERMES_CODE in line and "data/hermes-agent" not in line:
            matched.append(OLD_HERMES_CODE)
        # think.py is exempt in historical/analysis docs
        rel = path.name
        try:
            in_research = RESEARCH_DIR in path.relative_to(DOCS_DIR).parts
        except ValueError:
            in_research = False
        if rel in THINK_EXEMPT_FILES or in_research:
            matched = [m for m in matched if m != "think.py"
                       and m != "supervisor_persist.py"]
        # dated historical reports keep original paths (top note explains mapping)
        if rel in HISTORICAL_EXEMPT_FILES:
            matched = [m for m in matched if m in ("think.py", "supervisor_persist.py")]
        # CHANGELOG entries are historical by nature
        if rel == "CHANGELOG.md":
            matched = [m for m in matched if m not in ("think.py", "supervisor_persist.py")]
        # mapping-table rows (old -> new shown side by side) are intentional
        if OLD_CORE_PATH in line and "core/memory_v5" in line:
            matched = [m for m in matched if m != OLD_CORE_PATH]
        if OLD_HERMES_CODE in line and "core/hermes" in line:
            matched = [m for m in matched if m != OLD_HERMES_CODE]
        if rel in HERMES_PATCH_EXEMPT_FILES:
            matched = [m for m in matched if m != OLD_HERMES_CODE]
        if matched:
            seen = set()
            uniq = [m for m in matched if not (m in seen or seen.add(m))]
            hits.append((str(path), i, f"[{', '.join(uniq)}] {line.strip()}"))
    return hits


def main() -> int:
    all_hits: list[tuple[str, int, str]] = []
    for target in iter_targets():
        all_hits.extend(scan_file(target))

    if not all_hits:
        print("OK: no drift detected")
        return 0

    for file, line, text in all_hits:
        print(f"WARN: {file}:{line} {text}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
