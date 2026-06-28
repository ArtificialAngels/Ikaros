"""
Hermes Bridge — monkey-patch sitecustomize.

Windows-only fixes applied at Python interpreter startup.
Copy this file to portable-python/Lib/site-packages/sitecustomize.py to activate.
"""
from __future__ import annotations

import logging
import os
import re
import sys
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
# PATCH 3 — Async delegation completion drain (Artificial Angel Phase 2)
# ============================================================================
#
# The webui broker uses bridge_pool.AgentPool which runs AIAgent directly
# (not through the gateway). The gateway has _async_delegation_watcher but
# the broker path has no equivalent — so background delegate_task() results
# would be lost. This patch adds a drain thread that captures completions
# and injects them into the next chat turn.
#
# IMPORTANT: This patch is fully self-contained (inlined) because at
# sitecustomize time the project's bridge/ directory is NOT in sys.path
# for the broker process. Importing from bridge.completion_drain would
# silently fail. So all drain logic lives here.

def _apply_patch_3_completion_drain() -> bool:
    """Monkey-patch bridge_pool.AgentPool with completion drain.

    Strategy: install BOTH an import hook AND a polling watcher.
    - Import hook (find_spec): fires immediately when bridge_pool is imported
    - Polling watcher: background thread that checks sys.modules every 1s
    Whichever fires first applies the patch; the other becomes a no-op.
    This handles both direct-import and deferred-import scenarios.
    """
    import importlib
    import importlib.abc
    import threading as _threading_mod
    import time as _time_mod

    _patch_applied = [False]
    _pending_completions = {}   # session_key -> list of event dicts
    _pending_lock = _threading_mod.Lock()
    _drain_thread = [None]      # mutable container for closure

    def _drain_loop(interval=2.0):
        """Background loop: drain completion_queue and bucket by session_key."""
        while True:
            try:
                from tools.process_registry import process_registry
                cq = process_registry.completion_queue
                drained = 0
                while not cq.empty():
                    try:
                        evt = cq.get_nowait()
                    except Exception:
                        break
                    evt_type = evt.get("type", "")
                    if evt_type != "async_delegation":
                        try:
                            cq.put(evt)
                        except Exception:
                            pass
                        break
                    session_key = evt.get("session_key", "")
                    if not session_key:
                        continue
                    with _pending_lock:
                        _pending_completions.setdefault(session_key, []).append(evt)
                    drained += 1
                if drained:
                    _log("drained %d completion(s)" % drained)
            except Exception as exc:
                _log("drain loop error: %s" % exc)
            _time_mod.sleep(interval)

    def _start_drain():
        if _drain_thread[0] is not None and _drain_thread[0].is_alive():
            return
        _drain_thread[0] = _threading_mod.Thread(
            target=_drain_loop, name="completion-drain", daemon=True)
        _drain_thread[0].start()
        _log("thread launched")

    def _format_completions(events):
        if not events:
            return ""
        lines = [
            "[IMPORTANT: Background Task Completion(s)]",
            "The following background tasks have completed since the last interaction:",
            ""
        ]
        for evt in events:
            goal = evt.get("goal", "unknown task")
            status = evt.get("status", "unknown")
            summary = evt.get("summary", "")
            error = evt.get("error", "")
            duration = evt.get("duration_seconds", 0)
            lines.append("--- Task: %s ---" % goal[:100])
            lines.append("  Status: %s" % status)
            lines.append("  Duration: %.1fs" % duration)
            if summary:
                lines.append("  Result: %s" % summary[:500])
            if error:
                lines.append("  Error: %s" % error[:200])
            lines.append("")
        lines.append("Please inform the user about these completed tasks concisely.")
        lines.append("[END Background Task Completions]")
        return "\n".join(lines)

    def _do_patch():
        """Apply monkey-patch to bridge_pool.AgentPool. Returns True on success."""
        if _patch_applied[0]:
            return True
        bp = sys.modules.get("bridge_pool")
        if bp is None:
            return False
        try:
            # Patch __init__ to start drain thread
            original_init = bp.AgentPool.__init__
            def _patched_init(self, *args, **kwargs):
                original_init(self, *args, **kwargs)
                _start_drain()
            bp.AgentPool.__init__ = _patched_init

            # Patch _run_chat to inject pending completions + persist to JSONL
            original_run_chat = bp.AgentPool._run_chat

            # Resolve completion log path
            _hermes_home = os.environ.get("HERMES_HOME", "")
            _completion_log = os.path.join(_hermes_home, "completed-tasks.jsonl") if _hermes_home else None

            def _persist_completions(events, session_id):
                """Write completion events to JSONL for later querying."""
                if not _completion_log:
                    return
                import json as _json
                try:
                    with open(_completion_log, "a", encoding="utf-8") as f:
                        for evt in events:
                            record = {
                                "session_id": session_id,
                                "goal": evt.get("goal", ""),
                                "status": evt.get("status", ""),
                                "summary": evt.get("summary", ""),
                                "error": evt.get("error", ""),
                                "duration_seconds": evt.get("duration_seconds", 0),
                                "delegation_id": evt.get("delegation_id", ""),
                                "completed_at": _time_mod.strftime("%Y-%m-%dT%H:%M:%S"),
                            }
                            f.write(_json.dumps(record, ensure_ascii=False) + "\n")
                except Exception as exc:
                    _log("persist error: %s" % exc)

            def _patched_run_chat(self, session, record, message, *args, **kwargs):
                with _pending_lock:
                    pending = _pending_completions.pop(session.session_id, [])
                if pending:
                    # Phase 3: persist to JSONL
                    _persist_completions(pending, session.session_id)
                    ctx = _format_completions(pending)
                    if ctx:
                        if isinstance(message, str):
                            message = ctx + "\n\n" + message
                        elif isinstance(message, dict) and "content" in message:
                            message = {**message, "content": ctx + "\n\n" + str(message.get("content", ""))}
                        else:
                            message = ctx + "\n\n" + str(message)
                        _log("injected %d completion(s) into session %s" % (
                            len(pending), session.session_id))
                return original_run_chat(self, session, record, message, *args, **kwargs)
            bp.AgentPool._run_chat = _patched_run_chat

            _patch_applied[0] = True
            _log("PATCH 3 applied: completion drain for async delegation")
            return True
        except Exception as exc:
            _log("PATCH 3 patch error: %s" % exc)
            return False

    # --- Strategy 1: import hook (fires immediately if bridge_pool is imported) ---
    class _BridgePoolFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname == "bridge_pool" and not _patch_applied[0]:
                try:
                    sys.meta_path.remove(self)
                except ValueError:
                    pass
            return None

    sys.meta_path.insert(0, _BridgePoolFinder())

    # --- Strategy 2: polling watcher (catches cases where import hook misses) ---
    import tempfile as _tempfile_mod
    _debug_log = os.path.join(
        os.environ.get("HERMES_HOME", os.environ.get("TEMP", _tempfile_mod.gettempdir())),
        "..", "logs", "patch3_debug.log"
    )
    # Fallback: write next to sitecustomize.py
    if not os.path.isdir(os.path.dirname(_debug_log)):
        _debug_log = os.path.join(os.path.dirname(__file__), "patch3_debug.log")

    def _log(msg):
        try:
            with open(_debug_log, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n" % (_time_mod.strftime("%H:%M:%S"), msg))
        except Exception:
            pass

    def _poll_for_bridge_pool():
        """Check sys.modules every 1s for bridge_pool, up to 5 minutes."""
        for i in range(300):
            _time_mod.sleep(1.0)
            if "bridge_pool" in sys.modules:
                _log("bridge_pool found at tick %d (pid=%d)" % (i, os.getpid()))
                if _do_patch():
                    _log("patch OK (pid=%d)" % os.getpid())
                    return
                _log("patch FAILED (pid=%d)" % os.getpid())

    _watcher = _threading_mod.Thread(target=_poll_for_bridge_pool,
                                     name="patch3-watcher", daemon=True)
    _watcher.start()

    _log("PATCH 3: lazy finder + polling watcher installed")
    return True


# ============================================================================
# Patch registry
# ============================================================================

_PATCHES: list[Callable[[], bool]] = [
    _apply_patch_1_rawstring,
    _apply_patch_2_terminal_cwd,
    _apply_patch_3_completion_drain,
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
