"""
Hermes Bridge — Component Health Monitor.

Provides a singleton health registry that any component (bridge, webui,
llm_engine) can report into. The WebUI polls ``/api/bridge/health`` to
get a consolidated view of every running service.

Unlike the bridge's internal ``_llama_health`` dict, this module is
designed to be importable from anywhere without pulling in FastAPI or
httpx dependencies.

Usage::

    from bridge.health import registry

    # Report liveness
    registry.report("llm_engine", alive=True, latency_ms=12.3)

    # Check a component
    status = registry.status("llm_engine")  # {"alive": True, ...}

    # Get full snapshot
    snapshot = registry.snapshot()  # {"llm_engine": {...}, "bridge": {...}}

Components are auto-expired after ``STALE_TIMEOUT_SEC`` seconds of
silence, so a crashed process disappears from the snapshot without
needing an explicit "dead" signal.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


STALE_TIMEOUT_SEC: float = 30.0
"""Seconds of silence before a component is considered dead."""


@dataclass
class _ComponentState:
    alive: bool = False
    last_report: float = 0.0
    latency_ms: float = 0.0
    consecutive_failures: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class HealthRegistry:
    """Thread-safe registry of component health states.

    Thread-safe for concurrent reads/writes from different threads.
    A component that hasn't reported in STALE_TIMEOUT_SEC is
    automatically considered dead when queried via snapshot().
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._components: dict[str, _ComponentState] = {}

    # ---- Public API ----

    def report(
        self,
        name: str,
        *,
        alive: bool = True,
        latency_ms: float = 0.0,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Register or update a component's health state."""
        now = time.time()
        with self._lock:
            comp = self._components.get(name)
            if comp is None:
                comp = _ComponentState()
                self._components[name] = comp
            comp.alive = alive
            comp.last_report = now
            comp.latency_ms = latency_ms
            comp.extra = dict(extra or {})
            if alive:
                comp.consecutive_failures = 0
            else:
                comp.consecutive_failures += 1

    def report_failure(self, name: str, error: str = "") -> None:
        """Shorthand for reporting a component as dead."""
        self.report(name, alive=False, extra={"error": error} if error else {})

    def status(self, name: str) -> dict[str, Any]:
        """Get a single component's status (or empty dict if unknown)."""
        with self._lock:
            comp = self._components.get(name)
            if comp is None:
                return {}
            return self._serialize(name, comp)

    def snapshot(self) -> dict[str, Any]:
        """Return a full snapshot of all known components.

        Components that are stale (no report within STALE_TIMEOUT_SEC)
        are marked as ``alive: false``.
        """
        now = time.time()
        out: dict[str, Any] = {}
        with self._lock:
            for name, comp in list(self._components.items()):
                entry = self._serialize(name, comp)
                if now - comp.last_report > STALE_TIMEOUT_SEC:
                    entry["alive"] = False
                    entry["stale"] = True
                out[name] = entry
        return out

    def is_alive(self, name: str) -> bool:
        """Quick check: is *name* currently alive?"""
        with self._lock:
            comp = self._components.get(name)
            if comp is None:
                return False
            if time.time() - comp.last_report > STALE_TIMEOUT_SEC:
                return False
            return comp.alive

    def known_components(self) -> list[str]:
        """Return list of all component names ever reported."""
        with self._lock:
            return sorted(self._components.keys())

    def reset(self, name: str | None = None) -> None:
        """Remove a component (or all) from the registry."""
        with self._lock:
            if name is None:
                self._components.clear()
            else:
                self._components.pop(name, None)

    # ---- Internal ----

    @staticmethod
    def _serialize(name: str, comp: _ComponentState) -> dict[str, Any]:
        return {
            "name": name,
            "alive": comp.alive,
            "last_report_sec_ago": round(max(0, time.time() - comp.last_report), 1),
            "latency_ms": comp.latency_ms,
            "consecutive_failures": comp.consecutive_failures,
            **comp.extra,
        }


# ---- Singleton ----
registry = HealthRegistry()
