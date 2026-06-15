"""
Network connectivity detection for Hermes Agent.

Provides cached, non-blocking internet connectivity checks so the
routing engine can decide whether to route through local or cloud
LLM providers without hitting a timeout on every request.

Usage::

    from hermes.network import is_online, get_network_status

    if is_online():
        print("Cloud providers are reachable.")
    else:
        print("Running offline — local model only.")

All public functions are synchronous and lightweight (a single HTTP
HEAD request with a short timeout). Results are cached for a
configurable TTL so repeated calls inside a tight loop are cheap.
"""

from __future__ import annotations

import logging
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("hermes.network")

# ---- Tunable constants ----

DEFAULT_TEST_URLS: list[str] = [
    "https://api.openai.com/v1/models",   # fast, tiny response
    "https://www.google.com/generate_204", # Google's no-content ping
    "https://httpbin.org/status/200",
]

DEFAULT_TIMEOUT_SEC: float = 5.0
DEFAULT_CACHE_TTL_SEC: float = 30.0

# ---- Internal state ----

_cache: dict = {
    "online": None,       # bool | None (None = never checked)
    "latency_ms": 0.0,
    "last_check": 0.0,    # epoch seconds
    "error": "",
}


@dataclass
class NetworkStatus:
    """Immutable snapshot of the last connectivity check."""
    online: bool
    latency_ms: float
    last_check_epoch: float
    error: str = ""


def check_connectivity(
    test_urls: Optional[list[str]] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> tuple[bool, float, str]:
    """Perform a *real* connectivity check against one or more URLs.

    Unlike a simple DNS lookup or socket-open test, this actually
    sends an HTTP HEAD request to a public endpoint so it catches
    captive portals, proxy misconfigurations, and split-tunnel VPNs.

    Parameters
    ----------
    test_urls:
        List of URLs to try. Defaults to :data:`DEFAULT_TEST_URLS`.
    timeout:
        Seconds before each individual request is considered failed.

    Returns
    -------
    (online, latency_ms, error):
        ``online`` is ``True`` if **any** URL responded with a 2xx or
        3xx status. ``latency_ms`` is the round-trip time of the
        **first successful** URL. ``error`` is the last error message
        (empty string on success).
    """
    urls = test_urls or DEFAULT_TEST_URLS
    last_error = ""

    for url in urls:
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, method="HEAD")
            req.add_header("User-Agent", "Hermes-NetworkCheck/3.0")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                # Any 2xx/3xx is good enough
                if 200 <= resp.status < 400:
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    return True, round(elapsed_ms, 1), ""
        except urllib.error.HTTPError as e:
            # Some endpoints return 403/404 but we're still "online"
            # (the TCP/TLS handshake succeeded).
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            logger.debug("connectivity check: %s → HTTP %d (online)", url, e.code)
            return True, round(elapsed_ms, 1), ""
        except Exception as e:
            last_error = f"{url}: {e}"
            logger.debug("connectivity check failed: %s", last_error)

    return False, 0.0, last_error


def is_online(cache_ttl_sec: float = DEFAULT_CACHE_TTL_SEC) -> bool:
    """Return ``True`` if the host has internet connectivity.

    Results are cached for ``cache_ttl_sec`` seconds to avoid
    hammering the test endpoints. Pass ``cache_ttl_sec=0`` to force
    a fresh check.

    This is the primary entry point used by the routing engine.
    """
    now = time.time()
    if _cache["online"] is not None and (now - _cache["last_check"]) < cache_ttl_sec:
        return _cache["online"]

    online, latency, err = check_connectivity()
    _cache["online"] = online
    _cache["latency_ms"] = latency
    _cache["last_check"] = now
    _cache["error"] = err
    return online


def get_network_status(force_check: bool = False) -> NetworkStatus:
    """Return a structured snapshot of the current network state.

    If ``force_check`` is ``True`` the cache is bypassed.
    """
    if force_check:
        _cache["online"] = None  # invalidate
        is_online(cache_ttl_sec=0)

    return NetworkStatus(
        online=_cache["online"] or False,
        latency_ms=_cache["latency_ms"],
        last_check_epoch=_cache["last_check"],
        error=_cache.get("error", ""),
    )


def invalidate_cache() -> None:
    """Reset the cached connectivity status.

    Call this after a network change (e.g. VPN connect/disconnect).
    The next call to :func:`is_online` will perform a fresh check.
    """
    _cache["online"] = None
    _cache["last_check"] = 0.0
    _cache["error"] = ""
    logger.info("network cache invalidated")
