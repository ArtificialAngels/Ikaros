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
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from bridge import telemetry
from bridge.health import registry as health_registry

logger = logging.getLogger("hermes.bridge")

# Mark this process so signals know where they came from.
os.environ.setdefault("HERMES_MODULE", "bridge")

# ---- Config (all overridable via env vars) ----

LLAMA_BASE_URL = os.environ.get("HERMES_LLAMA_URL", "http://127.0.0.1:8080").rstrip("/")
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
_llama_health: dict[str, Any] = {
    "alive": False,
    "last_check": 0.0,
    "last_success": 0.0,
    "consecutive_failures": 0,
    "latency_ms": 0.0,
}
_health_lock = threading.Lock()


def _get_llama_health() -> dict[str, Any]:
    with _health_lock:
        return dict(_llama_health)


async def _check_llama_health() -> bool:
    """Probe llama-server and update the shared health state."""
    t0 = time.perf_counter()
    try:
        r = await _http.get("/health", timeout=3.0)
        alive = r.status_code == 200
        latency = (time.perf_counter() - t0) * 1000.0
    except Exception as e:
        alive = False
        latency = 0.0

    now = time.time()
    with _health_lock:
        _llama_health["last_check"] = now
        if alive:
            _llama_health["alive"] = True
            _llama_health["last_success"] = now
            _llama_health["consecutive_failures"] = 0
            _llama_health["latency_ms"] = round(latency, 1)
        else:
            _llama_health["consecutive_failures"] += 1
            _llama_health["latency_ms"] = 0.0
            # Only mark dead after 2 consecutive failures (avoid flapping)
            if _llama_health["consecutive_failures"] >= 2:
                _llama_health["alive"] = False

    # Also update the shared health registry for cross-component visibility
    health_registry.report("llama_server", alive=alive, latency_ms=round(latency, 1))
    health_registry.report("bridge", alive=True, extra={"port": BRIDGE_PORT, "pid": os.getpid()})

    return alive


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


# Single shared HTTP client for proxying to llama-server (keeps the connection
# pool warm and makes streaming responses efficient).
_http = httpx.AsyncClient(
    base_url=LLAMA_BASE_URL,
    timeout=httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=5.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)
_warmup_http_client = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=5.0))

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
    logger.info("bridge v0.5.0 started — llama=%s", _get_llama_health()["alive"])
    yield
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

    # ---- Smart routing ----
    routing_override = request.headers.get("X-Hermes-Routing", "").strip().lower()
    # Also check global env var (HERMES_ROUTING_MODE in .env)
    if not routing_override:
        routing_override = os.environ.get("HERMES_ROUTING_MODE", "auto").strip().lower()
    routing_decision = None

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
    if routing_decision is not None and routing_decision.route_target == "cloud_api":
        return await _handle_cloud_chat(body, routing_decision, is_stream)

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

    # Non-streaming
    try:
        r = await _retry_call(
            lambda: _http.post("/v1/chat/completions", json=body, timeout=300.0),
            label="chat",
        )
        telemetry.bus().emit(telemetry.Topics.CHAT_DONE, {
            "model": body.get("model", "?"),
            "status": r.status_code,
        })
        return JSONResponse(
            content=r.json(),
            status_code=r.status_code,
            headers={"X-Hermes-Routing-Target": "llama_server"},
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
) -> Any:
    """Handle a chat request routed to a cloud provider."""
    provider = getattr(decision, "cloud_provider", "") or "openai"
    model = body.get("model", "")

    # Use the body's model if specified, otherwise let the cloud client pick
    if not model or model == "auto":
        model = "gpt-4o"  # sensible default

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


# ---- Lifecycle ----


if __name__ == "__main__":
    import uvicorn  # type: ignore
    uvicorn.run(
        "bridge.server:app",
        host="127.0.0.1",
        port=BRIDGE_PORT,
        log_level="info",
    )