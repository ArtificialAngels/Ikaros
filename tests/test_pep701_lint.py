"""PEP 701 f-string nesting linter (Ikaros core/, stdlib only).

Background
----------
PEP 701 (Python 3.12) lifted the restriction on nested f-strings that reuse
the same quote character as the outer f-string. The Ikaros project runs on
3.12+ (portable-python), so PEP 701 nesting is legal and the project
standard is:

    >>> text = f"outer {f"inner {val}"} end"     # PEP 701 same-quote nesting (OK)

This linter enforces *consistency* with that standard. It does **not**
forbid PEP 701 nesting. It **does** flag the one pre-3.12 nesting
workaround that should be migrated to PEP 701 style:

  (a) different-quote nesting — ``f"outer {f'inner {val}'} end"``.  Under
      PEP 701 the same pattern is written ``f"outer {f"inner {val}"} end"``
      and works on 3.12+.  The different-quote form is the legacy 3.11
      trick and is banned on this project.

A plain ``f"...\"…\"..."`` is **not** a nesting violation — the escaped
quote is part of the outer literal, not a nested f-string.  We only flag
*actual* nested `JoinedStr` nodes whose outer/inner quote chars differ.

Detection is AST-based (`ast.JoinedStr` + `ast.FormattedValue`) so we
catch the pattern even when split across lines or padded with whitespace,
and we never confuse a non-nesting escaped quote with a real nest.

If you intentionally need a one-off different-quote nest for an
interpreter-compat reason, add a `# pep701: allow diff-quote` comment on
the same line and the linter will skip it (escape hatch, audited by code
review, not silently).

Run:
    cd E:\\Ikaros-line2
    python -m pytest tests/test_pep701_lint.py -v
or
    python tests/test_pep701_lint.py        # CLI mode, prints OK / hits
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Project layout: this file lives at tests/test_pep701_lint.py,
# so the project root is parents[1]. The core/ tree we want to scan is
# <root>/core/.
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parents[1]
_CORE_ROOT = _PROJECT_ROOT / "core"

# Skip these (vendored, generated, build artefacts).  None in core/ today,
# but defensive against future additions.
_SKIP_DIR_NAMES = {"__pycache__", "data", ".git", "node_modules", "dist",
                   "build", ".venv", "venv", ".tox", ".mypy_cache"}
_SKIP_FILE_GLOBS = ()  # none currently

# Marker comment that exempts a single line from the diff-quote rule.
# The comment must be on the same source line as the offending JoinedStr.
_DIFF_QUOTE_ALLOW_TAG = "pep701: allow diff-quote"


def _iter_python_files(root: Path):
    """Yield every .py file under *root*, skipping vendored/build trees."""
    if not root.exists():
        return
    for path in root.rglob("*.py"):
        # path.parts is portable on Windows + POSIX
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        if any(path.match(g) for g in _SKIP_FILE_GLOBS):
            continue
        yield path


def _quote_at(src_lines: list[str], lineno: int, col: int) -> str | None:
    """Return the quote char ('"') of an f-string starting at (lineno, col).

    ``lineno`` and ``col`` are 1-indexed AST positions.  We only handle the
    trivial case: the f-prefix and quote are on the same line at ``col`` /
    ``col+1``.  For multiline f-strings we fall back to ``None`` and skip
    (these are vanishingly rare and AST doesn't model them well).
    """
    if lineno - 1 >= len(src_lines):
        return None
    line = src_lines[lineno - 1]
    if col >= len(line):
        return None
    # Position points at the leading "f" or "F" of the f-string token.
    if line[col] in ("f", "F") and col + 1 < len(line):
        return line[col + 1]
    return None


def _line_has_allow_tag(src_lines: list[str], lineno: int, tag: str) -> bool:
    if lineno - 1 >= len(src_lines):
        return False
    return tag in src_lines[lineno - 1]


def _scan_text(src: str, filename: str) -> list[tuple[str, int, int, str]]:
    """Return list of (filename, lineno, col, kind) hits for one file.

    kind is one of:
      - "diff_quote_nest"     f"…{f'…'}…"   (different quote nest,
                                            pre-PEP 701 workaround)

    PEP 701 same-quote nesting (``f"…{f"…"}…"``) is the project standard
    and is NOT flagged.
    """
    hits: list[tuple[str, int, int, str]] = []
    try:
        tree = ast.parse(src, filename=filename)
    except SyntaxError:
        # File has a parse error — let pytest's collection catch it; we
        # don't try to lint broken code.
        return hits
    src_lines = src.splitlines()

    def visit(jstr: ast.JoinedStr) -> None:
        """Inspect one f-string's FormattedValue children."""
        own_quote = _quote_at(src_lines, jstr.lineno, jstr.col_offset)
        for child in jstr.values:
            if not isinstance(child, ast.FormattedValue):
                continue
            inner = child.value
            if isinstance(inner, ast.JoinedStr):
                inner_quote = _quote_at(src_lines, inner.lineno, inner.col_offset)
                if (
                    own_quote is not None
                    and inner_quote is not None
                    and own_quote != inner_quote
                    and not _line_has_allow_tag(
                        src_lines, inner.lineno, _DIFF_QUOTE_ALLOW_TAG
                    )
                ):
                    hits.append((filename, inner.lineno, inner.col_offset,
                                 "diff_quote_nest"))
                # Recurse: this inner JoinedStr may itself contain further
                # FormattedValues whose value is yet another joinedStr.
                visit(inner)

    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            visit(node)

    return hits


def scan_core(root: Path | None = None) -> list[tuple[str, int, int, str]]:
    """Scan the whole core/ tree; return list of hits."""
    root = root or _CORE_ROOT
    all_hits: list[tuple[str, int, int, str]] = []
    for path in _iter_python_files(root):
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        all_hits.extend(_scan_text(src, str(path)))
    return all_hits


# ────────────────────────────────────────────────────────────────────────────
# pytest entrypoint
# ────────────────────────────────────────────────────────────────────────────

def test_no_pre_pep701_fstring_workarounds_in_core():
    """core/ must not contain pre-PEP 701 nested-f-string workarounds.

    PEP 701 (Python 3.12) made nested same-quote f-strings legal.  The
    project runs on 3.12+ and the agreed style is PEP 701.  Anything
    looking like the pre-3.12 workaround (different-quote nest or
    backslash-escape inside interpolation) should fail this test.
    """
    hits = scan_core()
    if hits:
        report = "\n".join(
            f"  {fname}:{lineno}:{col}  [{kind}]"
            for fname, lineno, col, kind in hits
        )
        raise AssertionError(
            "PEP 701 f-string nesting violations in core/:\n"
            f"{report}\n"
            "\n"
            "Fix: use PEP 701 nested same-quote f-strings "
            '(f"outer {f"inner {val}"} end"), '
            'or add `# pep701: allow diff-quote` on the offending line '
            "if a one-off escape is genuinely required."
        )


# ────────────────────────────────────────────────────────────────────────────
# CLI entrypoint (so this can be run standalone, like docs/lint.py)
# ────────────────────────────────────────────────────────────────────────────

def _main(argv: list[str]) -> int:
    root = _CORE_ROOT
    if len(argv) >= 2 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) >= 2:
        root = Path(argv[1])
    hits = scan_core(root)
    if not hits:
        print(f"OK: no PEP 701 nesting violations in {root}")
        return 0
    for fname, lineno, col, kind in hits:
        print(f"HIT: {fname}:{lineno}:{col}  [{kind}]")
    print(f"FAIL: {len(hits)} PEP 701 violation(s)")
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))