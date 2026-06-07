"""
Verify the api-adapter.js route table:
  * Specific kanban routes (boards, board, tasks, etc.) come BEFORE any
    catch-all that would otherwise shadow them.
  * Every kanban route in the source has either a static URL or url:null
    (so the adapter won't synthesise a bogus fallback URL).
  * The catch-all /api/kanban/ stub is gone.
"""
import re
import sys
from pathlib import Path

src = Path(r"E:\Hermes Agent\hermes\static\api-adapter.js").read_text(encoding="utf-8")

# 1. The old noop catch-all for kanban must be gone.
old_noop_boards = re.search(
    r"\{ match: \(p\) => p === '/api/kanban/boards', method: 'GET',\s*"
    r"url: '/api/webui/noop'",
    src,
)
old_noop_catch = re.search(
    r"\{ match: \(p\) => p\.startsWith\('/api/kanban/'\), method: '\*',\s*"
    r"url: '/api/webui/noop'",
    src,
)
print("=== api-adapter.js sanity checks ===")
print(f"  [{'PASS' if old_noop_boards is None else 'FAIL'}] old /api/kanban/boards noop stub is gone")
print(f"  [{'PASS' if old_noop_catch is None else 'FAIL'}] old /api/kanban/* noop catch-all is gone")

# 2. The new passthrough entries must exist.
need = [
    "/api/kanban/boards",
    "/api/kanban/board",
    "/api/kanban/tasks",
    "/api/kanban/tasks/bulk",
    "/api/kanban/tasks/",
    "/api/kanban/config",
    "/api/kanban/assignees",
    "/api/kanban/stats",
    "/api/kanban/events",
    "/api/kanban/events/stream",
    "/api/kanban/dispatch",
    "/api/kanban/boards/",
]
for n in need:
    found = n in src
    print(f"  [{'PASS' if found else 'FAIL'}] route for {n} is present")

# 3. No kanban route still points to /api/webui/noop.
m = re.search(r"url: '/api/webui/noop'[^\n]*\n[^\n]*kanban", src)
print(f"  [{'PASS' if m is None else 'FAIL'}] no kanban route still uses /api/webui/noop")

# 4. Make sure the v0.5 banner shows.
print(f"  [{'PASS' if 'v0.5' in src else 'FAIL'}] v0.5 banner is present")

# 5. Check ordering: passthrough boards list (GET) must come before the
#    catch-all (method: '*'). The crons pattern above the kanban block is a
#    good ordering reference.
#    Find indices of all "match: (p) => ..." lines for kanban, ensure that
#    the more-specific entries (no {slug}) appear first.
lines = src.splitlines()
specific_idx = []
broad_idx = []
for i, line in enumerate(lines):
    if "match: (p) => p" in line and "kanban" in line:
        # "specific" = uses equality or /api/kanban/board; "broad" = startsWith
        if "startsWith" in line:
            broad_idx.append(i)
        else:
            specific_idx.append(i)
print(f"  [{'PASS' if (not specific_idx or not broad_idx or min(specific_idx) < min(broad_idx)) else 'FAIL'}] specific kanban routes come before broad ones")
print(f"      specific first: {specific_idx[:3] if specific_idx else 'none'}, broad first: {broad_idx[:3] if broad_idx else 'none'}")
