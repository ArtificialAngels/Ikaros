"""
Hermes Bridge — monkey-patch sitecustomize.

Windows-only fixes applied at Python interpreter startup.
Copy this file to portable-python/Lib/site-packages/sitecustomize.py to activate.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Callable

logger = logging.getLogger("hermes_bridge_sitecustomize")


# ============================================================================
# PATCH 1 — Windows path raw-string preprocess
# ============================================================================

def _auto_rawstring_windows_paths(code: str) -> str:
    """Rewrite bare Windows paths in Python code to raw strings.

    Turns parse_excel('E:\\Foo\\bar') into parse_excel(r'E:\\Foo\\bar')
    so Python doesn't choke on \\F \\A \\H as truncated unicode escapes.
    """
    # Match single-quoted strings containing Windows-style paths
    # Pattern: 'X:\\...' or "X:\\..." where X is a drive letter
    pattern = re.compile(
        r"([('\"])"
        r"([A-Za-z]:\\[^'\"\n]+)"
        r"([)'\"])"
    )

    def _replace(m: re.Match) -> str:
        open_quote = m.group(1)
        path = m.group(2)
        close_quote = m.group(3)
        # If already raw (r'...'), skip
        # We only rewrite if the quotes are the same type
        if open_quote in '"\'' and close_quote == open_quote:
            # Return with r prefix inside the quotes
            return f"{open_quote}r{path}{close_quote}"
        return m.group(0)

    # Also handle double-quoted strings
    pattern2 = re.compile(
        r"([=,\(]\s*)"
        r"([\"'])"
        r"([A-Za-z]:\\[^'\"\n]*?)"
        r"\2"
    )

    def _replace2(m: re.Match) -> str:
        prefix = m.group(1)
        quote = m.group(2)
        path = m.group(3)
        # Check if already preceded by r
        if prefix.strip().endswith("r"):
            return m.group(0)
        return f"{prefix}r{quote}{path}{quote}"

    new_code = pattern2.sub(_replace2, code)
    if new_code != code:
        logger.debug("PATCH 1: rewrote %d Windows paths to raw strings", code.count("\\") - new_code.count("\\"))
    return new_code


def _apply_patch_1_rawstring() -> bool:
    """Monkey-patch tools.code_execution_tool.execute_code to preprocess
    Windows paths into raw strings before execution."""
    try:
        from tools import code_execution_tool
    except ImportError:
        logger.debug("PATCH 1: tools.code_execution_tool not importable yet — deferred")
        return False

    _original = code_execution_tool.execute_code

    def _wrapped(code: str, task_id=None, enabled_tools=None) -> str:
        # Only preprocess Python code (not bash, not json, etc.)
        # Heuristic: if it looks like Python with Windows paths
        if re.search(r"[A-Za-z]:\\", code):
            code = _auto_rawstring_windows_paths(code)
        return _original(code, task_id=task_id, enabled_tools=enabled_tools)

    code_execution_tool.execute_code = _wrapped
    logger.info("PATCH 1 applied: Windows path raw-string preprocess")
    return True


# ============================================================================
# PATCH 2 — Windows-cwd-aware terminal wrapper
# ============================================================================

def _is_windows_native_path(path: str) -> bool:
    """Check if path is a Windows-style absolute path."""
    return bool(re.match(r"^[A-Za-z]:\\", path))


def _windows_to_msys_path(win_path: str) -> str:
    """Convert E:\\Foo\\bar to /e/Foo/bar (Git Bash POSIX form)."""
    # Normalize backslashes
    path = win_path.replace("\\", "/")
    # Convert drive letter
    match = re.match(r"^([A-Za-z]):(/.*)?$", path)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2) or ""
        return f"/{drive}{rest}"
    return path


def _apply_patch_2_terminal_cwd() -> bool:
    """Monkey-patch BaseEnvironment.execute to translate Windows cwd
    to Git Bash POSIX form when the backend expects POSIX paths."""
    try:
        from tools.environments import base
    except ImportError:
        logger.debug("PATCH 2: tools.environments.base not importable yet — deferred")
        return False

    _original_execute = base.BaseEnvironment.execute

    def _wrapped_execute(self, command: str, cwd: str = "", *, timeout=None, stdin_data=None, rewrite_compound_background=True) -> dict:
        # Translate Windows cwd to POSIX if backend is Git Bash
        if cwd and _is_windows_native_path(cwd):
            # Check if the command involves Git Bash (heuristic)
            # Git Bash is typically invoked via 'bash' or 'sh' with a Windows path
            # We translate the cwd to MSYS form to be safe
            posix_cwd = _windows_to_msys_path(cwd)
            logger.debug("PATCH 2: translated cwd %s -> %s", cwd, posix_cwd)
            cwd = posix_cwd
        return _original_execute(self, command, cwd=cwd, timeout=timeout, stdin_data=stdin_data, rewrite_compound_background=rewrite_compound_background)

    base.BaseEnvironment.execute = _wrapped_execute
    logger.info("PATCH 2 applied: Windows-cwd terminal wrapper")
    return True


# ============================================================================
# Patch registry
# ============================================================================

_PATCHES: list[Callable[[], bool]] = [
    _apply_patch_1_rawstring,
    _apply_patch_2_terminal_cwd,
]


def apply_all() -> None:
    for patch in _PATCHES:
        name = patch.__name__
        try:
            applied = patch()
        except Exception:
            logger.exception("patch %s raised — skipping", name)
            continue
        if applied:
            logger.info("patch %s applied", name)
        else:
            logger.debug("patch %s skipped (not yet loadable)", name)


apply_all()
