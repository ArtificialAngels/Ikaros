"""bin/fix-eol.py -- Normalize line endings on Hermes-owned .bat/.ps1/.cmd files to CRLF.

Why: AGENTS.md §7 -- cmd.exe does NOT parse LF-only .bat files correctly
(paths with spaces get truncated, scripts fail silently). After every bat
edit, run this tool to guarantee CRLF.

Usage:
    portable-python/python.exe bin/fix-eol.py <file1> [file2] ...
    portable-python/python.exe bin/fix-eol.py --all      (project .bat/.cmd/.ps1)
    portable-python/python.exe bin/fix-eol.py --check <file>  (verify only, no write)

What --all covers (project-owned only, ~16 files):
    bin/*.bat, bin/*.cmd, bin/*.ps1             (14 launchers)
    deps/hermes-env.bat, deps/hermes-env.ps1    (2 dep env scripts)

What --all does NOT cover (intentional):
    deps/node/, deps/llamacpp/, deps/tools/, deps/python-test/
        These are third-party (Node.js, llama.cpp, greenlet) -- their
        scripts stay LF because they are not interpreted by cmd.exe.

Exit codes:
    0 = all files already CRLF (or successfully converted)
    1 = at least one file has wrong line endings and --check was used
    2 = I/O error
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def to_crlf(path: Path) -> tuple:
    """Normalize line endings to CRLF. Returns (was_crlf, new_cr, new_lf)."""
    raw = path.read_bytes()
    # Strip all existing CR/LF, then re-add CRLF consistently
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    crlf = normalized.replace(b"\n", b"\r\n")
    # If the file ended without a newline, preserve that
    had_trailing_newline = raw.endswith(b"\n") or raw.endswith(b"\r")
    if not had_trailing_newline and crlf.endswith(b"\r\n"):
        crlf = crlf[:-2]
    was_crlf = (b"\r\n" in raw)
    new_cr = crlf.count(b"\r")
    new_lf = crlf.count(b"\n")
    path.write_bytes(crlf)
    return (was_crlf, new_cr, new_lf)


def check_crlf(path: Path) -> bool:
    raw = path.read_bytes()
    # File is "CRLF" if it has at least one CRLF and no bare LF outside of CRLF
    if b"\r\n" not in raw:
        return False
    # Check no bare LF (i.e. LF not preceded by CR)
    normalized = raw.replace(b"\r\n", b"")
    return b"\n" not in normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize line endings to CRLF")
    parser.add_argument("files", nargs="*", type=Path, help="Files to normalize")
    parser.add_argument("--all", action="store_true",
                        help="Process Hermes-owned .bat/.cmd/.ps1: bin/* (one level) "
                             "+ deps/hermes-env.{bat,ps1}. Skips third-party node_modules.")
    parser.add_argument("--check", action="store_true",
                        help="Check only, do not write. Exit 1 on wrong line endings.")
    args = parser.parse_args()

    if args.all:
        bin_dir = Path(__file__).resolve().parent
        files = []
        # 1. Project-owned launchers: bin/*.bat, *.cmd, *.ps1 (one level only).
        #    bin/ has no subdirs today, but glob() (not rglob) keeps it future-proof.
        for ext in ("*.bat", "*.cmd", "*.ps1"):
            files.extend(bin_dir.glob(ext))
        # 2. Project-owned dep env scripts. Skip deps/node/, deps/llamacpp/,
        #    deps/tools/, deps/python-test/ -- those are third-party LF scripts.
        deps_dir = bin_dir.parent / "deps"
        if deps_dir.is_dir():
            for name in ("hermes-env.bat", "hermes-env.ps1"):
                p = deps_dir / name
                if p.is_file():
                    files.append(p)
        if not files:
            print("No Hermes-owned .bat / .cmd / .ps1 files found under bin/ or deps/.")
            return 0
    else:
        files = [f for f in args.files if f.suffix.lower() in (".bat", ".cmd", ".ps1")]

    if not files:
        print("No .bat / .cmd / .ps1 files specified. Use --all or pass paths.",
              file=sys.stderr)
        return 0

    failures = []
    fixed = 0
    for f in sorted(files):
        if not f.exists():
            print(f"[SKIP] {f} (not found)")
            continue
        if args.check:
            ok = check_crlf(f)
            if ok:
                print(f"[OK]   {f}")
            else:
                print(f"[FAIL] {f}  (not CRLF)")
                failures.append(f)
        else:
            was, cr, lf = to_crlf(f)
            status = "was-CRLF" if was else "was-LF  -> CRLF"
            print(f"[{status}] {f}  CR={cr} LF={lf}")
            if not was:
                fixed += 1
    if args.check:
        return 1 if failures else 0
    print()
    print(f"Done. {fixed} file(s) fixed, {len(failures)} failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())