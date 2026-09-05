"""Syntax check the inline JS in conversation-tree/index.html.

Why: A single parse error anywhere in one of the giant inline <script> blocks
aborts the ENTIRE block — `boot()` / `__fxConfig` / all other functions become
'ReferenceError: X is not defined', and the canvas renders empty (grid visible,
zero cards). This is what broke the dsh tree view on 2026-09-05: an
`await confirmDialog(...)` was placed in a non-async keydown callback, producing
`Unexpected identifier 'confirmDialog'` mid-block. `node --check` flags this at
parse time (before any browser sees it). Cost: ~0.2s per block. Catches: missing
braces, await-in-non-async, unclosed template literals, regex typos, etc.

What it does NOT catch: runtime errors (TypeError, ReferenceError) at first
execute — those need a separate boot-flow integration test.

Strategy: extract each <script> block (skipping <script src=...>), run
`node --check` on it, fail the test if any block has syntax errors. Reports
the offending line and the error message so future failures are debuggable
without re-running the JS through a browser.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_HTML = _HERE.parent / "index.html"
_NODE = shutil.which("node")


pytestmark = pytest.mark.skipif(
    _NODE is None,
    reason="node not on PATH (portable Node lives in runtime/node/, not system)",
)


def _extract_script_blocks(html_text: str) -> list[tuple[int, int, str]]:
    """Return [(start_line, end_line, js_text), ...] for each inline <script> block.

    Uses BeautifulSoup to walk <script> tags, which correctly handles
    <script> inside JS string literals (e.g. marked.js's renderer setup
    references '<script>' in plain text — a naive regex would over-split).

    Skips <script src=...> external links (no inline content).
    Skips blocks whose first non-blank line starts with `*` or `//` (likely
    a comment-only block — those are never meant to execute).
    """
    from bs4 import BeautifulSoup  # local import: bs4 is in venv but optional

    soup = BeautifulSoup(html_text, "html.parser")
    blocks: list[tuple[int, int, str]] = []
    for tag in soup.find_all("script"):
        # External script — skip (we only care about inline)
        if tag.get("src"):
            continue
        js_text = tag.string or ""
        if not js_text.strip():
            continue
        # Map soup's byte offsets back to line numbers in the source
        # We use sourceline on the tag, then compute start/end from the
        # tag's source-position. BeautifulSoup's `sourceline` is the line
        # where the tag's opening '<' appears.
        start_line = tag.sourceline or 0
        # End line: sourceline + (number of newlines in the JS text)
        end_line = start_line + js_text.count("\n")
        # Skip comment-only blocks (banner / license / etc.)
        # Only skip blocks whose FIRST non-blank line is itself an entire-line
        # comment like /** ... spanning multiple lines. Don't skip blocks that
        # just START with a one-line block comment like
        # `/* ===== Section ===== */` — those have real JS below.
        first_real = next((x for x in js_text.split("\n") if x.strip()), "")
        stripped = first_real.lstrip()
        last_real = next(
            (x for x in reversed(js_text.split("\n")) if x.strip()), ""
        ).rstrip()
        is_comment_only_banner = (
            # Single-line /** ... */ banner (e.g. license header)
            (stripped.startswith("/**") and stripped.endswith("*/"))
            # Entire block is `// ...` line comments
            or stripped.startswith("//")
            # Multi-line /*\n * body\n */ banner
            or (stripped == "*" and last_real.endswith("*/"))
        )
        if is_comment_only_banner:
            continue
        blocks.append((start_line, end_line, js_text))
    return blocks


def test_no_inline_script_syntax_errors():
    """Every inline <script> block in index.html must parse via node --check."""
    html_text = _HTML.read_text(encoding="utf-8", errors="replace")
    blocks = _extract_script_blocks(html_text)

    # Sanity: we expect a known set of block boundaries (regression guard).
    # L1538-L6765 = main app JS; L6766-L7871 = more app; L7872 = boot()
    # call; L8060 = FX controls. Five blocks total (excluding src= externals).
    assert len(blocks) >= 4, (
        f"expected >=4 inline script blocks, got {len(blocks)}; "
        "index.html structure changed — update this test"
    )

    failures: list[str] = []
    for start_line, end_line, js_text in blocks:
        # node --check reads stdin if no path; pass via tmpfile to capture
        # accurate line numbers
        tmp = _HTML.parent / f".tmp_node_check_{start_line}.js"
        tmp.write_text(js_text, encoding="utf-8")
        try:
            r = subprocess.run(
                [_NODE, "--check", str(tmp)],
                capture_output=True, text=True, timeout=15,
            )
        finally:
            tmp.unlink(missing_ok=True)

        if r.returncode != 0:
            # node's error format: "<file>:<line>\n  <context>\n\nSyntaxError: msg"
            # Translate relative line back to index.html line
            err = r.stderr.strip()
            # Add context for debuggability
            failures.append(
                f"--- L{start_line}-L{end_line} ({end_line-start_line+1} lines) ---\n{err}"
            )

    if failures:
        pytest.fail(
            "Syntax errors in inline <script> blocks of "
            f"E:/Ikaros/core/conversation-tree/index.html:\n\n"
            + "\n\n".join(failures)
            + "\n\n"
            "Fix the JS first; downstream tests depend on this file parsing. "
            "See 'Syntax-checking the giant inline JS block' in skill "
            "ikaros-conversation-tree for the inline extraction recipe."
        )


def test_no_await_in_sync_callbacks():
    """Grep for `await confirmDialog|promptDialog|destructiveConfirm` and verify
    each occurrence is inside an async function context. Prevents the B4 regression
    where `await confirmDialog(...)` was placed in a non-async keydown listener
    callback (parse error → entire main JS block aborts → boot() never runs →
    canvas empty).

    Heuristic: walk forward from each match to find enclosing async/await boundary.
    If we hit a non-async 'function ... {' or '=> {' before the closing brace, flag
    it. Not perfect (can't fully resolve scopes), but catches the obvious
    non-async-function pattern.
    """
    text = _HTML.read_text(encoding="utf-8", errors="replace")
    # Find await <XDialog>( calls
    pat = re.compile(r"\bawait\s+(confirmDialog|promptDialog|destructiveConfirm)\b")
    hits = list(pat.finditer(text))

    assert hits, (
        "no confirmDialog/promptDialog/destructiveConfirm await sites found — "
        "maybe they're all gone, or the regex needs updating"
    )

    bad: list[str] = []
    for m in hits:
        # Map match offset to line number
        line_no = text[: m.start()].count("\n") + 1
        # Walk backward up to 800 chars looking for the nearest enclosing
        # function opener. If it's `function NAME(...) {` without `async`, flag.
        start = max(0, m.start() - 800)
        prefix = text[start : m.start()]
        # Last function/method opener in prefix
        # naive: last "function NAME" or "=> {" before the match
        # match `function NAME(` or `function(` or `=> {`
        func_re = re.compile(
            r"(?:function\s+[A-Za-z_]\w*\s*\([^)]*\)\s*\{|"
            r"function\s*\([^)]*\)\s*\{|"
            r"\)\s*=>\s*\{|"
            r"\b\w+\s*=\s*function\s*\([^)]*\)\s*\{|"
            r"\b\w+\s*\([^)]*\)\s*=>\s*\{)"
        )
        # Find LAST opener
        last_opener = None
        for fm in func_re.finditer(prefix):
            last_opener = fm
        if last_opener is None:
            continue  # can't tell, skip
        opener_text = last_opener.group(0)
        is_async = "async " in prefix[max(0, last_opener.start() - 20) : last_opener.end()]
        if not is_async:
            # Show the surrounding context
            ctx_start = text.rfind("\n", 0, m.start()) + 1
            ctx_end = text.find("\n", m.end())
            if ctx_end == -1:
                ctx_end = len(text)
            snippet = text[ctx_start:ctx_end].strip()
            bad.append(
                f"L{line_no}: await {m.group(1)}() in NON-async context:\n"
                f"    opener='{opener_text}'\n"
                f"    line={snippet[:200]}"
            )

    if bad:
        pytest.fail(
            "Found `await <dialog>()` calls that look like they're in non-async "
            "contexts — these are parse errors (B4 regression on 2026-09-05).\n\n"
            + "\n\n".join(bad)
            + "\n\nFix: change `await confirmDialog(...)` to "
            "`.confirmDialog(...).then(ok => {...})` OR make the enclosing function "
            "`async`. See skill ikaros-conversation-tree §28."
        )