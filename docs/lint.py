#!/usr/bin/env python3
"""Ikaros doc-drift linter (stdlib only).

Scans docs/ (recursively, excluding scripts/ archive/ assets/) and the root
AGENTS.md / README.md for stale references that indicate the docs have drifted
from the current implementation:

  (a) deleted files:  think.py, supervisor_persist.py, bin/v5-sync-persona.py,
                      bin/ikaros-memory-watchdog.py (2026-08-19 分布式 watchdog),
                      bin/wd_import.py, 2026-08-14 重构删除的 memory_v5 旧模块等
  (b) deleted ports:  :7870, :7871 (语音桥)
  (b2) retired ports (2026-08-18 底座退役): :8642, :8650, :9119, :9100, :8080,
       :8088, :48911 — hermes gateway/bridge/dashboard、9100 面板、本地 LLM、
       N.E.K.O 前端全部退役；工作引擎 = dsh :3080
  (c) literal core/v5 (should be core/memory_v5)
  (d) hermes-agent/ as a *code* path (data/hermes-agent user-state dir allowed)
  (e) retired dirs (2026-08-18): core/hermes/, apps/neko/, core/control-panel/

Historical reports may still contain the old names for traceability; they are
exempted when the line also carries a dated correction / retirement note.

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
    # 2026-08-14 重构删除: 旧 companion 链 + 死代码 + 旧启动器
    "bin/ikaros-control.bat",
    "bin/cloud_chat.py",
    "bin/ikaros-soul-sync.py",
    "bin/soul_refine.py",
    "bin/night-watchdog.py",
    "core/memory_v5/orchestrator.py",
    "core/memory_v5/hermes_provider.py",
    "core/memory_v5/hermes_client.py",
    "core/memory_v5/router.py",
    "core/memory_v5/task_runner.py",
    "core/memory_v5/provider_bridge.py",
    "core/memory_v5/drivers.py",
)
DELETED_PORTS = (":7870", ":7871")
# 2026-08-18 底座退役端口：hermes 全家 + 9100 面板 + 本地 LLM + N.E.K.O 前端
RETIRED_PORTS = (":8642", ":8650", ":9119", ":9100", ":8080", ":8088", ":48911")
OLD_CORE_PATH = "core/v5"  # should now be core/memory_v5
OLD_HERMES_CODE = "hermes-agent/"  # code path; data/hermes-agent is allowed
# 2026-08-18 退役目录（随底座整体删除）
RETIRED_DIRS = ("core/hermes/", "apps/neko/", "core/control-panel/")

EXEMPT_DIRS = {"scripts", "assets", "archive", ".git"}
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
    "conversation-flow-test-report.md",
    "evolution-path-2026-08-02.md",
    "ikaros-as-hermes-agent-proposal.md",
    "hermes-agent-full-survey.md",
}  # 2026-08-19 起多已迁入 docs/archive/（整目录豁免），此名单保留兜底
# hermes 退役清单本身合法引用退役路径/端口
RETIREMENT_INVENTORY_EXEMPT = "hermes-retirement-inventory.md"
# herdr 提案：顶部横幅已声明 neko/9100/dashboard 引用按退役代入阅读
BANNER_EXEMPT_FILES = {"herdr-integration-design.md"}
# historical patch manifest; legitimately references hermes-agent venv paths
# (it records Ikaros-specific patches against the upstream hermes-agent repo).
HERMES_PATCH_EXEMPT_FILES = {
    "hermes-ikaros-patches.md",
}
RESEARCH_DIR = "research"
# lines carrying a 2026-07-2x / 2026-08-1x correction note keep old names
# for traceability
CORRECTION_MARKS = ("2026-07-2", "2026-08-1")
# lines that merely DESCRIBE a deletion / obsolete / retired state are exempt
OBSOLETE_DESC_MARKS = ("🗑️", "已删除", "随重构删除", "勿引用", "移除", "已移除", "退役",
                       "不存在", "NousResearch/hermes-agent",
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
        if any(mark in line for mark in CORRECTION_MARKS):
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
        # retired ports: only actionable drift if the line does NOT mark them
        # as retired (OBSOLETE_DESC_MARKS "退役" already exempted such lines)
        for pat in RETIRED_PORTS:
            if pat in line:
                matched.append(pat)
        if OLD_CORE_PATH in line:
            matched.append(OLD_CORE_PATH)
        if OLD_HERMES_CODE in line and "data/hermes-agent" not in line:
            matched.append(OLD_HERMES_CODE)
        # retired dirs (core/hermes/, apps/neko/, core/control-panel/)
        for pat in RETIRED_DIRS:
            if pat in line:
                matched.append(pat)
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
        # CHANGELOG entries are historical records: deleted-file names, retired
        # ports and retired dirs appear as dated milestones, never actionable
        # drift; keep core/v5 + hermes-agent/ code-path checks.
        if rel == "CHANGELOG.md":
            matched = [m for m in matched if m not in DELETED_FILES
                       and m not in RETIRED_PORTS and m not in RETIRED_DIRS]
        # the retirement inventory itself legitimately references retired paths
        if rel == RETIREMENT_INVENTORY_EXEMPT:
            matched = [m for m in matched if m not in RETIRED_PORTS
                       and m not in RETIRED_DIRS and m != OLD_HERMES_CODE]
        # banner-exempt proposals (top banner declares retirement mapping)
        if rel in BANNER_EXEMPT_FILES:
            matched = [m for m in matched if m not in RETIRED_DIRS]
        # mapping-table rows (old -> new shown side by side) are intentional
        if OLD_CORE_PATH in line and "core/memory_v5" in line:
            matched = [m for m in matched if m != OLD_CORE_PATH]
        if OLD_HERMES_CODE in line and "runtime/hermes-agent" in line:
            matched = [m for m in matched if m != OLD_HERMES_CODE]
        if rel in HERMES_PATCH_EXEMPT_FILES:
            matched = [m for m in matched if m != OLD_HERMES_CODE]
        if matched:
            seen = set()
            uniq = [m for m in matched if not (m in seen or seen.add(m))]
            hits.append((str(path), i, f"[{', '.join(uniq)}] {line.strip()}"))
    return hits


def main() -> int:
    # Windows GBK 控制台打印含 ❌/中文的 WARN 行会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
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
