"""
Ikaros Desktop Pet — Singleton lock using Windows LockFileEx.

Ensures only one pet process runs at a time, regardless of how it's
launched (main.py directly, detached.py, hermes-pet.bat, HKCU autostart).

Uses OS-level file lock (LockFileEx) — cross-process mutual exclusion.
Lock file path: data/logs/ikaros-pet.lock (configurable via env var
IKAROS_PET_LOCK_PATH).
"""

import os
import sys
import ctypes
import ctypes.wintypes
from datetime import datetime
from pathlib import Path

# Default lock file path (under data/logs/)
_DEFAULT_LOCK_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "logs" / "ikaros-pet.lock"
)

LOCK_PATH = Path(os.environ.get(
    "IKAROS_PET_LOCK_PATH",
    str(_DEFAULT_LOCK_PATH),
))


class IkarosPetLock:
    """Windows file lock for singleton enforcement.

    Usage:
        lock = IkarosPetLock()
        if not lock.acquire():
            print("[FATAL] Another instance is already running.")
            sys.exit(2)
        try:
            # ... run app ...
        finally:
            lock.release()

    The lock file holds the PID and start time for diagnostic purposes.
    """

    # Win32 constants
    GENERIC_READ  = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = -1
    LOCKFILE_EXCLUSIVE_LOCK = 2

    def __init__(self):
        self._handle = None
        self._path = LOCK_PATH

    def acquire(self) -> bool:
        """Acquire exclusive lock.

        Returns True if this instance now holds the lock.
        Returns False if another process already holds the lock.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Create the file if it doesn't exist
        if not self._path.exists():
            try:
                self._path.touch()
            except Exception:
                pass

        # Open with exclusive access (dwShareMode=0 means no sharing)
        handle = ctypes.windll.kernel32.CreateFileW(
            str(self._path),
            self.GENERIC_READ | self.GENERIC_WRITE,  # dwDesiredAccess
            0,            # dwShareMode = 0 — no sharing (exclusive)
            None,         # lpSecurityAttributes
            self.OPEN_EXISTING,
            self.FILE_ATTRIBUTE_NORMAL,
            None,         # hTemplateFile
        )

        if handle == self.INVALID_HANDLE_VALUE:
            return False

        # LockFileEx requires an OVERLAPPED structure
        # OVERLAPPED = 32 bytes (8 fields × 4 bytes = 32 on x64)
        # We use a zeroed-out buffer
        overlapped = (ctypes.c_ubyte * 32)()

        success = ctypes.windll.kernel32.LockFileEx(
            handle,
            self.LOCKFILE_EXCLUSIVE_LOCK,  # dwFlags: exclusive lock
            0,                             # dwReserved
            0, 0,                          # nNumberOfBytesToLockLow/High
            ctypes.byref(overlapped),      # lpOverlapped
        )

        if not success:
            ctypes.windll.kernel32.CloseHandle(handle)
            return False

        # Write PID and timestamp for diagnosis
        try:
            info = f"pid={os.getpid()}\nstarted={datetime.now().isoformat()}\n"
            # WriteFile via kernel32 for low-level file write
            written = ctypes.c_ulong(0)
            ctypes.windll.kernel32.SetFilePointer(handle, 0, None, 0)  # seek to start
            ctypes.windll.kernel32.WriteFile(
                handle,
                info.encode("utf-8"),
                len(info),
                ctypes.byref(written),
                None,
            )
            ctypes.windll.kernel32.SetEndOfFile(handle)  # truncate remainder
        except Exception:
            pass  # best-effort diagnostic write

        self._handle = handle
        return True

    def release(self):
        """Release the lock. Safe to call multiple times."""
        if self._handle is not None:
            try:
                ctypes.windll.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

    def __enter__(self):
        if not self.acquire():
            print(
                f"[FATAL] Another Ikaros Desktop Pet is already running.",
                file=sys.stderr,
            )
            print(
                f"  If you believe this is wrong, delete: {self._path}",
                file=sys.stderr,
            )
            sys.exit(2)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def require_singleton_or_exit() -> IkarosPetLock:
    """Acquire singleton lock or exit with code 2.

    Call this at main.py entry. Returns the lock instance so the caller
    can release() it on shutdown.
    """
    lock = IkarosPetLock()
    if not lock.acquire():
        print(
            "[FATAL] Another Ikaros Desktop Pet is already running.",
            file=sys.stderr,
        )
        print(
            f"  If you believe this is wrong, delete: {LOCK_PATH}",
            file=sys.stderr,
        )
        sys.exit(2)
    return lock
