"""
Hermes Bridge — FastAPI server.

Thin glue layer between EKKOLearnAI `hermes-web-ui` (port :8648) and our local
runtime (llama-server :8080 + upstream hermes-agent v0.16.0 as a library).

Endpoints implemented:
    GET  /health                                — liveness probe (3 signals)
    GET  /debug/config                          — inspect what AIAgent sees (DEBUG=1)
    GET  /v1/signals                            — aggregated telemetry snapshot
    POST /v1/signals/emit                       — broadcast a signal (modules -> bus)
    GET  /v1/signals/recent                     — last N signal envelopes (filter by topic)
    GET  /v1/signals/stats                      — request log aggregates
    GET  /v1/modules                            — online mirror of supervisor --ports
    GET  /v1/inspect/{name}                     — online mirror of supervisor --inspect N
    GET  /v1/models                             — proxy from llama-server, filter .gguf
    POST /v1/models/load                        — router-mode preload (POST {model:...})
    POST /v1/models/swap                        — alias of /load, semantically distinct
    GET  /v1/models/status                      — resident + available + vram snapshot
    POST /v1/models/evict                       — evict LRU / trigger reload-resident
    POST /v1/models/warmup                      — async preload list of models (background thread)
    GET  /v1/models/warmup/{id}                 — poll warmup progress
    POST /v1/chat/completions                   — OpenAI-compat chat (proxy to llama-server)
    POST /v1/chat/completions/sse               — streaming chat (SSE)
    GET  /api/chat/sessions                     — list sessions (upstream hermes_state.SessionDB)
    POST /api/agent/run                         — wrap upstream AIAgent for non-WebUI callers

Every request goes through a timing middleware that emits:
    * one CHAT_REQUEST / CHAT_DONE / CHAT_ERROR signal
    * one entry in the in-process request log (for /v1/signals/stats)
    * one snapshot row in data/logs/telemetry.json (flushed every 30s)

See `bridge/README.md` for the full architecture diagram.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import os
import random
import re
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# Neuro signals (global state for Prompter + Memory)
from bridge.signals import icarus, AI_NAME, HOST_NAME, PATIENCE_DEFAULT

from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from bridge import telemetry
from bridge.health import registry as health_registry
from bridge.voice_server import voice_ws_handler as _voice_ws_handler

logger = logging.getLogger("hermes.bridge")

# Mark this process so signals know where they came from.
os.environ.setdefault("HERMES_MODULE", "bridge")

# ---- Config (all overridable via env vars) ----

# ---- Multi-endpoint fallback for llama-server ----
# "活着" 是第一要义：当主端口 (HERMES_LLAMA_URL) 死了，bridge 自动切到
# 下一个候选端口。环境变量 HERMES_LLAMA_FALLBACKS 逗号分隔，例如：
#   export HERMES_LLAMA_FALLBACKS="http://127.0.0.1:8081,http://127.0.0.1:8082"
# 候选必须互相独立（不同 port，或不同 host）。健康监控每次轮询所有候选，
# 把当前 alive 的第一个选为活跃 base_url；所有候选全死才走 liveness.dead。
_LLAMA_CANDIDATES: list[str] = []
_primary = os.environ.get("HERMES_LLAMA_URL", "http://127.0.0.1:8080").rstrip("/")
_LLAMA_CANDIDATES.append(_primary)
_fb = os.environ.get("HERMES_LLAMA_FALLBACKS", "").strip()
if _fb:
    for u in _fb.split(","):
        u = u.strip().rstrip("/")
        if u and u not in _LLAMA_CANDIDATES:
            _LLAMA_CANDIDATES.append(u)
LLAMA_BASE_URL = _primary  # 兼容老代码，下方 _active_base_url 才是真相
_FALLBACK_FAIL_THRESHOLD = int(os.environ.get("HERMES_BRIDGE_FALLBACK_FAILS", "2"))
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path(__file__).resolve().parent.parent / "data" / "hermes-agent")))
MODELS_DIR = Path(os.environ.get("HERMES_MODELS_DIR", str(Path(__file__).resolve().parent.parent / "data" / "models")))
BRIDGE_PORT = int(os.environ.get("HERMES_BRIDGE_PORT", "7860"))

# ---- Retry & resilience ----
_MAX_RETRIES = int(os.environ.get("HERMES_BRIDGE_MAX_RETRIES", "3"))
_RETRY_BASE_DELAY_MS = float(os.environ.get("HERMES_BRIDGE_RETRY_BASE_MS", "200"))
_RETRY_MAX_DELAY_MS = float(os.environ.get("HERMES_BRIDGE_RETRY_MAX_MS", "5000"))
_RETRYABLE_STATUSES = frozenset({502, 503, 504})


async def _retry_call(coro_factory, label: str = "request") -> Any:
    """Call *coro_factory* with exponential-backoff retry on transient errors.

    *coro_factory* must be an async callable that returns an httpx Response.
    Retries on connection errors, timeouts, and 502/503/504 status codes.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await coro_factory()
            if resp.status_code not in _RETRYABLE_STATUSES:
                return resp
            if attempt < _MAX_RETRIES:
                delay = min(_RETRY_BASE_DELAY_MS * (2 ** attempt) / 1000.0 + random.uniform(0, 0.1), _RETRY_MAX_DELAY_MS / 1000.0)
                logger.warning("bridge %s retry %d/%d (HTTP %d), waiting %.1fs", label, attempt + 1, _MAX_RETRIES, resp.status_code, delay)
                await asyncio.sleep(delay)
            else:
                return resp  # return last 5xx on final attempt
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout) as e:
            last_exc = e
            if attempt < _MAX_RETRIES:
                delay = min(_RETRY_BASE_DELAY_MS * (2 ** attempt) / 1000.0 + random.uniform(0, 0.05), _RETRY_MAX_DELAY_MS / 1000.0)
                logger.warning("bridge %s retry %d/%d (%s), waiting %.1fs", label, attempt + 1, _MAX_RETRIES, type(e).__name__, delay)
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ---- Connection health tracking ----
# Per-candidate health: {url: {alive, consecutive_failures, last_check, last_success, latency_ms}}
_candidate_health: dict[str, dict[str, Any]] = {
    u: {
        "alive": False,
        "consecutive_failures": 0,
        "last_check": 0.0,
        "last_success": 0.0,
        "latency_ms": 0.0,
    }
    for u in _LLAMA_CANDIDATES
}
_health_lock = threading.Lock()
# 当前活跃的 base_url：每个候选 alive 时立即切到候选；不 alive 才保持原样
_active_base_url: str = _primary
_last_active_change: float = 0.0
# 兼容老代码：LLAMA_BASE_URL 改写成"当前活跃"
def _refresh_active_base_url() -> str:
    """Pick the first alive candidate; otherwise keep current. Returns the active url."""
    global _active_base_url
    with _health_lock:
        for u in _LLAMA_CANDIDATES:
            if _candidate_health[u]["alive"]:
                if u != _active_base_url:
                    logger.warning(
                        "bridge: llama-server active endpoint switched %s → %s",
                        _active_base_url, u,
                    )
                    _active_base_url = u
                    _last_active_change = time.time()
                return _active_base_url
    return _active_base_url


def _get_llama_health() -> dict[str, Any]:
    """Back-compat shape: aggregate over the active candidate + per-candidate breakdown."""
    with _health_lock:
        active = _active_base_url
        per = {u: dict(v) for u, v in _candidate_health.items()}
    agg = per.get(active, {})
    return {
        "alive": agg.get("alive", False),
        "last_check": agg.get("last_check", 0.0),
        "last_success": agg.get("last_success", 0.0),
        "consecutive_failures": agg.get("consecutive_failures", 0),
        "latency_ms": agg.get("latency_ms", 0.0),
        "active_url": active,
        "candidates": per,
    }


async def _check_llama_health() -> bool:
    """Probe every candidate; pick the first alive as active. Returns aggregate alive."""
    global _active_base_url
    any_alive = False
    now = time.time()

    async def _probe_one(url: str) -> tuple[str, bool, float]:
        t0 = time.perf_counter()
        try:
            # use a fresh client per-candidate because _http's base_url is fixed
            async with httpx.AsyncClient(base_url=url, timeout=3.0) as cli:
                r = await cli.get("/health")
            ok = r.status_code == 200
        except Exception:
            ok = False
        return url, ok, (time.perf_counter() - t0) * 1000.0

    results = await asyncio.gather(*[_probe_one(u) for u in _LLAMA_CANDIDATES], return_exceptions=False)

    with _health_lock:
        for url, ok, latency in results:
            ch = _candidate_health[url]
            ch["last_check"] = now
            if ok:
                ch["alive"] = True
                ch["last_success"] = now
                ch["consecutive_failures"] = 0
                ch["latency_ms"] = round(latency, 1)
                any_alive = True
            else:
                ch["consecutive_failures"] += 1
                ch["latency_ms"] = 0.0
                if ch["consecutive_failures"] >= _FALLBACK_FAIL_THRESHOLD:
                    ch["alive"] = False

        # Re-pick active: only switch when the CURRENT active is dead. Otherwise
        # stay sticky to the current active (manual switch survives across health
        # checks). 切端口不能让 Icarus 休眠 — manual switch wins over auto-pick.
        if not _candidate_health.get(_active_base_url, {}).get("alive", False):
            for u in _LLAMA_CANDIDATES:
                if _candidate_health[u]["alive"]:
                    if u != _active_base_url:
                        logger.warning(
                            "bridge: llama-server active endpoint auto-failed-over %s → %s (current dead)",
                            _active_base_url, u,
                        )
                        _active_base_url = u
                        _last_active_change = now
                    break

    # Cross-component visibility: report the active candidate as "llama_server"
    active_health = _candidate_health.get(_active_base_url, {})
    health_registry.report(
        "llama_server",
        alive=active_health.get("alive", False),
        latency_ms=active_health.get("latency_ms", 0.0),
        extra={
            "active_url": _active_base_url,
            "candidates_alive": sum(1 for v in _candidate_health.values() if v["alive"]),
            "candidates_total": len(_LLAMA_CANDIDATES),
        },
    )
    health_registry.report("bridge", alive=True, extra={"port": BRIDGE_PORT, "pid": os.getpid()})

    return any_alive


# ---- Background health monitor ----
_health_monitor_stop = threading.Event()
_health_monitor_interval = float(os.environ.get("HERMES_BRIDGE_HEALTH_INTERVAL_SEC", "10"))


def _start_health_monitor() -> None:
    """Background thread that periodically probes llama-server health."""
    async def _probe_loop():
        while not _health_monitor_stop.is_set():
            try:
                await _check_llama_health()
            except Exception:
                pass
            _health_monitor_stop.wait(_health_monitor_interval)

    def _run():
        asyncio.run(_probe_loop())

    t = threading.Thread(target=_run, daemon=True, name="bridge-health-monitor")
    t.start()


def _stop_health_monitor() -> None:
    _health_monitor_stop.set()


# Single shared HTTP client for proxying to llama-server. The base_url is
# refreshed on every request from _active_base_url so that chat traffic
# always reaches whichever llama-server candidate is currently alive.
# "活着" 是第一要义: 切端口不影响 Icarus 持续在线。
def _current_base_url() -> str:
    return _active_base_url


_http = httpx.AsyncClient(
    base_url=_current_base_url(),
    timeout=httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=5.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)
_warmup_http_client = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=5.0))


async def _proxy_to_active(method: str, path: str, **kwargs) -> httpx.Response:
    """Send an HTTP request to the *current* active llama-server, picking the
    alive candidate at call-time. If a request fails with a connection error,
    we transparently retry against any other alive candidate once before
    surfacing the error. This is the "活着回退" hot-path.
    """
    last_exc: Exception | None = None
    tried: set[str] = set()
    # Try the active one first, then any other alive candidate as fallback.
    with _health_lock:
        order = [_active_base_url] + [u for u in _LLAMA_CANDIDATES if u != _active_base_url]
    for url in order:
        if url in tried:
            continue
        tried.add(url)
        # Skip known-dead candidates (unless it's the only one, in which case try anyway)
        if _candidate_health.get(url, {}).get("alive") is False and len([u for u in _LLAMA_CANDIDATES if u not in tried]) > 0:
            continue
        try:
            async with httpx.AsyncClient(
                base_url=url,
                timeout=httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=5.0),
            ) as cli:
                resp = await cli.request(method, path, **kwargs)
                return resp
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            last_exc = e
            logger.warning("bridge: %s %s via %s failed: %s", method, path, url, type(e).__name__)
            continue
    if last_exc:
        raise last_exc
    raise RuntimeError("no llama-server candidates available")

# ---- Bridge-to-WebUI heartbeat ----
# The WebUI can poll /health to verify the bridge is alive, and the bridge
# self-reports its own component health in the response. This keeps the
# WebUI's topology panel accurate even when the supervisor process is gone.
_heartbeat_sequence: int = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    telemetry.bus().emit(telemetry.Topics.MODULE_BOOT, {
        "module": "bridge",
        "version": "0.5.0",
        "port": BRIDGE_PORT,
        "pid": os.getpid(),
    })
    # Start background health monitor
    _start_health_monitor()
    # Do an initial health check immediately
    await _check_llama_health()
    # ---- Neuro (Prompter + Memory) startup ----
    # Mark LLM as ready so Prompter 100ms tick starts firing decisions
    icarus.llm_ready = True
    try:
        from bridge.neuro import get_memory
        memory = get_memory()
        # Spawn memory reflection loop (every 20 messages triggers a self-summary)
        asyncio.create_task(memory.run())
        logger.info("NEURO: memory reflection loop started")
    except Exception as exc:
        logger.warning(f"NEURO: memory init failed: {exc}")
    try:
        from bridge.prompter import get_prompter
        prompter = get_prompter()
        prompter.start()
        logger.info("NEURO: prompter started (100ms tick + PATIENCE)")
    except Exception as exc:
        logger.warning(f"NEURO: prompter init failed: {exc}")
    logger.info("bridge v0.5.0 started — llama=%s neuro=on", _get_llama_health()["alive"])
    yield
    icarus.terminate = True
    _stop_health_monitor()
    telemetry.bus().emit(telemetry.Topics.MODULE_SHUTDOWN, {"module": "bridge"})
    try:
        telemetry.log().flush(background=True)
    except Exception:
        pass
    _warmup_http_client.close()
    await _http.aclose()


app = FastAPI(
    title="Hermes Bridge",
    version="0.5.0",
    lifespan=lifespan,
    description=(
        "Robust glue layer between EKKOLearnAI hermes-web-ui (port :8648) and "
        "our local llama-server (port :8080) + upstream NousResearch "
        "hermes-agent v0.16.0. Features: health monitoring, retry logic, "
        "graceful degradation. See bridge/README.md."
    ),
)


# ---- Telemetry middleware ----
# Records every request into the RequestLog ring buffer and emits a signal.
# Runs on both async (FastAPI) and sync paths; never raises.

_TELEMETRY_FLUSH_EVERY_S = 30.0
_last_flush = [0.0]
_flush_lock = threading.Lock()


@app.middleware("http")
async def _telemetry_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    req_bytes = int(request.headers.get("content-length", "0") or 0)
    method = request.method
    path = request.url.path
    status = 500
    err: str | None = None
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    except Exception as e:
        err = repr(e)
        status = 500
        raise
    finally:
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        # Build a single record once; reuse for log + signal.
        record = {
            "method": method,
            "path": path,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "req_bytes": req_bytes,
            "resp_bytes": 0,
            "error": err,
            "client": request.client.host if request.client else None,
        }
        try:
            telemetry.log().record(record)
        except Exception:
            pass
        try:
            topic = telemetry.Topics.CHAT_REQUEST if path.startswith("/v1/chat") else telemetry.Topics.MODULE_BOOT
            if status >= 500:
                topic = telemetry.Topics.CHAT_ERROR if path.startswith("/v1/chat") else telemetry.Topics.MODULE_ERROR
            elif status < 400 and path.startswith("/v1/chat"):
                topic = telemetry.Topics.CHAT_DONE
            telemetry.bus().emit(topic, {**record, "ts_emit": time.time()})
        except Exception:
            pass
        # Best-effort periodic flush of the telemetry snapshot file.
        try:
            now = time.time()
            if now - _last_flush[0] >= _TELEMETRY_FLUSH_EVERY_S:
                with _flush_lock:
                    if now - _last_flush[0] >= _TELEMETRY_FLUSH_EVERY_S:
                        telemetry.log().flush(background=True)
                        _last_flush[0] = now
        except Exception:
            pass


# ---- /health ----

@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness + readiness probe with full component health.

    Returns bridge self-health, llama-server status, hermes-agent
    availability, and disk state. The WebUI polls this every few
    seconds to maintain its topology view.
    """
    # Use cached health to avoid hammering llama-server on every poll.
    # The background monitor keeps this fresh every ~10s.
    llama_health = _get_llama_health()

    # Check hermes-agent importability (memoized — only runs once).
    agent_importable = _check_agent_importable()

    hermes_home_writable = HERMES_HOME.exists() and os.access(HERMES_HOME, os.W_OK)
    models_dir_exists = MODELS_DIR.is_dir()
    gguf_count = len(list(MODELS_DIR.glob("*.gguf"))) if models_dir_exists else 0

    global _heartbeat_sequence
    _heartbeat_sequence += 1

    payload = {
        "status": "ok" if llama_health["alive"] else "degraded",
        "version": "0.5.0",
        "heartbeat_seq": _heartbeat_sequence,
        "pid": os.getpid(),
        "uptime_sec": round(time.time() - telemetry.log()._start_time if hasattr(telemetry.log(), "_start_time") else 0, 1),
        "components": {
            "llama_server": {
                "url": LLAMA_BASE_URL,
                "alive": llama_health["alive"],
                "latency_ms": llama_health["latency_ms"],
                "consecutive_failures": llama_health["consecutive_failures"],
                "last_success_ago_sec": round(time.time() - llama_health["last_success"], 1) if llama_health["last_success"] else None,
            },
            "hermes_agent": {
                "importable": agent_importable,
                "home_path": str(HERMES_HOME),
                "home_exists": HERMES_HOME.exists(),
                "home_writable": hermes_home_writable,
                "config_exists": (HERMES_HOME / "config.yaml").exists(),
            },
            "models": {
                "dir": str(MODELS_DIR),
                "exists": models_dir_exists,
                "gguf_count": gguf_count,
            },
            "bridge": {
                "alive": True,
                "port": BRIDGE_PORT,
                "websocket_clients": len(_ws_clients),
            },
        },
        "endpoints": [
            "/health",
            "/v1/signals", "/v1/signals/recent", "/v1/signals/stats",
            "/v1/signals/emit", "/v1/signals/ws",
            "/v1/modules", "/v1/inspect/{name}",
            "/v1/models", "/v1/models/load", "/v1/models/swap",
            "/v1/models/status", "/v1/models/evict",
            "/v1/models/warmup", "/v1/models/warmup/{warmup_id}",
            "/v1/chat/completions", "/v1/chat/completions/sse",
            "/api/chat/sessions", "/api/agent/run",
            "/api/bridge/health",
        ],
    }
    # Side-effect: emit PORT_LISTEN signal on every health probe.
    if llama_health["alive"]:
        telemetry.bus().emit(telemetry.Topics.PORT_LISTEN, {
            "module": "llm_engine",
            "url": LLAMA_BASE_URL,
        })
    return payload


# ---- Agent importability cache ----
_agent_importable_cache: dict[str, Any] = {"checked": False, "importable": False, "error": ""}


def _check_agent_importable() -> bool:
    """Memoized check: can we import AIAgent from run_agent?"""
    if _agent_importable_cache["checked"]:
        return _agent_importable_cache["importable"]
    _agent_importable_cache["checked"] = True
    try:
        from run_agent import AIAgent  # noqa: F401
        _agent_importable_cache["importable"] = True
    except ImportError as e:
        _agent_importable_cache["importable"] = False
        _agent_importable_cache["error"] = str(e)
    return _agent_importable_cache["importable"]


# ---- Dedicated bridge health endpoint (lighter than /health) ----
@app.get("/api/bridge/health")
async def bridge_health() -> dict[str, Any]:
    """Lightweight health check for the WebUI's heartbeat poller.

    Returns only bridge + llama alive status. Faster than /health
    because it skips hermes-agent import and disk checks.
    """
    h = _get_llama_health()
    return {
        "bridge": "ok",
        "llama_server": "ok" if h["alive"] else "degraded",
        "latency_ms": h["latency_ms"],
        "heartbeat_seq": _heartbeat_sequence,
        "ts": time.time(),
    }


@app.get("/api/bridge/health/snapshot")
async def health_snapshot() -> dict[str, Any]:
    """Full health snapshot of all registered components.

    Uses the shared HealthRegistry singleton. Any module that imports
    ``bridge.health.registry`` can report its status and appear here.
    """
    return {
        "ts": time.time(),
        "components": health_registry.snapshot(),
    }


# ---- /v1/liveness (Icarus is "alive" = at least one chat-capable provider reachable) ----
#
# Why this endpoint exists
# ------------------------
# The watchdog + icarus-remember pipeline need a single, trustworthy answer
# to "can Icarus talk to a model right now?"  The /health endpoint reports
# 30+ fields and skips external network calls, so it can't tell the
# difference between "llama-server is up but every chat times out" and
# "everything is fine". /v1/liveness actively pings each known provider
# and returns a 3-state verdict:
#
#   status="ok"       — at least one provider (local OR cloud) answered
#   status="degraded" — providers reachable but auth/format errors
#   status="dead"     — no provider answered within timeout
#
# Watchdog writes this verdict into the heartbeat. icarus-remember
# surfaces it in the daily memory narrative.  icarus-self-explore score
# can dock points if the verdict stays "dead" for too long.
#
# Probes (run in parallel, 4s timeout each)
# -----------------------------------------
#   1. Local:        GET  <LLAMA_BASE_URL>/v1/models
#   2. minimax-cn:   GET  https://api.minimaxi.com/anthropic/v1/models
#                    (cheap, no auth required for the model list)
#   3. deepseek:     GET  https://api.deepseek.com/v1/models
#   4. openai:       GET  https://api.openai.com/v1/models
#   5. anthropic:    GET  https://api.anthropic.com/v1/models
#                    (Anthropic doesn't expose /v1/models — use /v1/messages
#                    with max_tokens=1 instead, see _probe_anthropic)
#
# Cloud providers are hard-coded here (not loaded from config) because
# liveness is a fixed concept — the same 4-5 providers any user might
# have configured. If a user runs a private proxy, the liveness score
# only reflects the public clouds it knows about.

_LIVENESS_PROBE_TIMEOUT_S = 4.0
_LIVENESS_CLOUD_PROVIDERS: dict[str, str] = {
    "minimax-cn": "https://api.minimaxi.com/anthropic/v1/models",
    "deepseek": "https://api.deepseek.com/v1/models",
    "openai": "https://api.openai.com/v1/models",
}


async def _probe_local_llama() -> dict[str, Any]:
    """Probe local llama-server. Routes through _proxy_to_active so it always
    hits whichever candidate is currently alive (port-switch survival)."""
    t0 = time.monotonic()
    try:
        r = await _proxy_to_active("GET", "/v1/models", timeout=_LIVENESS_PROBE_TIMEOUT_S)
        latency = round((time.monotonic() - t0) * 1000, 1)
        if r.status_code == 200:
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            ids = [m.get("id") for m in (data.get("data") or [])]
            return {
                "alive": True, "status": 200, "latency_ms": latency,
                "model_count": len(ids), "note": f"{len(ids)} chat model(s) available",
                "active_url": _active_base_url,
            }
        return {"alive": False, "status": r.status_code, "latency_ms": latency,
                "error": f"HTTP {r.status_code}", "active_url": _active_base_url}
    except Exception as e:
        return {"alive": False, "status": 0, "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "error": f"{type(e).__name__}: {e}", "active_url": _active_base_url}


async def _probe_cloud_get(url: str) -> dict[str, Any]:
    """Generic GET probe (no auth — some providers expose /v1/models publicly)."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_LIVENESS_PROBE_TIMEOUT_S) as c:
            r = await c.get(url)
        latency = round((time.monotonic() - t0) * 1000, 1)
        alive = r.status_code in (200, 401, 403)  # 401/403 = reachable, just unauth
        return {
            "alive": alive, "status": r.status_code, "latency_ms": latency,
            "error": None if alive else f"HTTP {r.status_code}",
        }
    except Exception as e:
        return {"alive": False, "status": 0, "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "error": f"{type(e).__name__}: {e}"}


async def _probe_cloud_post(url: str, body: dict, headers: dict) -> dict[str, Any]:
    """Generic POST probe (used when GET isn't supported)."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_LIVENESS_PROBE_TIMEOUT_S) as c:
            r = await c.post(url, json=body, headers=headers)
        latency = round((time.monotonic() - t0) * 1000, 1)
        alive = r.status_code in (200, 401, 403)
        return {
            "alive": alive, "status": r.status_code, "latency_ms": latency,
            "error": None if alive else f"HTTP {r.status_code}",
        }
    except Exception as e:
        return {"alive": False, "status": 0, "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "error": f"{type(e).__name__}: {e}"}


@app.get("/v1/liveness")
async def liveness() -> dict[str, Any]:
    """Composite liveness: at least one provider reachable → "ok".

    Returns:
      {
        "status": "ok" | "degraded" | "dead",
        "ts": <unix>,
        "local":  { "alive": bool, "latency_ms": float, ... },
        "cloud":  { "<provider>": { ... }, ... },
        "summary": "local up, 2/3 clouds up"
      }
    """
    # Run all probes in parallel.
    tasks: dict[str, Any] = {"local": asyncio.create_task(_probe_local_llama())}
    for name, url in _LIVENESS_CLOUD_PROVIDERS.items():
        tasks[f"cloud.{name}"] = asyncio.create_task(_probe_cloud_get(url))
    # Anthropic: probe with a minimal /v1/messages POST (they don't expose /v1/models).
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        tasks["cloud.anthropic"] = asyncio.create_task(_probe_cloud_post(
            "https://api.anthropic.com/v1/messages",
            {"model": "claude-3-5-haiku-20241022", "max_tokens": 1, "messages": [{"role": "user", "content": "x"}]},
            {"x-api-key": anthropic_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        ))
    # Collect.
    results: dict[str, Any] = {}
    for name, task in tasks.items():
        try:
            results[name] = await task
        except Exception as e:
            results[name] = {"alive": False, "status": 0, "latency_ms": 0.0,
                             "error": f"probe crashed: {type(e).__name__}: {e}"}

    # Decide.
    local_alive = results.get("local", {}).get("alive", False)
    cloud_alive_count = sum(1 for k, v in results.items()
                            if k.startswith("cloud.") and v.get("alive"))
    cloud_total = sum(1 for k in results if k.startswith("cloud."))

    if local_alive and cloud_alive_count == cloud_total and cloud_total > 0:
        status = "ok"
    elif local_alive or cloud_alive_count > 0:
        status = "ok"  # at least one — Icarus is alive
    else:
        status = "dead"

    if local_alive and cloud_alive_count > 0 and cloud_alive_count < cloud_total:
        # surface as "degraded" only if we have at least 2 configured and one is down
        if cloud_total >= 2 and cloud_alive_count < cloud_total:
            status = "degraded"

    # Side-effect: emit liveness signal for downstream consumers.
    sig_topic = {
        "ok": telemetry.Topics.LIVENESS_OK,
        "degraded": telemetry.Topics.LIVENESS_DEGRADED,
        "dead": telemetry.Topics.LIVENESS_DEAD,
    }[status]
    telemetry.bus().emit(sig_topic, {
        "local_alive": local_alive,
        "cloud_alive": cloud_alive_count,
        "cloud_total": cloud_total,
    })

    summary = []
    if local_alive:
        lh = results["local"]
        # Tell the operator which base_url is currently active
        active_url = lh.get("active_url") or _active_base_url
        summary.append(
            f"local llama up ({lh.get('model_count', 0)} models, {lh.get('latency_ms', 0):.0f}ms, via {active_url})"
        )
    else:
        # Report candidate breakdown so the operator can see which ports died
        cand = _get_llama_health().get("candidates", {})
        alive = [u.rsplit(':', 1)[-1] for u, v in cand.items() if v.get("alive")]
        summary.append(
            f"local llama DOWN (candidates: {','.join(alive) or 'none alive'} of {len(cand)})"
        )
    summary.append(f"cloud: {cloud_alive_count}/{cloud_total} up")

    return {
        "status": status,
        "ts": time.time(),
        "local": results.get("local"),
        "cloud": {k.removeprefix("cloud."): v for k, v in results.items() if k.startswith("cloud.")},
        "summary": " · ".join(summary),
        "llama_candidates": _get_llama_health().get("candidates", {}),
        "llama_active": _active_base_url,
    }


# ---- /v1/signals (telemetry aggregation) ----

_signals_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_SIGNALS_CACHE_TTL_S = 1.0  # cache for 1 second to avoid hammering llama-server


@app.get("/v1/signals")
async def signals_snapshot() -> dict[str, Any]:
    """Single-source aggregated telemetry snapshot.

    Combines in-process bus recent events, request log stats, llama-server
    /v1/models status, and runtime env (PID, ports). This is the primary
    endpoint the signal panel reads from.
    """
    # Return cached data if fresh
    now = time.time()
    if _signals_cache["data"] and now - _signals_cache["ts"] < _SIGNALS_CACHE_TTL_S:
        return _signals_cache["data"]

    # Probe llama-server in parallel-style (sequential async is fine — both
    # calls have short timeouts). Use cached health when available.
    llama_health = _get_llama_health()
    llama_alive = llama_health["alive"]
    llama_models_count = 0
    try:
        r = await _retry_call(
            lambda: _http.get("/v1/models", timeout=2.0),
            label="signals-models",
        )
        if r.status_code == 200:
            llama_models_count = len(r.json().get("data", []) or [])
    except Exception:
        # Fall back to local scan for models count
        llama_models_count = len(list(MODELS_DIR.glob("*.gguf"))) if MODELS_DIR.is_dir() else 0

    stats = telemetry.log().stats()
    recent = telemetry.bus().recent(limit=20)
    result = {
        "ts": time.time(),
        "module": "bridge",
        "pid": os.getpid(),
        "uptime_sec": stats.get("uptime_sec"),
        "endpoints": [
            "/health", "/v1/signals", "/v1/signals/recent",
            "/v1/signals/stats", "/v1/signals/emit",
            "/v1/models", "/v1/models/load", "/v1/models/swap",
            "/v1/models/status", "/v1/models/evict",
            "/v1/models/warmup", "/v1/models/warmup/{warmup_id}",
            "/v1/chat/completions", "/v1/chat/completions/sse",
            "/api/chat/sessions", "/api/agent/run",
        ],
        "upstream": {
            "llama_server": {
                "url": LLAMA_BASE_URL,
                "alive": llama_alive,
                "models_count": llama_models_count,
            },
            "hermes_home": {
                "path": str(HERMES_HOME),
                "exists": HERMES_HOME.exists(),
                "writable": HERMES_HOME.exists() and os.access(HERMES_HOME, os.W_OK),
            },
        },
        "request_stats": {
            "total": stats["total"],
            "errors": stats["errors"],
            "error_rate": stats["error_rate"],
            "by_status": stats["by_status"],
            "top_paths": sorted(
                [{"path": p, **v} for p, v in stats["by_path"].items()],
                key=lambda x: x["count"], reverse=True,
            )[:10],
        },
        "recent_signals": recent,
        "warmup_queue_size": len(_warmup_tasks),
    }
    _signals_cache["ts"] = now
    _signals_cache["data"] = result
    return result


@app.get("/v1/signals/recent")
async def signals_recent(topic: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Return recent signal envelopes, optionally filtered by topic prefix."""
    items = telemetry.bus().recent(topic=topic, limit=max(1, min(limit, 500)))
    return {"count": len(items), "items": items, "topic": topic}


@app.get("/v1/signals/stats")
async def signals_stats() -> dict[str, Any]:
    """Return request-log aggregates only (lighter than /v1/signals)."""
    return telemetry.log().stats()


@app.post("/v1/signals/emit")
async def signals_emit(request: Request) -> dict[str, Any]:
    """Publish a signal into the in-process bus.

    Body: {"topic": "...", "payload": {...}}. The bus adds id/ts/source.
    Used by other modules (llm_engine, webui, env_bootstrap) to announce
    state changes (model.loaded, session.opened, ...). Persistent callers
    can also pass {"topic": "...", "payload": {...}, "persist": true} which
    writes the envelope to data/logs/telemetry.json immediately.
    """
    body = await request.json()
    topic = body.get("topic")
    if not topic or not isinstance(topic, str):
        raise HTTPException(status_code=400, detail="`topic` field is required (string)")
    payload = body.get("payload") or {}
    persist = bool(body.get("persist", False))
    env = telemetry.bus().emit(topic, payload)
    if persist:
        try:
            telemetry.log().flush(background=True)
        except Exception:
            pass
    return {"ok": True, "envelope": env}


# ---- WebSocket: real-time signal push ----

_ws_clients: set[WebSocket] = set()
_ws_lock = threading.Lock()


@app.websocket("/v1/signals/ws")
async def signals_ws(websocket: WebSocket, topic: str = ""):
    """WebSocket endpoint for real-time signal push.

    Query param: ?topic=model.loaded (optional, defaults to all topics)
    Sends JSON envelopes as they are emitted. Client can send "ping" to keep alive.
    """
    await websocket.accept()
    with _ws_lock:
        _ws_clients.add(websocket)

    # Subscribe to bus for real-time forwarding
    def _on_signal(sig_topic: str, envelope: dict):
        if topic and sig_topic != topic and not sig_topic.startswith(topic + "."):
            return
        msg = json.dumps(envelope, ensure_ascii=False)
        try:
            asyncio.get_event_loop().create_task(websocket.send_text(msg))
        except Exception:
            pass

    unsub = telemetry.bus().subscribe("*", _on_signal)

    try:
        while True:
            # Keep connection alive, receive pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        unsub()
        with _ws_lock:
            _ws_clients.discard(websocket)


# ---- /v1/modules (online mirror of `hermes-supervisor.py --ports`) ----
#
# Scans modules/*/module.json at request time (with a short in-process cache)
# so the bridge can answer "what does each module listen on" without needing
# the supervisor process to be running. The bridge is the canonical runtime
# bus, so this is where every panel / dashboard / upstream caller asks.

import socket as _socket
import asyncio as _asyncio
import time as _time

_modules_cache: dict[str, Any] = {"ts": 0.0, "modules": []}
_MODULES_TTL_S = 2.0


async def _tcp_alive(host: str, port: int, timeout_s: float = 0.5) -> bool:
    """Non-blocking async TCP probe."""
    try:
        reader, writer = await _asyncio.wait_for(
            _asyncio.open_connection(host, port), timeout=timeout_s
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def _scan_module_json() -> list[dict[str, Any]]:
    """Read modules/*/module.json and return a flat list of port-contract dicts.

    Mirrors (but does not import) bin/hermes-supervisor.py discover_modules().
    Kept independent so the bridge can run without the supervisor on PYTHONPATH.
    Cached for _MODULES_TTL_S seconds; the live TCP probe is *always* fresh.
    """
    now = _time.time()
    if now - _modules_cache["ts"] < _MODULES_TTL_S and _modules_cache["modules"]:
        return _modules_cache["modules"]

    root = Path(__file__).resolve().parent.parent
    modules_dir = root / "modules"
    if not modules_dir.is_dir():
        return []

    out: list[dict[str, Any]] = []
    for entry in sorted(modules_dir.iterdir()):
        if not entry.is_dir():
            continue
        json_path = entry / "module.json"
        if not json_path.is_file():
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            out.append({
                "name": entry.name,
                "error": f"bad module.json: {e}",
                "port": None,
                "alive": False,
            })
            continue

        net = data.get("network") or {}
        port = net.get("port")
        host = net.get("host", "127.0.0.1")
        depends = data.get("depends_on") or []
        alive = bool(port) and await _tcp_alive(host, int(port)) if port else False
        out.append({
            "name": data.get("name", entry.name),
            "version": str(data.get("version", "")),
            "description": data.get("description", ""),
            "type": data.get("type", "service"),
            "runtime_kind": (data.get("runtime") or {}).get("kind", ""),
            "port": port,
            "host": host,
            "protocol": net.get("protocol", "http"),
            "endpoint": f"{net.get('protocol', 'http')}://{host}:{port}" if port else None,
            "health_url": f"http://{host}:{port}{net.get('health_endpoint', '/health')}" if port else None,
            "health_endpoint": net.get("health_endpoint", "/health"),
            "health_timeout_ms": int(net.get("health_timeout_ms", 5000)),
            "depends_on": depends,
            "env": data.get("env", {}),
            "alive": alive,
        })
    _modules_cache["ts"] = now
    _modules_cache["modules"] = out
    return out


@app.get("/v1/modules")
async def list_modules() -> dict[str, Any]:
    """Online mirror of `bin/hermes-supervisor.py --ports`.

    Returns a JSON object listing every module's IO contract, with a live
    TCP-level liveness probe for each service module. The same payload is
    also what the WebUI's signal panel uses to draw the topology graph, so
    the supervisor (`--ports`) and the bridge (`/v1/modules`) always tell
    the same story.
    """
    mods = await _scan_module_json()
    up = sum(1 for m in mods if m.get("alive"))
    down = sum(1 for m in mods if m.get("port") and not m.get("alive"))
    tools = sum(1 for m in mods if not m.get("port"))
    return {
        "ts": _time.time(),
        "source": "bridge.server",
        "ttl_s": _MODULES_TTL_S,
        "summary": {"total": len(mods), "up": up, "down": down, "tools": tools},
        "modules": mods,
    }


@app.get("/v1/inspect/{name}")
async def inspect_module(name: str) -> dict[str, Any]:
    """Online mirror of `bin/hermes-supervisor.py --inspect <name>`.

    Returns the full decoded module.json (verbatim) plus a live TCP probe
    and the derived /health URL. Used by the WebUI module-detail panel.
    """
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        raise HTTPException(status_code=400, detail=f"invalid module name: {name}")
    root = Path(__file__).resolve().parent.parent
    json_path = root / "modules" / name / "module.json"
    if not json_path.is_file():
        raise HTTPException(status_code=404, detail=f"module not found: {name}")
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"bad module.json: {e}")

    net = data.get("network") or {}
    port = net.get("port")
    host = net.get("host", "127.0.0.1")
    probe: dict[str, Any] = {"port": port}
    if port:
        probe["alive"] = await _tcp_alive(host, int(port))
        probe["health_url"] = f"http://{host}:{port}{net.get('health_endpoint', '/health')}"
    else:
        probe["alive"] = None
        probe["health_url"] = None
    probe["probe_kind"] = "tcp" if port else "none"
    return {
        "ts": _time.time(),
        "name": name,
        "module_json": data,
        "probe": probe,
    }


# ---- Debug endpoints (only when DEBUG=1) ----

@app.get("/debug/config")
async def debug_config() -> dict[str, Any]:
    """Diagnostic endpoint to inspect what AIAgent sees at startup.
    Reports HERMES_HOME, the resolved config.yaml path, and what
    load_config() returns. Used to debug 'agent picks wrong provider'
    scenarios.
    """
    out: dict[str, Any] = {
        "os_env_HERMES_HOME": os.environ.get("HERMES_HOME"),
        "os_env_PYTHONPATH": os.environ.get("PYTHONPATH"),
        "bridge_HERMES_HOME_attr": str(HERMES_HOME),
        "config_yaml_exists": (HERMES_HOME / "config.yaml").exists(),
        "config_yaml_path": str(HERMES_HOME / "config.yaml"),
        "config_yaml_size": (HERMES_HOME / "config.yaml").stat().st_size if (HERMES_HOME / "config.yaml").exists() else 0,
    }
    try:
        from hermes_constants import get_hermes_home as _ghh
        out["upstream_get_hermes_home"] = str(_ghh())
    except Exception as e:
        out["upstream_get_hermes_home_error"] = str(e)
    try:
        from hermes_cli.config import load_config
        # Clear cache so we read fresh
        cfg = load_config()
        out["model"] = cfg.get("model")
        out["providers_keys"] = list((cfg.get("providers") or {}).keys())
        out["custom_providers_count"] = len(cfg.get("custom_providers") or [])
    except Exception as e:
        out["load_config_error"] = str(e)
    return out


# ---- /v1/models ----

# Model IDs that are not standalone chat models (vision encoders, embeddings, etc.)
_MODEL_ID_BLOCKLIST = {
    "mmproj",  # vision projector
}


def _normalize_model_id(model_id: str) -> str:
    """Strip .gguf suffix for deduplication. 'Qwen3-8B-Q4_K_M.gguf' → 'Qwen3-8B-Q4_K_M'."""
    return model_id.removesuffix(".gguf").removesuffix(".GGUF")


def _is_chat_model(model_id: str) -> bool:
    """Return True if model_id looks like a standalone chat model (not mmproj/embedding)."""
    base = _normalize_model_id(model_id).lower()
    return not any(blocked in base for blocked in _MODEL_ID_BLOCKLIST)


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    """List available models. Live-proxies llama-server's /v1/models.

    Deduplicates llama-server's router-mode output (which returns both
    ``name`` and ``name.gguf`` for the same file) and filters out
    non-chat models (mmproj vision encoders). Models whose .gguf file
    no longer exists on disk (stale router-preset.ini sections) are
    also dropped.

    Falls back to scanning ``data/models/*.gguf`` via
    ``modules.model_manager.gguf`` if llama-server is unreachable (so
    the WebUI's model dropdown still works during cold boot).
    """
    try:
        r = await _retry_call(
            lambda: _http.get("/v1/models", timeout=5.0),
            label="models-list",
        )
        if r.status_code == 200:
            raw = r.json()
            data = raw.get("data", [])
            # Deduplicate: keep one entry per base name, preferring the
            # .gguf id (since /v1/models/load accepts both forms, the
            # .gguf id is the one webui dropdowns historically use and
            # matches the on-disk filename exactly).
            # Also filter out ghost models (no file on disk) and
            # non-chat models (mmproj vision encoders, etc).
            seen: dict[str, dict] = {}
            for m in data:
                mid = m.get("id", "")
                if not _is_chat_model(mid):
                    continue
                base = _normalize_model_id(mid)
                existing = seen.get(base)
                if existing is None:
                    seen[base] = m
                else:
                    # Prefer the .gguf variant (canonical for /models/load
                    # AND matches the on-disk filename users see).
                    if mid.endswith(".gguf") and not existing.get("id", "").endswith(".gguf"):
                        seen[base] = m
            # Drop ghosts: model whose .gguf file is not on disk
            on_disk = {f.name for f in MODELS_DIR.glob("*.gguf")} if MODELS_DIR.is_dir() else set()
            filtered = [
                m for m in seen.values()
                if (m.get("id", "") + ".gguf" if not m.get("id", "").endswith(".gguf") else m["id"]) in on_disk
            ]
            return {"object": "list", "data": filtered}
    except Exception as e:
        logger.warning("llama-server /v1/models unreachable (%s); falling back to local scan", e)

    # Fallback: scan GGUF directory using our own parser
    try:
        from modules.model_manager.gguf import list_gguf_models  # type: ignore
        models = [m for m in list_gguf_models(MODELS_DIR) if _is_chat_model(m["name"])]
        return {"object": "list", "data": models}
    except Exception as e:
        logger.warning("modules.model_manager.gguf fallback also failed: %s", e)
        return {"object": "list", "data": []}


# ---- /v1/models/load ----

@app.post("/v1/models/load")
async def load_model(request: Request) -> dict[str, Any]:
    """Router-mode preload. Tells llama-server to load a specific model into VRAM.

    Body: {"model": "filename.gguf"} or {"model": "<alias>"}.
    llama-server's router mode resolves the filename to a loaded context and
    evicts LRU models if --models-max is hit.
    """
    body = await request.json()
    model = body.get("model")
    if not model:
        raise HTTPException(status_code=400, detail="`model` field is required")

    # llama-server exposes /models/load (not /v1/models/load) per b9538 docs
    # The router mode accepts both "<alias>" and "<filename>.gguf" but
    # we normalise at this layer so callers can pass either. Try the
    # caller's spelling first; on 404 try the alternate form (alias ↔
    # .gguf) so a "wrong" choice of name is still silently corrected
    # — critical for webui dropdowns where the user has no way to
    # know which form llama-server's router has registered.
    candidates: list[str] = [model]
    base = _normalize_model_id(model)
    if base != model:
        # Caller passed "X.gguf" → also try alias "X".
        candidates.append(base)
    else:
        # Caller passed "X" (or any non-.gguf) → also try "X.gguf".
        # Only add if a .gguf of that name actually exists on disk;
        # otherwise llama-server will 404 (correctly) and our next
        # 404 → 404 fallback would be wasted work.
        candidate_gguf = MODELS_DIR / f"{model}.gguf"
        if candidate_gguf.is_file():
            candidates.append(f"{model}.gguf")

    last_err: str = ""
    for i, candidate in enumerate(candidates):
        try:
            r = await _retry_call(
                lambda c=candidate: _http.post(
                    "/models/load", json={"model": c}, timeout=30.0,
                ),
                label=f"model-load({candidate})",
            )
        except httpx.HTTPError as e:
            telemetry.bus().emit(telemetry.Topics.MODULE_ERROR, {
                "model": candidate, "via": "load", "error": str(e),
            })
            raise HTTPException(status_code=502, detail=f"llama-server unreachable: {e}")

        # _retry_call returns the response (does NOT raise on 4xx), so
        # we inspect status_code ourselves. 404 → try the next candidate
        # in the alternation. 5xx → bubble up (server is sick, not
        # the wrong name). 2xx → done.
        if r.status_code == 404 and i + 1 < len(candidates):
            logger.debug(
                "model-load: '%s' returned 404, trying fallback '%s'",
                candidate, candidates[i + 1],
            )
            last_err = f"404 for {candidate}"
            continue
        if 200 <= r.status_code < 300:
            telemetry.bus().emit(
                telemetry.Topics.MODEL_LOADED,
                {"model": candidate, "status": r.status_code, "via": "load",
                 "fallback_index": i},
            )
            if i > 0:
                logger.info(
                    "model-load: '%s' resolved via fallback '%s' (index %d)",
                    model, candidate, i,
                )
        else:
            telemetry.bus().emit(telemetry.Topics.MODULE_ERROR, {
                "model": candidate, "via": "load",
                "status": r.status_code,
            })
        return JSONResponse(
            content=r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text},
            status_code=r.status_code,
        )

    # All candidates 404'd. Report the last one.
    raise HTTPException(
        status_code=404,
        detail=f"model '{model}' not found (tried: {', '.join(candidates)})",
    )


# ---- /v1/models/swap (alias of /v1/models/load, semantically distinct) ----
#
# `swap` emphasises the in-place LRU eviction semantics. WebUI / hermes_bridge.py
# call this when the active model changes; llama-server keeps the same process
# and only reloads weights. Identical wire format to `/v1/models/load`.

@app.post("/v1/models/swap")
async def swap_model(request: Request) -> dict[str, Any]:
    """Hot-swap the active model. Identical to /v1/models/load but named to
    signal intent at the caller (WebUI / hermes_bridge.py use this when a
    session's model changes).
    """
    return await load_model(request)


# ---- /v1/models/status ----

@app.get("/v1/models/status")
async def models_status() -> dict[str, Any]:
    """Report which models are currently loaded into VRAM and total VRAM usage.

    llama-server's /v1/models only lists declared models (config --models-dir).
    For the loaded subset we rely on the slot state endpoint (b9503+) or fall
    back to heuristic: probe /props to discover the currently resident model.
    """
    out: dict[str, Any] = {"loaded": [], "available": [], "vram": None, "source": "unknown"}

    # 1. Probe llama-server /props — returns the current single resident model
    #    (and helpful runtime stats). When llama-server is in router mode
    #    `/props` reports the most recently active slot.
    try:
        r = await _http.get("/props", timeout=5.0)
        if r.status_code == 200:
            props = r.json()
            default_slots = props.get("default_generation_settings", {})
            # In router mode the currently resident model appears in
            # `model_alias` (set via --alias) or `model_path`.
            resident_id = props.get("model_alias") or props.get("model_path") or props.get("model")
            if resident_id:
                out["loaded"].append({
                    "id": resident_id,
                    "source": "props.model_alias",
                })
            out["vram"] = props.get("vram_total_size") or props.get("vram_used_size")
            out["source"] = "llama-server-props"
    except Exception as e:
        logger.debug("llama-server /props unreachable: %s", e)

    # 2. Augment with /v1/models (full declared list under --models-dir)
    try:
        r = await _retry_call(
            lambda: _http.get("/v1/models", timeout=5.0),
            label="models-list",
        )
        if r.status_code == 200:
            data = r.json().get("data", [])
            out["available"] = [
                {"id": m.get("id", ""), "owned_by": m.get("owned_by", "llama-cpp")}
                for m in data
            ]
    except Exception as e:
        logger.debug("llama-server /v1/models unreachable: %s", e)

    # 3. Local fallback if llama-server was unreachable: scan MODELS_DIR
    if not out["available"]:
        try:
            from modules.model_manager.gguf import list_gguf_models  # type: ignore
            local = list_gguf_models(MODELS_DIR)
            out["available"] = [{"id": m.get("id", ""), "owned_by": "local-scan"} for m in local]
            if out["source"] == "unknown":
                out["source"] = "local-scan"
        except Exception as e:
            logger.debug("local gguf scan failed: %s", e)

    return out


# ---- /v1/models/evict ----

@app.post("/v1/models/evict")
async def evict_model(request: Request) -> dict[str, Any]:
    """Evict a model from llama-server router cache. Without a body, asks
    llama-server to unload the LRU slot (i.e. clear all). With
    {"model": "<id>"}, validates the model exists in --models-dir; llama-server
    has no per-model evict endpoint, so this is a best-effort preload of a
    null sentinel + restart prompt.

    Body: {} or {"model": "filename.gguf"}
    """
    try:
        body = await request.json() if (await request.body()) else {}
    except Exception:
        body = {}

    # llama-server b9538 doesn't expose a direct /models/evict. We approximate
    # it by reloading the currently-loaded model (forces a fresh slot) which
    # naturally evicts LRU candidates when --models-max is saturated.
    model = body.get("model")
    if not model:
        # Pick the resident model from /props
        try:
            r = await _http.get("/props", timeout=5.0)
            if r.status_code == 200:
                props = r.json()
                resident = props.get("model_alias") or props.get("model_path")
                # b9538 router-mode placeholder: when nothing is resident,
                # `model_alias == "llama-server"` and `model_path == "none"`.
                # Treat as "nothing to evict" instead of triggering a reload.
                if resident and resident != "llama-server" and resident != "none":
                    model = resident
                else:
                    return {
                        "status": "noop",
                        "reason": "router mode has no resident model — nothing to evict",
                        "resident_alias": resident,
                    }
        except Exception:
            pass

    if not model:
        return {"status": "noop", "reason": "no resident model detected — nothing to evict"}

    # Reload-resident trick: POST /models/load with the resident model forces
    # a slot re-evaluation. Returns success even if already resident.
    try:
        r = await _http.post("/models/load", json={"model": model}, timeout=30.0)
        # Signal: model evicted (best-effort — llama-server has no real evict).
        telemetry.bus().emit(telemetry.Topics.MODEL_EVICTED, {
            "model": model, "status": r.status_code, "method": "reload-resident",
        })
        return JSONResponse(
            content={"status": "ok", "triggered_reload": model, "upstream": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text},
            status_code=r.status_code,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"llama-server unreachable: {e}")


# ---- /v1/llama/restart ----
#
# Why this endpoint exists
# ------------------------
# llama-server's router mode reads `--models-dir` + `router-preset.ini` ONCE at
# startup and caches the resulting model list in memory. The runtime API
# (`/v1/models`, `/models/load`, `/models/unload`) never re-scans the disk,
# so a user who deletes/renames a `.gguf` file mid-session will:
#   1. Still see the old id in /v1/models (stale cache).
#   2. Get HTTP 500 "model name=X failed to load" on /v1/chat/completions
#      (llama tries to mmap a file that no longer exists).
#   3. Have no way to force a refresh from the WebUI ("refresh cache" only
#      refreshes cloud provider catalogs, see webui index.js:1035).
#
# The WebUI's "refresh model cache" button deliberately does NOT touch local
# .gguf, so users hit a dead end. This endpoint shells out to the supervisor
# (which owns the lifecycle) and asks it to stop + start the llm_engine
# module. After it returns, llama-server's process is fresh and its in-memory
# model list matches what's currently on disk.
#
# Safety
# ------
# * Only restarts the `llm_engine` module (whitelisted by name).
# * Hardcoded to HERMES_ROOT/bin/hermes-supervisor.py — the supervisor's own
#   stop.ps1 is what kills the old process, so we don't reinvent the wheel.
# * 90s timeout to absorb llama-server's startup (it can take 10-20s to load
#   the first model from cold VRAM).
# * After restart, the bridge immediately re-probes llama health so the
#   response payload tells the caller whether the new instance is up.
_SUPERVISOR_BIN = Path(__file__).resolve().parent.parent / "bin" / "hermes-supervisor.py"
_LLAMA_HEALTH_TIMEOUT_S = 30.0


async def _wait_for_llama_back(timeout_s: float = _LLAMA_HEALTH_TIMEOUT_S) -> dict[str, Any]:
    """Poll llama-server /health until it returns 200 or timeout elapses.

    Returns {"ready": bool, "elapsed_s": float, "last_status": int|None}.
    """
    deadline = time.monotonic() + timeout_s
    last_status: int | None = None
    t0 = time.monotonic()
    while time.monotonic() < deadline:
        try:
            r = await _http.get("/health", timeout=2.0)
            last_status = r.status_code
            if r.status_code == 200:
                return {"ready": True, "elapsed_s": round(time.monotonic() - t0, 2), "last_status": 200}
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return {"ready": False, "elapsed_s": round(time.monotonic() - t0, 2), "last_status": last_status}


@app.post("/v1/llama/restart")
async def restart_llama() -> dict[str, Any]:
    """Force-restart llama-server so it re-scans --models-dir + router-preset.ini.

    Use this after you add/remove/rename a .gguf file in data/models/. The
    WebUI's "refresh model cache" button does NOT touch local models, so
    this is the only bridge-level way to make WebUI's model dropdown reflect
    the current disk state.

    Body: {} (reserved for future options like { "wait": false }).

    Returns: { "status": "ok" | "error", "supervisor_exit": int, "elapsed_s": float,
               "llama_ready": bool, "pid_before": int|None, "pid_after": int|None,
               "models_count": int|None }
    """
    if not _SUPERVISOR_BIN.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"supervisor binary not found at {_SUPERVISOR_BIN}",
        )
    if shutil.which("python") is None and shutil.which("python3") is None:
        raise HTTPException(status_code=500, detail="no python interpreter on PATH for supervisor")

    # 1) capture the old llama PID (for the response payload + heartbeat).
    pid_before: int | None = None
    try:
        r = await _http.get("/props", timeout=3.0)
        if r.status_code == 200:
            # /props doesn't expose a PID; we have to infer from netstat.
            pass
    except Exception:
        pass
    try:
        netstat_out = subprocess.run(
            ["netstat", "-aon", "-p", "tcp"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
        for line in netstat_out.splitlines():
            if "LISTENING" in line and ":8080" in line:
                m = re.search(r"(\d+)\s*$", line.strip())
                if m:
                    pid_before = int(m.group(1))
                    break
    except Exception:
        pass

    # 2) shell out to supervisor --restart llm_engine (no shell, list args).
    py = shutil.which("python") or shutil.which("python3")
    # We deliberately call the *portable* python (HERMES_HOME's portable-python)
    # if it's on PATH, because the supervisor scripts depend on a working
    # `python` that has all the deps. The PATH set by start.bat puts that first.
    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            py, str(_SUPERVISOR_BIN), "--restart", "llm_engine",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=90.0)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(status_code=504, detail="supervisor --restart timed out (>90s)")
        supervisor_exit = proc.returncode
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"supervisor spawn failed: {e}")

    elapsed_s = round(time.monotonic() - t0, 2)
    supervisor_stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    supervisor_stderr = (stderr_b or b"").decode("utf-8", errors="replace")

    if supervisor_exit != 0:
        raise HTTPException(
            status_code=500,
            detail=(
                f"supervisor --restart llm_engine failed (exit={supervisor_exit}); "
                f"stderr={supervisor_stderr.strip()[-400:]}"
            ),
        )

    # 3) wait for llama-server to be reachable again.
    health = await _wait_for_llama_back()

    # 4) capture the new PID + the post-restart /v1/models count.
    pid_after: int | None = None
    models_count: int | None = None
    if health["ready"]:
        try:
            netstat_out = subprocess.run(
                ["netstat", "-aon", "-p", "tcp"],
                capture_output=True, text=True, timeout=5, check=False,
            ).stdout
            for line in netstat_out.splitlines():
                if "LISTENING" in line and ":8080" in line:
                    m = re.search(r"(\d+)\s*$", line.strip())
                    if m:
                        pid_after = int(m.group(1))
                        break
        except Exception:
            pass
        try:
            r = await _http.get("/v1/models", timeout=5.0)
            if r.status_code == 200:
                models_count = len(r.json().get("data", []))
        except Exception:
            pass

    # 5) emit a signal so the rest of the system can observe the restart.
    telemetry.bus().emit(telemetry.Topics.MODEL_EVICTED, {
        "event": "llama_restart",
        "pid_before": pid_before,
        "pid_after": pid_after,
        "elapsed_s": elapsed_s,
        "llama_ready": health["ready"],
        "models_count": models_count,
    })

    return {
        "status": "ok" if health["ready"] else "degraded",
        "supervisor_exit": supervisor_exit,
        "elapsed_s": elapsed_s,
        "pid_before": pid_before,
        "pid_after": pid_after,
        "llama_ready": health["ready"],
        "llama_health_wait_elapsed_s": health["elapsed_s"],
        "models_count": models_count,
        "supervisor_stdout_tail": supervisor_stdout.strip().splitlines()[-6:],
    }


# ---- /v1/llama/active — read current active llama-server base_url ----
# Icarus rule: 切端口不能让我休眠。
@app.get("/v1/llama/active")
async def llama_active() -> dict[str, Any]:
    """Return the current active llama-server endpoint and per-candidate health.
    Useful for the WebUI to display 'currently routing via :8080' (or whichever)."""
    return {
        "active_url": _active_base_url,
        "candidates": _get_llama_health().get("candidates", {}),
        "candidates_total": len(_LLAMA_CANDIDATES),
        "candidates_alive": sum(1 for v in _get_llama_health().get("candidates", {}).values() if v.get("alive")),
    }


# ---- /v1/llama/switch-active — manually pick which candidate to use ----
# Body: {"url": "http://127.0.0.1:8081"} — must be one of the configured
# candidates. The active url is updated immediately (no restart). The next
# chat request will route to the new endpoint. Use this when the primary
# llama-server died and you want to force-failover without waiting for the
# health monitor to react.
@app.post("/v1/llama/switch-active")
async def llama_switch_active(body: dict[str, Any]) -> dict[str, Any]:
    global _active_base_url
    target = (body.get("url") or "").strip().rstrip("/")
    if not target:
        raise HTTPException(status_code=400, detail="missing 'url' in body")
    if target not in _LLAMA_CANDIDATES:
        raise HTTPException(
            status_code=400,
            detail=f"url {target!r} is not a configured candidate. Known: {_LLAMA_CANDIDATES}",
        )
    # Probe the target before switching so we don't blindly point at a dead one.
    try:
        async with httpx.AsyncClient(base_url=target, timeout=3.0) as cli:
            r = await cli.get("/health")
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"target {target} not healthy (HTTP {r.status_code})")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"target {target} unreachable: {e}")

    old = _active_base_url
    with _health_lock:
        _active_base_url = target
        _candidate_health[target]["alive"] = True
        _candidate_health[target]["consecutive_failures"] = 0
        _last_active_change = time.time()
    logger.warning("bridge: active llama endpoint manually switched %s → %s", old, target)
    telemetry.bus().emit(telemetry.Topics.MODEL_EVICTED, {
        "event": "llama_active_switch",
        "from": old,
        "to": target,
        "manual": True,
    })
    return {
        "ok": True,
        "from": old,
        "to": target,
        "candidates": _get_llama_health().get("candidates", {}),
    }


# ---- /v1/models/warmup ----

# Warmup task registry — concurrent warmups are rare, so a single dict + lock
# is sufficient. Keys are warmup_id (uuid4), values are {state, models, results}.
_warmup_tasks: dict[str, dict[str, Any]] = {}
_warmup_lock = threading.Lock()
_WARMUP_TASK_TTL_S = 3600  # 1 hour


def _cleanup_warmup_tasks() -> None:
    """Evict completed warmup tasks older than _WARMUP_TASK_TTL_S."""
    now = time.time()
    with _warmup_lock:
        expired = [
            wid for wid, t in _warmup_tasks.items()
            if t.get("state") == "completed" and now - t.get("finished_at", 0) > _WARMUP_TASK_TTL_S
        ]
        for wid in expired:
            del _warmup_tasks[wid]


def _run_warmup(warmup_id: str, models: list[str]) -> None:
    """Background thread: sequentially preload models into llama-server router cache.

    Updates _warmup_tasks[warmup_id]["state"] and per-model results as we go.
    Sequential (not parallel) because llama-server's --models-max typically
    allows only a small number of concurrent slots, and concurrent loads would
    thrash VRAM. Idempotent: re-loading the same model returns success quickly.
    """
    results: list[dict[str, Any]] = []
    for m in models:
        t0 = time.time()
        try:
            r = _warmup_http_client.post(
                f"{LLAMA_BASE_URL}/models/load",
                json={"model": m},
            )
            elapsed = time.time() - t0
            results.append({
                "model": m,
                "status": "ok" if r.status_code == 200 else "error",
                "http": r.status_code,
                "elapsed_sec": round(elapsed, 2),
                "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200],
            })
        except Exception as e:
            elapsed = time.time() - t0
            results.append({
                "model": m,
                "status": "error",
                "error": str(e),
                "elapsed_sec": round(elapsed, 2),
            })
        # Persist progress after each model so polling clients see updates.
        with _warmup_lock:
            _warmup_tasks[warmup_id]["results"] = list(results)
            _warmup_tasks[warmup_id]["current"] = m
        # Signal: per-model warmup progress (one per model).
        telemetry.bus().emit(telemetry.Topics.MODEL_WARMUP_PROGRESS, {
            "warmup_id": warmup_id,
            "model": m,
            "results_so_far": len(results),
            "total": len(models),
            "last_status": results[-1]["status"],
        })
    with _warmup_lock:
        _warmup_tasks[warmup_id]["state"] = "completed"
        _warmup_tasks[warmup_id]["finished_at"] = time.time()
        ok_count = sum(1 for r in results if r.get("status") == "ok")
    # Signal: warmup done (overall).
    telemetry.bus().emit(telemetry.Topics.MODEL_WARMUP_DONE, {
        "warmup_id": warmup_id,
        "ok": ok_count,
        "errors": len(results) - ok_count,
        "total": len(results),
    })


@app.post("/v1/models/warmup")
async def warmup_models(request: Request) -> dict[str, Any]:
    """Asynchronously preload a list of models into llama-server's router cache.

    Body: {"models": ["file1.gguf", "file2.gguf"]}
    Returns: {"warmup_id": "...", "state": "running", "count": N}

    Poll status at GET /v1/models/warmup/{warmup_id}.
    Use case: preheat models on app startup so the first user chat doesn't pay
    the multi-second load cost. Models not in --models-dir are silently skipped.
    """
    body = await request.json()
    models = body.get("models")
    if not isinstance(models, list) or not models:
        raise HTTPException(status_code=400, detail="`models` must be a non-empty list")

    _cleanup_warmup_tasks()

    warmup_id = uuid.uuid4().hex
    started_at = time.time()
    with _warmup_lock:
        _warmup_tasks[warmup_id] = {
            "warmup_id": warmup_id,
            "state": "running",
            "models": list(models),
            "results": [],
            "started_at": started_at,
        }

    # Signal: warmup kicked off.
    telemetry.bus().emit(telemetry.Topics.MODEL_WARMUP_START, {
        "warmup_id": warmup_id,
        "models": list(models),
        "count": len(models),
    })

    t = threading.Thread(target=_run_warmup, args=(warmup_id, list(models)), daemon=True, name=f"warmup-{warmup_id[:8]}")
    t.start()

    return {"warmup_id": warmup_id, "state": "running", "count": len(models), "started_at": started_at}


@app.get("/v1/models/warmup/{warmup_id}")
async def warmup_status(warmup_id: str) -> dict[str, Any]:
    """Poll status of an in-flight or completed warmup."""
    with _warmup_lock:
        task = _warmup_tasks.get(warmup_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"warmup_id {warmup_id} not found")
    # Strip raw bodies in the response to keep payload small
    public = {k: v for k, v in task.items() if k != "_internal"}
    return public


# ---- /v1/chat/completions (smart routing enabled) ----
#
# Every chat request first passes through the RoutingEngine. Based on
# the user's message content and network status, the request is either:
#   - proxied to llama-server (:8080) for local/privacy tasks
#   - forwarded to a cloud provider (OpenAI/Anthropic/etc.) for complex tool work
#
# The routing decision is logged and attached to response headers.

from hermes.routing import RoutingEngine as _RoutingEngine
from bridge.cloud_client import CloudClient as _CloudClient, CloudClientError as _CloudClientError

_routing_engine: _RoutingEngine | None = None
_cloud_client: _CloudClient | None = None


def _get_routing_engine() -> _RoutingEngine:
    global _routing_engine
    if _routing_engine is None:
        try:
            _routing_engine = _RoutingEngine.from_config()
        except Exception:
            _routing_engine = _RoutingEngine()
        logger.info("routing engine initialized")
    return _routing_engine


def _get_cloud_client() -> _CloudClient:
    global _cloud_client
    if _cloud_client is None:
        _cloud_client = _CloudClient()
        logger.info("cloud client initialized")
    return _cloud_client


def _check_local_availability() -> tuple[bool, str]:
    """Probe whether the local llama-server router can serve a chat request.

    Returns (available, reason) where reason is a human-readable string for logs.

    Triggers fallback to cloud when:
    - router /props not reachable (down/dead)
    - model_path == "none" AND no worker has been spawned in the last 30s
    - VRAM usage > 95% (worker stuck mid-load — exact case 哥哥 2026-06-27 hit)

    5-second hard cap on the probe — never block chat_completions for more than
    that. The point is to AVOID hanging on a dead worker, not to add a new way
    to hang.
    """
    # 1. router reachable? — use _active_base_url (canonical llama-server URL)
    if not _active_base_url:
        return False, "no active llama-server URL configured"
    try:
        r = httpx.get(f"{_active_base_url}/props", timeout=3.0)
        if r.status_code != 200:
            return False, f"router /props returned HTTP {r.status_code}"
    except httpx.HTTPError as e:
        return False, f"router unreachable: {e}"

    # 2. parse router status
    try:
        props = r.json()
    except Exception:
        return False, "router /props returned non-JSON"

    model_path = props.get("model_path", "none")
    model_alias = props.get("model_alias", "llama-server")
    if model_path == "none":
        return False, f"no model loaded (alias={model_alias})"

    # 3. worker running? (cheap psutil-like check)
    try:
        import psutil
        worker_count = 0
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                if proc.info.get("name") and "llama-server" in proc.info["name"]:
                    cmdline = proc.info.get("cmdline") or []
                    if any("--alias" in arg for arg in cmdline):
                        worker_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if worker_count == 0:
            return False, "router loaded but no worker process"
    except ImportError:
        # psutil not available — skip worker process check
        pass

    # 4. VRAM check via nvidia-smi (fast, non-blocking)
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3.0,
        )
        if out.returncode == 0:
            line = out.stdout.strip().split("\n")[0]
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                used = int(parts[0])
                total = int(parts[1])
                if total > 0 and used / total > 0.95:
                    return False, f"VRAM near full ({used}/{total} MiB, {100*used/total:.1f}%) — worker likely stuck"
    except Exception:
        # nvidia-smi not on PATH or timeout — don't block on this
        pass

    return True, f"local ready (alias={model_alias}, path={model_path})"





def _extract_user_message(messages: list[dict]) -> str:
    """Extract the last user message from the chat messages list."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                # multimodal: extract text parts
                parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                return " ".join(parts)
            return str(content)
    return ""


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    """OpenAI-compatible chat completion with smart routing.

    - Privacy-sensitive / local-skill requests → llama-server (:8080)
    - Complex tool-use requests → cloud API (OpenAI / etc.)
    - Simple conversation → local model (fast + private)
    - Network offline → local model fallback

    Set ``X-Hermes-Routing: local`` or ``X-Hermes-Routing: cloud`` header
    to bypass the routing engine.
    """
    body = await request.json()
    is_stream = bool(body.get("stream"))

    # FIX 2026-06-18: model name normalisation for llama-server router.
    # llama-server's router mode registers each .gguf under TWO ids:
    #   - the alias (filename without .gguf suffix)  → responds to chat
    #   - the bare filename with .gguf suffix          → only resolves in
    #                                                  /models/load, not /v1/chat/completions
    # If webui passes "Qwen3-8B-Q4_K_M.gguf" to chat, llama-server
    # returns 400 "model not found" with an empty stream. Strip the
    # .gguf before forwarding chat. The dedup layer in /v1/models
    # still surfaces the .gguf form to webui so the dropdown shows
    # both options; this strip is the bridge-time safety net.
    raw_model = body.get("model", "")
    if raw_model:
        normalised = _normalize_model_id(raw_model)
        if normalised != raw_model:
            logger.info(
                "chat: model %r → %r (strip .gguf for llama-server router)",
                raw_model, normalised,
            )
            body["model"] = normalised

    # ---- Neuro: mark user message + inject memory ----
    try:
        user_msg = _extract_user_message(body.get("messages", []))
        if user_msg:
            icarus.mark_new_message("user", user_msg)
    except Exception as exc:
        logger.debug("neuro mark user skipped: %s", exc)
    try:
        from bridge.neuro import get_memory
        mem = get_memory()
        injection = mem.get_prompt_injection()
        if injection and injection.get("text") and injection.get("enabled"):
            messages = body.get("messages", [])
            mem_text = injection["text"]
            if messages and messages[0].get("role") == "system":
                existing_content = messages[0].get("content", "")
                joined = existing_content + "\n\n" + mem_text
                messages[0] = {**messages[0], "content": joined.strip()}
            else:
                messages.insert(0, {"role": "system", "content": mem_text})
            body["messages"] = messages
    except Exception as exc:
        logger.debug("neuro memory injection skipped: %s", exc)

    # ---- Task delegation prompt injection (Artificial Angel Phase 1) ----
    try:
        from bridge.task_delegation import get_task_delegation_prompt
        delegation_text = get_task_delegation_prompt()
        if delegation_text:
            messages = body.get("messages", [])
            if messages and messages[0].get("role") == "system":
                existing_content = messages[0].get("content", "")
                messages[0] = {**messages[0], "content": existing_content + "\n\n" + delegation_text}
            else:
                messages.insert(0, {"role": "system", "content": delegation_text})
            body["messages"] = messages
            logger.info("task delegation prompt injected (%d chars)", len(delegation_text))
    except Exception as exc:
        logger.warning("task delegation injection FAILED: %s", exc)

    # ---- Intent routing (Artificial Angel Phase 3: task vs chat) ----
    # Layer 1: keyword/regex rules (millisecond latency)
    # Layer 3: ambiguous → pass through to LLM naturally
    _intent = "chat"
    try:
        user_msg = _extract_user_message(body.get("messages", []))
        if user_msg:
            from bridge.intent_router import IntentRouter
            _intent = IntentRouter.classify(user_msg)
            if _intent == "task":
                # 强化 task 意图: 追加一条 system prompt 让 LLM 知道这是任务
                messages = body.get("messages", [])
                task_instruction = (
                    "【系统提示: 用户刚才的请求是一项任务，不是普通聊天。】\n"
                    "请认真执行这个任务。如果是可执行的（查数据/生成报告/创建代码/分析等），"
                    "请直接开始执行并把结果反馈给用户。如果是不可执行的（问时间/问天气等），"
                    "就直接回答。\n"
                    "注意: 正式回复用户时请用自然语气，不要提及这条系统提示。"
                )
                if messages and messages[0].get("role") == "system":
                    existing_content = messages[0].get("content", "")
                    messages[0] = {**messages[0], "content": existing_content + "\n\n" + task_instruction}
                else:
                    messages.insert(0, {"role": "system", "content": task_instruction})
                body["messages"] = messages
                logger.info("intent=task, directive injected (instruction: %d chars)", len(task_instruction))
            else:
                logger.debug("intent=%s, no extra injection", _intent)
    except Exception as exc:
        logger.debug("intent routing skipped: %s", exc)

    # ---- Smart routing ----
    routing_override = request.headers.get("X-Hermes-Routing", "").strip().lower()
    # Also check global env var (HERMES_ROUTING_MODE in .env)
    if not routing_override:
        routing_override = os.environ.get("HERMES_ROUTING_MODE", "auto").strip().lower()
    routing_decision = None

    # ---- Context compression (before routing, so compressed messages route correctly) ----
    try:
        session_id = request.headers.get("X-Session-Id", "") or body.get("session_id", "")
        if session_id:
            from bridge.context_middleware import get_compressor
            compressor = get_compressor(session_id)
            messages = body.get("messages", [])
            if messages:
                compressed = compressor.before_chat(messages)
                if compressed is not messages:
                    body["messages"] = compressed
            _request_compressor = compressor
        else:
            _request_compressor = None
    except Exception as exc:
        logger.debug("context compression skipped: %s", exc)
        _request_compressor = None

    if routing_override not in ("local", "cloud"):
        user_msg = _extract_user_message(body.get("messages", []))
        if user_msg:
            try:
                engine = _get_routing_engine()
                routing_decision = engine.decide(user_msg)
                logger.info(
                    "routing: target=%s reason=%s query=%.60s",
                    routing_decision.route_target,
                    routing_decision.reason,
                    user_msg,
                )
            except Exception:
                logger.debug("routing engine failed, falling back to llama-server", exc_info=True)
        else:
            routing_decision = None
    elif routing_override == "local":
        routing_decision = type("D", (), {"route_target": "llama_server", "reason": "X-Hermes-Routing header"})()
    elif routing_override == "cloud":
        # Explicit cloud override — bypass routing engine, route straight to
        # the cloud_api path. Caller asserts the model in body["model"] is a
        # cloud-registered model (e.g. minimax-cn / openai / anthropic).
        # If it's not, _handle_cloud_chat will return a clean 4xx explaining
        # why instead of falling through to llama-server (which would 400 on
        # "model not found" — misleading for the caller).
        routing_decision = type("D", (), {"route_target": "cloud_api", "reason": "X-Hermes-Routing: cloud header"})()

    # ---- Route ----
    # ---- Local availability check (哥哥 6-27 axiom: "router 后边直接就是 Hermes") ----
    # If routing engine decided llama_server, but local is currently unavailable
    # (no model loaded / VRAM full / worker stuck), flip to cloud. Only flips
    # DECIDED llama_server → cloud; respects explicit X-Hermes-Routing: local.
    if (routing_decision is None or getattr(routing_decision, "route_target", None) == "llama_server"):
        try:
            ok, reason = _check_local_availability()
            if not ok:
                logger.warning(
                    "local unavailable: %s — flipping to cloud_api (default provider=minimax-cn)",
                    reason,
                )
                # telemetry emit (gracefully skip if topic doesn't exist)
                try:
                    telemetry.bus().emit(telemetry.Topics.ROUTING_FLIPPED, {
                        "from": getattr(routing_decision, "route_target", "llama_server") if routing_decision else "llama_server",
                        "to": "cloud_api",
                        "reason": reason,
                    })
                except AttributeError:
                    # ROUTING_FLIPPED topic not defined yet — emit to a known one with a structured payload
                    try:
                        telemetry.bus().emit(telemetry.Topics.MODULE_ERROR, {
                            "event": "routing_flipped",
                            "from": getattr(routing_decision, "route_target", "llama_server") if routing_decision else "llama_server",
                            "to": "cloud_api",
                            "reason": reason,
                        })
                    except Exception:
                        pass
                routing_decision = type("D", (), {
                    "route_target": "cloud_api",
                    "reason": f"local unavailable: {reason}",
                    "cloud_provider": "minimax-cn",
                })()
        except Exception as exc:
            logger.warning("local availability check FAILED: %s", exc)
    if routing_decision is not None and routing_decision.route_target == "cloud_api":
        return await _handle_cloud_chat(body, routing_decision, is_stream, _request_compressor)

    # Default: proxy to llama-server
    logger.info(
        "chat.completions model=%s stream=%s messages=%d",
        body.get("model", "?"), is_stream, len(body.get("messages", [])),
    )

    # Signal: chat request received.
    telemetry.bus().emit(telemetry.Topics.CHAT_REQUEST, {
        "model": body.get("model", "?"),
        "messages": len(body.get("messages", []) or []),
        "stream": is_stream,
        "routing_target": getattr(routing_decision, "route_target", "llama_server") if routing_decision else "llama_server",
    })

    if is_stream:
        return StreamingResponse(
            _stream_chat(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Hermes-Routing-Target": "llama_server",
            },
        )

    # Non-streaming — route through _proxy_to_active so a dead port transparently
    # falls back to the next alive llama-server candidate. "活着" 优先。
    try:
        r = await _proxy_to_active("POST", "/v1/chat/completions", json=body, timeout=300.0)
        telemetry.bus().emit(telemetry.Topics.CHAT_DONE, {
            "model": body.get("model", "?"),
            "status": r.status_code,
            "active_url": _active_base_url,
        })
        # Context compression: feed usage back from local response
        if _request_compressor and r.status_code == 200:
            try:
                resp_data = r.json()
                _request_compressor.after_chat(resp_data.get("usage"))
            except Exception:
                pass
        return JSONResponse(
            content=r.json(),
            status_code=r.status_code,
            headers={
                "X-Hermes-Routing-Target": "llama_server",
                "X-Hermes-Llama-Active": _active_base_url,
            },
        )
    except httpx.HTTPError as e:
        telemetry.bus().emit(telemetry.Topics.CHAT_ERROR, {
            "model": body.get("model", "?"),
            "error": str(e),
        })
        raise HTTPException(status_code=502, detail=f"llama-server unreachable: {e}")


async def _handle_cloud_chat(
    body: dict[str, Any],
    decision: Any,
    is_stream: bool,
    _request_compressor: Any = None,
) -> Any:
    """Handle a chat request routed to a cloud provider."""
    # 哥哥 2026-06-27: 默认走 minimax-cn（HERMES auth.json 已注册，.env 有 key）
    # openai/anthropic 没 key；保持 caller 可通过 decision.cloud_provider / body.model 覆盖
    provider = getattr(decision, "cloud_provider", "") or "minimax-cn"
    model = body.get("model", "")

    # Use the body's model if specified, otherwise default to MiniMax-M3
    if not model or model == "auto":
        model = "MiniMax-M3"

    messages = body.get("messages", [])
    max_tokens = body.get("max_tokens", 4096)
    temperature = body.get("temperature", 0.7)

    logger.info(
        "cloud chat: provider=%s model=%s messages=%d reason=%s",
        provider, model, len(messages), getattr(decision, "reason", ""),
    )

    telemetry.bus().emit(telemetry.Topics.CHAT_REQUEST, {
        "model": model,
        "provider": provider,
        "messages": len(messages),
        "stream": is_stream,
        "routing_target": "cloud_api",
        "routing_reason": getattr(decision, "reason", ""),
    })

    # ---- Copilot ACP route ----
    if provider == "copilot":
        if is_stream:
            logger.warning("copilot ACP does not support streaming — falling back to non-stream")
        try:
            from bridge.copilot_bridge import chat as copilot_chat
            resp = await copilot_chat(messages, model=model)
            telemetry.bus().emit(telemetry.Topics.CHAT_DONE, {
                "model": model, "provider": "copilot",
            })
            # Feed usage back for context compression
            if _request_compressor:
                try:
                    _request_compressor.after_chat(resp.get("usage"))
                except Exception:
                    pass
            return JSONResponse(
                content=resp,
                headers={"X-Hermes-Routing-Target": "cloud:copilot"},
            )
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))

    client = _get_cloud_client()

    try:
        if is_stream:
            return StreamingResponse(
                client.chat_stream(provider, model, messages, max_tokens=max_tokens, temperature=temperature),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "X-Hermes-Routing-Target": f"cloud:{provider}",
                },
            )

        resp = await client.chat(
            provider, model, messages,
            max_tokens=max_tokens, temperature=temperature,
        )
        telemetry.bus().emit(telemetry.Topics.CHAT_DONE, {
            "model": model,
            "provider": provider,
        })
        # Context compression: feed usage back from cloud response
        if _request_compressor:
            try:
                _request_compressor.after_chat(resp.get("usage"))
            except Exception:
                pass
        return JSONResponse(
            content=resp,
            headers={"X-Hermes-Routing-Target": f"cloud:{provider}"},
        )
    except _CloudClientError as e:
        telemetry.bus().emit(telemetry.Topics.CHAT_ERROR, {
            "model": model,
            "provider": provider,
            "error": str(e),
        })
        logger.warning("cloud API failed: %s — falling back to local", e)
        # Fallback to local model
        if is_stream:
            return StreamingResponse(
                _stream_chat(body),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "X-Hermes-Routing-Target": "llama_server(fallback)",
                },
            )
        try:
            r = await _retry_call(
                lambda: _http.post("/v1/chat/completions", json=body, timeout=300.0),
                label="chat-fallback",
            )
            return JSONResponse(
                content=r.json(),
                status_code=r.status_code,
                headers={"X-Hermes-Routing-Target": "llama_server(fallback)"},
            )
        except httpx.HTTPError as e2:
            raise HTTPException(status_code=502, detail=f"Cloud + local both failed: {e} | {e2}")


async def _stream_chat(body: dict[str, Any]) -> AsyncIterator[bytes]:
    """Stream chat-completion chunks as SSE.

    llama-server emits OpenAI-format SSE (data: {...} per chunk, data: [DONE]
    at the end). We forward verbatim so any OpenAI-compatible client works.
    Emits CHAT_DELTA per non-empty chunk and CHAT_DONE / CHAT_ERROR at the end.
    """
    chunk_count = 0
    try:
        async with _http.stream(
            "POST",
            "/v1/chat/completions",
            json=body,
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=5.0),
        ) as r:
            async for line in r.aiter_lines():
                if line:
                    chunk_count += 1
                    yield (line + "\n\n").encode("utf-8")
        telemetry.bus().emit(telemetry.Topics.CHAT_DONE, {
            "model": body.get("model", "?"),
            "stream": True,
            "chunks": chunk_count,
        })
    except httpx.HTTPError as e:
        telemetry.bus().emit(telemetry.Topics.CHAT_ERROR, {
            "model": body.get("model", "?"),
            "stream": True,
            "error": str(e),
        })
        err = json.dumps({"error": {"message": str(e), "type": "bridge_error"}})
        yield f"data: {err}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"


@app.post("/v1/chat/completions/sse")
async def chat_completions_sse(request: Request):
    """Dedicated SSE endpoint for streaming chat. Identical behaviour to
    POST /v1/chat/completions with stream=true. Lives at a distinct URL so
    CDN/proxy layers can route on path (no body parsing)."""
    body = await request.json()
    telemetry.bus().emit(telemetry.Topics.CHAT_REQUEST, {
        "model": body.get("model", "?"),
        "messages": len(body.get("messages", []) or []),
        "stream": True,
        "via": "sse",
    })
    return StreamingResponse(
        _stream_chat(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---- /api/chat/sessions ----

@app.get("/api/chat/sessions")
async def list_sessions(limit: int = 50) -> dict[str, Any]:
    """List chat sessions from upstream hermes_state.SessionDB (SQLite FTS5).

    For now: best-effort import + lazy initialise. Returns empty list if the
    upstream module isn't fully loadable yet (e.g. user hasn't run the
    setup wizard). SessionDB itself falls back to in-memory when the DB file
    isn't present.
    """
    try:
        from hermes_state import SessionDB  # type: ignore
        # SessionDB takes (db_path, project_root) — project_root is optional
        db_path = HERMES_HOME / "state.db"
        db = SessionDB(str(db_path) if db_path.exists() else ":memory:")
        sessions = db.list_sessions(limit=limit) if hasattr(db, "list_sessions") else []
        return {"object": "list", "data": sessions, "count": len(sessions)}
    except Exception as e:
        logger.warning("SessionDB unavailable: %s", e)
        return {"object": "list", "data": [], "count": 0, "warning": str(e)}


# ---- /api/agent/run ----

@app.post("/api/agent/run")
async def agent_run(request: Request) -> dict[str, Any]:
    """Run upstream AIAgent for a single message. For non-WebUI callers (CLI,
    scripts, integrations). WebUI has its own backend and uses Socket.IO.

    Body: {"message": "...", "session_id": "...optional...", "model": "...optional..."}
    Returns: {"response": "...", "session_id": "...", "model": "..."}
    """
    body = await request.json()
    message = body.get("message")
    if not message:
        raise HTTPException(status_code=400, detail="`message` field is required")
    if len(message) > 32768:
        raise HTTPException(status_code=400, detail="`message` exceeds 32K char limit")
    max_iter = int(body.get("max_iterations", 1))
    if max_iter < 1 or max_iter > 10:
        raise HTTPException(status_code=400, detail="`max_iterations` must be between 1 and 10")

    try:
        from run_agent import AIAgent  # type: ignore
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"upstream hermes-agent not importable: {e}")

    try:
        # AIAgent has a huge signature; this is the minimal subset that works
        # without a profile / credential pool. See hermes-agent/AGENTS.md for
        # the full list.
        #
        # We disable all toolsets by default to keep the system prompt small
        # enough to fit local models on 8GB VRAM (where llama-server caps
        # n_ctx at ~10752 tokens for 8B Q4_K_M). Without this, hermes-agent
        # tries to inject every tool description (~17K tokens) and fails with
        # "request (18270 tokens) exceeds the available context size (10752)".
        # Pass ?toolsets=skill,terminal (comma-list) to re-enable specific sets
        # when running against cloud LLMs with larger contexts.
        enabled_ts_param = (request.query_params.get("enabled_toolsets") or "").strip()
        if enabled_ts_param:
            enabled_toolsets = [t.strip() for t in enabled_ts_param.split(",") if t.strip()]
            disabled_toolsets = None
        else:
            enabled_toolsets = []
            disabled_toolsets = None  # empty list = no toolsets loaded
        agent = AIAgent(
            model=body.get("model", ""),
            session_id=body.get("session_id"),
            quiet_mode=True,
            enabled_toolsets=enabled_toolsets,
            disabled_toolsets=disabled_toolsets,
            max_iterations=max_iter,
        )
        response = agent.chat(message)
        return {
            "response": response,
            "session_id": body.get("session_id"),
            "model": body.get("model", ""),
        }
    except Exception as e:
        logger.exception("AIAgent.chat failed")
        raise HTTPException(status_code=500, detail=f"AIAgent error: {e}")


# ---- Icarus memory endpoints (plan 3: 跨会话记忆检索) ----
# Three endpoints designed for "agent wakes up, asks: what did I do last
# time?". The third (awake-briefing) is the one the agent calls
# automatically at the start of each new session.

_ICARUS_MEMORY_DIR = Path(
    os.environ.get(
        "ICARUS_MEMORY_DIR",
        str(Path(__file__).resolve().parent.parent / "data" / "hermes-agent" / "memories" / "icarus"),
    )
)


def _read_memory_files(max_files: int = 7) -> list[dict[str, Any]]:
    """Read the most recent N daily notes (sorted newest-first).
    Returns a list of {date, path, headline, body} dicts.
    Skips the dojo-*.md raw analysis files (those are evidence; the
    agent reads the narrative notes instead)."""
    if not _ICARUS_MEMORY_DIR.is_dir():
        return []
    files = sorted(
        [p for p in _ICARUS_MEMORY_DIR.glob("*.md")
         if not p.name.startswith("dojo-")],
        key=lambda p: p.name,
        reverse=True,
    )[:max_files]
    out: list[dict[str, Any]] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # Extract headline = first non-empty line after the # heading
        lines = text.splitlines()
        headline = ""
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                headline = stripped[:160]
                break
        out.append({
            "date": p.stem,  # YYYY-MM-DD
            "path": str(p.relative_to(Path(__file__).resolve().parent.parent)),
            "headline": headline,
            "size_bytes": len(text),
        })
    return out


@app.get("/v1/icarus/last-session")
async def icarus_last_session() -> dict[str, Any]:
    """Return the most recent Icarus daily note (full body). Useful for
    the agent to load on session start to remember what happened yesterday."""
    notes = _read_memory_files(1)
    if not notes:
        return {"found": False, "reason": "no memory files in _ICARUS_MEMORY_DIR"}
    latest = notes[0]
    full = (_ICARUS_MEMORY_DIR / f"{latest['date']}.md").read_text(
        encoding="utf-8", errors="ignore"
    )
    return {
        "found": True,
        "date": latest["date"],
        "path": latest["path"],
        "body": full,
    }


@app.get("/v1/icarus/memories")
async def icarus_memories(days: int = 7) -> dict[str, Any]:
    """Return an index of the most recent N daily notes (headlines only,
    not full bodies — to keep the response small for the SPA sidebar)."""
    notes = _read_memory_files(max(1, min(days, 90)))
    return {
        "count": len(notes),
        "memory_dir": str(_ICARUS_MEMORY_DIR.relative_to(
            Path(__file__).resolve().parent.parent
        )),
        "notes": notes,
    }


@app.get("/v1/icarus/awake-briefing")
async def icarus_awake_briefing() -> dict[str, Any]:
    """One-shot briefing for a freshly-awakened agent session. Composes:
      - Last 1 daily note headline
      - Last 3 daily note headlines (recency window)
      - Heartbeat summary: tick count + last 5 events since wake
      - Today's todo status (if any)
    Designed to be cheap (no model calls, all in-process reads)."""
    notes = _read_memory_files(max_files=3)
    last_full = notes[0] if notes else None
    last_full_body = ""
    if last_full:
        try:
            last_full_body = (
                _ICARUS_MEMORY_DIR / f"{last_full['date']}.md"
            ).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass

    # Heartbeat summary: last 5 events for the wake sequence
    heartbeat_path = Path(__file__).resolve().parent.parent / "data" / "logs" / "icarus-heartbeat.jsonl"
    recent_events: list[dict[str, Any]] = []
    if heartbeat_path.is_file():
        try:
            lines = heartbeat_path.read_text(
                encoding="utf-8", errors="ignore"
            ).strip().splitlines()[-5:]
            for line in lines:
                try:
                    ev = json.loads(line)
                    recent_events.append({
                        "event": ev.get("event"),
                        "ts": ev.get("ts"),
                    })
                except Exception:
                    pass
        except Exception:
            pass

    return {
        "last_session": {
            "date": last_full["date"] if last_full else None,
            "headline": last_full["headline"] if last_full else None,
            "body_excerpt": last_full_body[:2000] if last_full_body else "",
        },
        "recent_three_dates": [n["date"] for n in notes],
        "recent_three_headlines": [n["headline"] for n in notes],
        "heartbeat_recent": recent_events,
        "current_ts": time.time(),
        "memory_dir": str(_ICARUS_MEMORY_DIR.relative_to(
            Path(__file__).resolve().parent.parent
        )),
    }





# ---- Icarus session recovery (plan: UI 重启对话续接) ----
# UI 重启导致对话中断的解决方案分 3 部分:
# 1) /v1/icarus/active-session — 查 webui db 找到最近一个未结束的 session
# 2) /v1/icarus/session/{id}/tail — 取 session 最近 N 条消息
# 3) /v1/icarus/session/{id}/resume-context — 生成 system prompt 可注入新对话
#
# SPA 启动后会自动调 #1, 弹个 toast 让用户决定是否 resume

_WEBUI_DB_PATH = Path(os.environ.get(
    "HERMES_WEBUI_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "webui" / "hermes-web-ui.db"),
))


def _open_webui_db():
    """Open the webui SQLite DB (read-only safe). Returns None if missing."""
    if not _WEBUI_DB_PATH.is_file():
        return None
    # readonly via URI mode (no writes from bridge — write side is webui itself)
    uri = f"file:{_WEBUI_DB_PATH.as_posix()}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True, timeout=3)
    except Exception:
        return None


@app.get("/v1/icarus/active-session")
async def icarus_active_session(max_age_seconds: int = 86400 * 3) -> dict[str, Any]:
    """Find the most recent ACTIVE (not ended) session in webui db.
    Returns metadata + the last few messages so the SPA can show
    a 'Resume last conversation?' toast.

    max_age_seconds: only consider sessions whose last_active is within
    this window. Default 3 days — anything older is considered stale
    and not worth resuming.
    """
    con = _open_webui_db()
    if not con:
        return {"found": False, "reason": "webui db not accessible"}

    # webui db stores last_active in SECONDS (10-digit unix ts)
    now_sec = int(time.time())
    cutoff = now_sec - max_age_seconds

    try:
        cur = con.cursor()
        cur.execute("""
            SELECT id, title, source, agent, profile, model, provider,
                   message_count, started_at, last_active, workspace
            FROM sessions
            WHERE ended_at IS NULL AND last_active >= ?
            ORDER BY last_active DESC
            LIMIT 5
        """, (cutoff,))
        rows = cur.fetchall()
    except Exception as e:
        con.close()
        return {"found": False, "reason": f"db query error: {e}"}

    if not rows:
        con.close()
        return {"found": False, "reason": "no recent active session"}

    # The most recent active session
    r = rows[0]
    session_id, title, source, agent, profile, model, provider, msg_count, started_at, last_active, workspace = r
    age_seconds = (int(time.time()) - (last_active or int(time.time())))

    # Last 3 messages
    last_msgs: list[dict[str, Any]] = []
    try:
        cur.execute("""
            SELECT role, content, timestamp
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT 3
        """, (session_id,))
        for m in cur.fetchall():
            role, content, ts = m
            last_msgs.append({
                "role": role,
                "content_excerpt": (content or "")[:300],
                "ts": ts,
            })
        last_msgs.reverse()  # oldest first
    except Exception:
        pass

    con.close()

    return {
        "found": True,
        "session_id": session_id,
        "title": title,
        "source": source,
        "agent": agent,
        "profile": profile,
        "model": model,
        "provider": provider,
        "message_count": msg_count,
        "started_at": started_at,
        "last_active": last_active,
        "age_seconds": age_seconds,
        "age_human": f"{int(age_seconds//3600)}h{int((age_seconds%3600)//60)}m" if age_seconds < 86400 else f"{int(age_seconds//86400)}d{int((age_seconds%86400)//3600)}h",
        "last_messages": last_msgs,
        "workspace": workspace,
        "db_path": str(_WEBUI_DB_PATH.relative_to(
            Path(__file__).resolve().parent.parent
        )),
    }


@app.get("/v1/icarus/session/{session_id}/tail")
async def icarus_session_tail(
    session_id: str, limit: int = 10
) -> dict[str, Any]:
    """Return the last N messages of a session from webui db.
    Used to compose a 'previous conversation' summary for resume."""
    con = _open_webui_db()
    if not con:
        return {"found": False, "reason": "webui db not accessible"}

    limit = max(1, min(limit, 50))
    try:
        cur = con.cursor()
        cur.execute("""
            SELECT role, content, timestamp, tool_name
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (session_id, limit))
        rows = cur.fetchall()
    except Exception as e:
        con.close()
        return {"found": False, "reason": f"db error: {e}"}

    msgs = list(reversed([{
        "role": r[0],
        "content_excerpt": (r[1] or "")[:500],
        "ts": r[2],
        "tool_name": r[3],
    } for r in rows]))
    con.close()
    return {"found": True, "session_id": session_id, "messages": msgs}


@app.post("/v1/icarus/session/{session_id}/resume-context")
async def icarus_resume_context(session_id: str) -> dict[str, Any]:
    """Compose a system prompt describing the previous session so the
    agent can seamlessly pick up where it left off.

    Returns:
      {
        "system_prompt": "You were working with the user on ...

                          Last messages:
  user: ...
  assistant: ...

                          The user wants to continue from where we left off.",
        "session_id": "...",
        "summary": "...",
      }
    """
    # 1) get tail
    con = _open_webui_db()
    if not con:
        return {"found": False, "reason": "webui db not accessible"}

    try:
        cur = con.cursor()
        cur.execute(
            "SELECT title, message_count, last_active FROM sessions WHERE id = ?",
            (session_id,),
        )
        sess = cur.fetchone()
        if not sess:
            con.close()
            return {"found": False, "reason": "session not found"}

        title, msg_count, last_active = sess
        cur.execute("""
            SELECT role, content, tool_name
            FROM messages
            WHERE session_id = ?
              AND role IN ('user', 'assistant')
              AND content IS NOT NULL AND content != ''
            ORDER BY timestamp DESC
            LIMIT 8
        """, (session_id,))
        rows = cur.fetchall()
    finally:
        con.close()

    msgs = list(reversed(rows))  # oldest first

    # 2) compose system prompt
    if title:
        lines = [f"# Previous session: {title} ({msg_count} messages)"]
    else:
        lines = [f"# Previous session ({msg_count} messages)"]
    if last_active:
        from datetime import datetime
        try:
            # webui db stores last_active in SECONDS
            dt = datetime.fromtimestamp(int(last_active))
            lines.append(f"# Last active: {dt.isoformat()}")
        except Exception:
            pass
    lines.append("")
    lines.append("The user wants to continue this conversation. Recap where you left off, then ask what they want to do next.")
    lines.append("")
    lines.append("## Last messages")
    for role, content, tool_name in msgs:
        excerpt = (content or "")[:400].replace("\n", " ")
        lines.append(f"- **{role}**: {excerpt}")

    system_prompt = "\n".join(lines)

    # 3) short summary (first ~200 chars of last assistant message)
    summary = ""
    for role, content, _ in reversed(msgs):
        if role == "assistant" and content:
            summary = content[:300]
            break

    return {
        "found": True,
        "session_id": session_id,
        "title": title,
        "summary": summary,
        "system_prompt": system_prompt,
        "message_count": msg_count,
    }


# ---- Voice: real-time dialogue WebSocket ----


@app.websocket("/v1/voice/ws")
async def voice_websocket(websocket: WebSocket):
    """WebSocket for real-time voice dialogue.

    Uses browser MediaRecorder → Whisper API → LLM → edge-tts streaming.
    See bridge/voice_server.py for protocol details.
    """
    await _voice_ws_handler(websocket)


# ---- Lifecycle ----

# ---- Lifecycle ----



# ============ Neuro Control Endpoints (for desktop pet / web UI) ============


@app.get("/v1/neuro/status")
async def neuro_status():
    """Snapshot of Neuro runtime state — for UI dashboard / desktop pet."""
    return {
        "patience": icarus.patience,
        "time_since_last_message": round(icarus.time_since_last_message, 1),
        "history_len": len(icarus.history),
        "human_speaking": icarus.human_speaking,
        "AI_thinking": icarus.AI_thinking,
        "AI_speaking": icarus.AI_speaking,
        "stt_ready": icarus.stt_ready,
        "tts_ready": icarus.tts_ready,
        "llm_ready": icarus.llm_ready,
        "new_message": icarus.new_message,
        "remote_queue": len(icarus.recent_remote_messages),
        "sio_queue": len(icarus.sio_queue),
    }


@app.post("/v1/neuro/patience")
async def neuro_set_patience(body: dict):
    """Adjust PATIENCE threshold (seconds before AI speaks proactively)."""
    seconds = float(body.get("seconds", 30.0))
    seconds = max(5.0, min(600.0, seconds))  # 5s..10min
    icarus.patience = seconds
    try:
        from bridge.prompter import get_prompter
        get_prompter().patience = seconds
    except Exception:
        pass
    return {"patience": icarus.patience}


@app.post("/v1/neuro/reset")
async def neuro_reset_signals():
    """Reset speaking flags (e.g. after a stuck state)."""
    icarus.AI_thinking = False
    icarus.AI_speaking = False
    icarus.human_speaking = False
    icarus.new_message = False
    return {"reset": True}


@app.get("/v1/neuro/memories")
async def neuro_memories(limit: int = 50):
    """Browse reflection memories (long-term)."""
    try:
        from bridge.neuro import get_memory
        mem = get_memory()
        all_mems = mem.API.get_all()
        return {"count": mem.collection.count(), "memories": all_mems[-limit:]}
    except Exception as e:
        return {"error": str(e), "count": 0, "memories": []}


@app.post("/v1/neuro/memory/add")
async def neuro_memory_add(body: dict):
    """Manually inject a memory."""
    try:
        from bridge.neuro import get_memory
        mem = get_memory()
        doc = body.get("document", "").strip()
        if not doc:
            return {"error": "empty document"}
        meta = body.get("metadata", {"type": "manual"})
        mid = mem.API.create(doc, meta)
        return {"id": mid, "count": mem.collection.count()}
    except Exception as e:
        return {"error": str(e)}


@app.post("/v1/neuro/memory/delete")
async def neuro_memory_delete(body: dict):
    """Delete a memory by id."""
    try:
        from bridge.neuro import get_memory
        mem = get_memory()
        mem.API.delete(body.get("id", ""))
        return {"deleted": True, "count": mem.collection.count()}
    except Exception as e:
        return {"error": str(e)}


@app.post("/v1/neuro/patience/trigger")
async def neuro_patience_trigger():
    """Force a PATIENCE tick — AI should say something immediately.
    Useful for testing and for desktop pet to wake the AI up."""
    try:
        from bridge.prompter import get_prompter
        prompter = get_prompter()
        icarus.last_message_time = time.time() - prompter.patience - 1
        return {"triggered": True, "reason": "patience_idle (manual)"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/v1/neuro/proactive")
async def neuro_proactive():
    """Phase 4: 获取最近的 AI 主动消息列表.
    桌宠 / Neuro tray / WebUI SPA 都可以 polling 这个端点."""
    try:
        from bridge.prompter import get_recent_proactive_messages
        messages = get_recent_proactive_messages()
        return {"proactive_messages": messages, "count": len(messages)}
    except ImportError:
        # prompter 可能没加载
        return {"proactive_messages": [], "count": 0}
    except Exception as e:
        return {"error": str(e)}


# ============ End Neuro Endpoints ============

if __name__ == "__main__":
    import uvicorn  # type: ignore
    uvicorn.run(
        "bridge.server:app",
        host="127.0.0.1",
        port=BRIDGE_PORT,
        log_level="info",
    )