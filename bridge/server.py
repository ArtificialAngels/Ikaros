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
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from bridge import telemetry

logger = logging.getLogger("hermes.bridge")

# Mark this process so signals know where they came from.
os.environ.setdefault("HERMES_MODULE", "bridge")

# ---- Config (all overridable via env vars) ----

LLAMA_BASE_URL = os.environ.get("HERMES_LLAMA_URL", "http://127.0.0.1:8080").rstrip("/")
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path(__file__).resolve().parent.parent / "data" / "hermes-agent")))
MODELS_DIR = Path(os.environ.get("HERMES_MODELS_DIR", str(Path(__file__).resolve().parent.parent / "data" / "models")))
BRIDGE_PORT = int(os.environ.get("HERMES_BRIDGE_PORT", "7860"))

# Single shared HTTP client for proxying to llama-server (keeps the connection
# pool warm and makes streaming responses efficient).
_http = httpx.AsyncClient(
    base_url=LLAMA_BASE_URL,
    timeout=httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=5.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    telemetry.bus().emit(telemetry.Topics.MODULE_BOOT, {
        "module": "bridge",
        "version": "0.4.0",
        "port": BRIDGE_PORT,
        "pid": os.getpid(),
    })
    yield
    telemetry.bus().emit(telemetry.Topics.MODULE_SHUTDOWN, {"module": "bridge"})
    try:
        telemetry.log().flush()
    except Exception:
        pass
    _warmup_http_client.close()
    await _http.aclose()


app = FastAPI(
    title="Hermes Bridge",
    version="0.4.0",
    lifespan=lifespan,
    description=(
        "Thin glue layer between EKKOLearnAI hermes-web-ui (port :8648) and "
        "our local llama-server (port :8080) + upstream NousResearch "
        "hermes-agent v0.16.0. See bridge/README.md."
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
                        telemetry.log().flush()
                        _last_flush[0] = now
        except Exception:
            pass


# ---- /health ----

@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness + readiness probe. Three signals in one response."""
    llama_alive = False
    try:
        r = await _http.get("/health", timeout=2.0)
        llama_alive = r.status_code == 200
    except Exception:
        pass

    hermes_home_writable = HERMES_HOME.exists() and os.access(HERMES_HOME, os.W_OK)

    payload = {
        "status": "ok" if llama_alive else "degraded",
        "version": "0.4.0",
        "upstream": {
            "hermes_agent": "0.16.0",
            "llama_server": {
                "url": LLAMA_BASE_URL,
                "alive": llama_alive,
            },
            "hermes_home": {
                "path": str(HERMES_HOME),
                "exists": HERMES_HOME.exists(),
                "writable": hermes_home_writable,
            },
            "models_dir": str(MODELS_DIR),
        },
        "endpoints": [
            "/health",
            "/v1/signals", "/v1/signals/recent", "/v1/signals/stats",
            "/v1/signals/emit",
            "/v1/models",
            "/v1/models/load",
            "/v1/models/swap",
            "/v1/models/status",
            "/v1/models/evict",
            "/v1/models/warmup",
            "/v1/models/warmup/{warmup_id}",
            "/v1/chat/completions",
            "/v1/chat/completions/sse",
            "/api/chat/sessions",
            "/api/agent/run",
        ],
    }
    # Side-effect: emit a PORT_LISTEN-style signal on every health probe so
    # downstream consumers can build a heartbeat timeline.
    if llama_alive:
        telemetry.bus().emit(telemetry.Topics.PORT_LISTEN, {
            "module": "llm_engine",
            "url": LLAMA_BASE_URL,
        })
    return payload


# ---- /v1/signals (telemetry aggregation) ----

@app.get("/v1/signals")
async def signals_snapshot() -> dict[str, Any]:
    """Single-source aggregated telemetry snapshot.

    Combines in-process bus recent events, request log stats, llama-server
    /v1/models status, and runtime env (PID, ports). This is the primary
    endpoint the signal panel reads from.
    """
    # Probe llama-server in parallel-style (sequential async is fine — both
    # calls have short timeouts).
    llama_alive = False
    llama_models_count = 0
    try:
        r = await _http.get("/health", timeout=2.0)
        llama_alive = r.status_code == 200
    except Exception:
        pass
    try:
        r = await _http.get("/v1/models", timeout=2.0)
        if r.status_code == 200:
            llama_models_count = len(r.json().get("data", []) or [])
    except Exception:
        pass

    stats = telemetry.log().stats()
    recent = telemetry.bus().recent(limit=20)
    return {
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
            telemetry.log().flush()
        except Exception:
            pass
    return {"ok": True, "envelope": env}


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

@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    """List available models. Live-proxies llama-server's /v1/models.

    Falls back to scanning `data/models/*.gguf` via
    `modules.model_manager.gguf` if llama-server is unreachable (so the
    WebUI's model dropdown still works during cold boot).
    """
    try:
        r = await _http.get("/v1/models", timeout=5.0)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.warning("llama-server /v1/models unreachable (%s); falling back to local scan", e)

    # Fallback: scan GGUF directory using our own parser
    try:
        from modules.model_manager.gguf import list_gguf_models  # type: ignore
        models = list_gguf_models(MODELS_DIR)
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
    try:
        r = await _http.post("/models/load", json={"model": model}, timeout=30.0)
        ok = 200 <= r.status_code < 300
        # Signal: model loaded (or failed) — distinguish by HTTP status.
        telemetry.bus().emit(
            telemetry.Topics.MODEL_LOADED if ok else telemetry.Topics.MODULE_ERROR,
            {"model": model, "status": r.status_code, "via": "load"},
        )
        return JSONResponse(content=r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text},
                            status_code=r.status_code)
    except httpx.HTTPError as e:
        telemetry.bus().emit(telemetry.Topics.MODULE_ERROR, {
            "model": model, "via": "load", "error": str(e),
        })
        raise HTTPException(status_code=502, detail=f"llama-server unreachable: {e}")


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
        r = await _http.get("/v1/models", timeout=5.0)
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


# ---- /v1/models/warmup ----

# Warmup task registry — concurrent warmups are rare, so a single dict + lock
# is sufficient. Keys are warmup_id (uuid4), values are {state, models, results}.
_warmup_tasks: dict[str, dict[str, Any]] = {}
_warmup_lock = threading.Lock()
_warmup_http_client = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=5.0))
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


# ---- /v1/chat/completions ----

@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    """OpenAI-compatible chat completion endpoint.

    Proxies the request body verbatim to llama-server's /v1/chat/completions.
    Non-streaming: returns the full JSON response.
    Streaming (stream=true): forwards chunks as SSE.

    The bridge adds value beyond raw proxy:
    - Centralised logging of every request (see data/logs/bridge.log)
    - Future: model-id alias resolution (e.g. "3b" -> "Qwen2.5-3B-Instruct-...")
    - Future: per-request budget / context-length enforcement
    """
    body = await request.json()
    is_stream = bool(body.get("stream"))

    logger.info(
        "chat.completions model=%s stream=%s messages=%d",
        body.get("model", "?"), is_stream, len(body.get("messages", [])),
    )

    # Signal: chat request received (before upstream call).
    telemetry.bus().emit(telemetry.Topics.CHAT_REQUEST, {
        "model": body.get("model", "?"),
        "messages": len(body.get("messages", []) or []),
        "stream": is_stream,
    })

    if is_stream:
        return StreamingResponse(
            _stream_chat(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming
    try:
        r = await _http.post("/v1/chat/completions", json=body, timeout=300.0)
        # Signal: chat succeeded.
        telemetry.bus().emit(telemetry.Topics.CHAT_DONE, {
            "model": body.get("model", "?"),
            "status": r.status_code,
        })
        return JSONResponse(content=r.json(), status_code=r.status_code)
    except httpx.HTTPError as e:
        telemetry.bus().emit(telemetry.Topics.CHAT_ERROR, {
            "model": body.get("model", "?"),
            "error": str(e),
        })
        raise HTTPException(status_code=502, detail=f"llama-server unreachable: {e}")


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