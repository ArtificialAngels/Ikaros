#!/usr/bin/env python3
"""Scan the Ikaros repo for likely hardcoded secrets.

Stdlib only. Additive scaffolding — does NOT modify any service code.
Run with:  python bin/secret-scan.py

Exit code is always 0 (non-blocking) so it is safe to wire into pre-commit.
"""

import os
import re
import sys

# --- Walk exclusions -------------------------------------------------------
EXCLUDE_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    "runtime",
    "venv",
    "dist",
    "build",
    ".codegraph",
}

# Path fragments that must be skipped wholesale.
EXCLUDE_PATH_FRAGMENTS = (
    "node_modules",
    os.sep + ".git" + os.sep,
    "__pycache__",
    os.sep + "runtime" + os.sep,
    "core/hermes" + os.sep + "venv",
    "core" + os.sep + "hermes" + os.sep + "venv",
)

# File names to skip entirely. The sanctioned secret store `.env` is gitignored
# and is the correct place for secrets, so it must not be flagged as a leak.
SKIP_FILENAMES = {".env", ".env.example"}

# File extensions to skip (images / binaries / generated maps).
SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".tiff",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".zip", ".gz", ".tar",
    ".rar", ".7z", ".pdf", ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4",
    ".avi", ".mov", ".mkv", ".wav", ".flac", ".class", ".jar",
}

# --- Secret patterns -------------------------------------------------------
# Compiled case-insensitively so uppercase attribute names (API_KEY, Password,
# Token, ...) are also caught. The pattern strings match the spec exactly.
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"api[_-]?key\s*=\s*[\"'][^\"']{16,}", re.IGNORECASE),
    re.compile(r"password\s*=\s*[\"'][^\"']{8,}", re.IGNORECASE),
    re.compile(r"github_pat_[A-Za-z0-9]{15,}", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"token\s*=\s*[\"'][^\"']{20,}", re.IGNORECASE),
]

# Lines containing these markers are not real leaks (placeholders / examples).
PLACEHOLDER_MARKERS = (
    "<...>",
    "your_",
    "example",
    "placeholder",
    "todo",
    "sk-${",
    "re.compile",  # skip lines that merely *define* a scan pattern
)

# Max snippet length printed per hit.
MAX_SNIPPET = 80


def is_excluded(path: str) -> bool:
    norm = os.path.normpath(path)
    parts = set(os.path.normpath(p) for p in norm.split(os.sep))
    if parts & EXCLUDE_DIRS:
        return True
    for frag in EXCLUDE_PATH_FRAGMENTS:
        if frag in norm:
            return True
    basename = os.path.basename(norm)
    if basename in SKIP_FILENAMES or basename.startswith(".env."):
        return True
    ext = os.path.splitext(norm)[1].lower()
    if ext in SKIP_EXTENSIONS:
        return True
    return False


def scan_file(abs_path: str, repo_root: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, start=1):
                low = line.lower()
                if any(m in low for m in PLACEHOLDER_MARKERS):
                    continue
                for pat in SECRET_PATTERNS:
                    m = pat.search(line)
                    if m:
                        snippet = m.group(0)
                        if len(snippet) > MAX_SNIPPET:
                            snippet = snippet[:MAX_SNIPPET] + "..."
                        rel = os.path.relpath(abs_path, repo_root)
                        hits.append((lineno, snippet))
                        break  # one report per line is enough
    except (OSError, UnicodeDecodeError):
        # Binary / unreadable file; ignore quietly.
        pass
    return hits


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    total = 0
    for dirpath, dirnames, filenames in os.walk(repo_root):
        # Prune excluded dirs in-place for speed.
        dirnames[:] = [d for d in dirnames if not is_excluded(os.path.join(dirpath, d))]
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            if is_excluded(fpath):
                continue
            hits = scan_file(fpath, repo_root)
            for lineno, snippet in hits:
                rel = os.path.relpath(fpath, repo_root)
                print(f"LEAK: {rel}:{lineno}: {snippet}", flush=True)
                total += 1

    if total == 0:
        print("OK: no secrets found", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
