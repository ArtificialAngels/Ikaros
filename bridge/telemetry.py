"""Hermes Bridge - telemetry center.

In-process signal bus + sliding-window request log. Lets every endpoint /
module emit structured events that the signal panel can later aggregate.

Two layers:

1. SignalBus - pub/sub, in-memory, no persistence. Subscribers register a
   callback for a topic; producers call emit(topic, payload). The bus is
   fire-and-forget by design (signals are best-effort, never block the
   request path).

2. RequestLog - ring buffer (default 1000 entries) of structured access
   records. Each entry captures method, path, status, elapsed_ms, payload
   sizes, error (if any). Thread-safe (threading.Lock); designed for both
   async and sync callers.

Persists across requests but does NOT persist across restarts (signals
are ephemeral - restart is itself a "first event").
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional


def _telemetry_path() -> Path:
    base = Path(os.environ.get("HERMES_LOGS") or
                (Path(__file__).resolve().parent.parent / "data" / "logs"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "telemetry.json"


class SignalBus:
    """Topic-based pub/sub for in-process signals.

    Subscribers register fn(topic, payload) -> None for a topic string.
    Producers call emit(topic, payload). Wildcards: subscribe to "*" to
    receive everything.

    Concurrency: subscribers are called synchronously in emit(). Failures
    in one subscriber do NOT affect others - they are isolated.
    """

    def __init__(self, max_history: int = 200) -> None:
        self._subs: Dict[str, List[Callable[[str, dict], None]]] = {}
        self._lock = threading.Lock()
        self._history: Deque[dict] = deque(maxlen=max_history)

    def subscribe(self, topic: str, fn: Callable[[str, dict], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.setdefault(topic, []).append(fn)

        def _unsub() -> None:
            with self._lock:
                if topic in self._subs and fn in self._subs[topic]:
                    self._subs[topic].remove(fn)

        return _unsub

    def emit(self, topic: str, payload: Optional[dict] = None) -> dict:
        """Fire a signal. Returns the envelope that was broadcast."""
        envelope: dict = {
            "id": uuid.uuid4().hex[:16],
            "ts": time.time(),
            "topic": topic,
            "payload": payload or {},
            "source": os.environ.get("HERMES_MODULE", "bridge"),
        }
        with self._lock:
            self._history.append(envelope)
            subs = list(self._subs.get(topic, [])) + list(self._subs.get("*", []))
        for fn in subs:
            try:
                fn(topic, envelope)
            except Exception:
                pass
        return envelope

    def recent(self, topic: Optional[str] = None, limit: int = 50) -> List[dict]:
        with self._lock:
            items = list(self._history)
        if topic:
            items = [e for e in items if e["topic"] == topic or e["topic"].startswith(topic + ".")]
        return items[-limit:]

class RequestLog:
    """Ring-buffer access log with per-path aggregates."""

    def __init__(self, capacity: int = 1000) -> None:
        self._buf: Deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._started = time.time()

    def record(self, entry: dict) -> None:
        entry.setdefault("id", f"req-{uuid.uuid4().hex[:12]}")
        entry.setdefault("ts", time.time())
        entry.setdefault("module", os.environ.get("HERMES_MODULE", "bridge"))
        with self._lock:
            self._buf.append(entry)

    def recent(self, limit: int = 100) -> List[dict]:
        with self._lock:
            return list(self._buf)[-limit:]

    def stats(self) -> dict:
        with self._lock:
            entries = list(self._buf)
        if not entries:
            return {
                "uptime_sec": round(time.time() - self._started, 1),
                "total": 0, "errors": 0, "by_path": {}, "by_status": {},
            }

        total = len(entries)
        errors = sum(1 for e in entries if (e.get("status", 0) or 0) >= 500 or e.get("error"))
        by_status: Dict[str, int] = {}
        by_path_raw: Dict[str, List[float]] = {}
        for e in entries:
            st = e.get("status")
            if st is not None:
                by_status[str(st)] = by_status.get(str(st), 0) + 1
            p = e.get("path", "?")
            by_path_raw.setdefault(p, []).append(float(e.get("elapsed_ms", 0) or 0))

        def _percentile(xs: List[float], pct: float) -> float:
            if not xs:
                return 0.0
            xs = sorted(xs)
            i = max(0, min(len(xs) - 1, int(len(xs) * pct / 100)))
            return round(xs[i], 2)

        by_path: Dict[str, dict] = {}
        for p, xs in by_path_raw.items():
            by_path[p] = {
                "count": len(xs),
                "errors": sum(1 for e in entries if e.get("path") == p and ((e.get("status") or 0) >= 500 or e.get("error"))),
                "p50_ms": _percentile(xs, 50),
                "p95_ms": _percentile(xs, 95),
                "max_ms": round(max(xs), 2),
            }

        return {
            "uptime_sec": round(time.time() - self._started, 1),
            "total": total,
            "errors": errors,
            "error_rate": round(errors / total, 4),
            "by_status": dict(sorted(by_status.items())),
            "by_path": dict(sorted(by_path.items())),
        }

    def flush(self, path: Optional[Path] = None) -> None:
        target = path or _telemetry_path()
        snapshot = {
            "ts": time.time(),
            "stats": self.stats(),
            "recent": self.recent(limit=100),
        }
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, target)
        except OSError:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


# ---- Module-level singletons ----

_bus = SignalBus(max_history=int(os.environ.get("HERMES_TELEMETRY_HISTORY", "200")))
_log = RequestLog(capacity=int(os.environ.get("HERMES_TELEMETRY_CAPACITY", "1000")))


def bus() -> SignalBus:
    return _bus


def log() -> RequestLog:
    return _log


def reset_for_tests() -> None:
    global _bus, _log
    _bus = SignalBus(max_history=200)
    _log = RequestLog(capacity=1000)


class Topics:
    """Canonical signal topics - use these constants to avoid drift."""

    MODULE_BOOT = "module.boot"
    MODULE_READY = "module.ready"
    MODULE_SHUTDOWN = "module.shutdown"
    MODULE_ERROR = "module.error"
    MODEL_LOADED = "model.loaded"
    MODEL_EVICTED = "model.evicted"
    MODEL_SWAP = "model.swap"
    MODEL_WARMUP_START = "model.warmup.start"
    MODEL_WARMUP_PROGRESS = "model.warmup.progress"
    MODEL_WARMUP_DONE = "model.warmup.done"
    CHAT_REQUEST = "chat.request"
    CHAT_DELTA = "chat.delta"
    CHAT_DONE = "chat.done"
    CHAT_ERROR = "chat.error"
    SESSION_OPENED = "session.opened"
    SESSION_CLOSED = "session.closed"
    SUPERVISOR_HEARTBEAT = "supervisor.heartbeat"
    PORT_LISTEN = "port.listen"
    PORT_LOST = "port.lost"
    DISK_LOW = "disk.low"


__all__ = [
    "SignalBus", "RequestLog", "Topics",
    "bus", "log", "reset_for_tests",
]
