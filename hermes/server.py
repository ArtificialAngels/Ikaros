"""
FastAPI web server for Hermes.

Provides the /api/* endpoints, built-in chat UI (/chat),
and OpenAI-compatible /v1/* shims for external clients.

Phase 1+2 (real SSE streaming + session persistence):
  - ``SessionStore`` (hermes.sessions) replaces the in-memory
    ``agent._chat_sessions`` dict; sessions live as JSON files under
    ``<data_dir>/sessions/<id>.json`` and survive process restarts.
  - ``POST /api/chat/start`` is a non-blocking entry point that returns
    a ``stream_id`` immediately and starts a background asyncio task to
    drive the LLM. ``GET /api/chat/stream/{stream_id}`` returns a real
    ``text/event-stream`` so the WebUI can render tokens as they arrive.
  - ``POST /api/chat/cancel/{stream_id}`` signals the background task
    to stop. ``/api/chat/send`` (legacy blocking) is kept for backward
    compatibility and other clients (CLI, tests).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

from hermes.sessions import SessionStore
from hermes.llm import Message, LLMResponse

logger = logging.getLogger("hermes.server")

# Hermes project root (parent of this hermes/ package)
HERMES_ROOT = Path(__file__).resolve().parent.parent

# Default port for llama-server (real LLM runtime). Used by /v1/models
# to live-proxy the OpenAI-compatible models listing when llama-server
# is up, and to construct a "this is the local LLM" indicator elsewhere.
LLAMA_PORT = int(os.environ.get("HERMES_LLAMA_PORT", "8080"))


# ---- Stream registry (in-process, scoped per app instance) -----------------
# Each entry: {
#   "id": str,
#   "session_id": str,
#   "queue": asyncio.Queue,  # pushed events for the SSE consumer
#   "cancel": asyncio.Event, # set by /api/chat/cancel
#   "task": asyncio.Task,    # background runner
#   "model": str,
#   "provider": str,
#   "created_at": float,
#   "done": bool,
# }
# The registry is intentionally simple: one process, one registry, no
# cross-process coordination needed. On restart, all in-flight streams
# are simply gone — the client's EventSource will get a 404 and the
# session will show whatever was persisted so far.

class _StreamRegistry:
    def __init__(self):
        self._streams: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def create(self, session_id: str, model: str, provider_hint: str = "") -> str:
        stream_id = "stream_" + uuid.uuid4().hex[:12]
        async with self._lock:
            self._streams[stream_id] = {
                "id": stream_id,
                "session_id": session_id,
                "queue": asyncio.Queue(maxsize=1024),
                "cancel": asyncio.Event(),
                "task": None,
                "model": model,
                "provider": provider_hint,
                "created_at": time.time(),
                "done": False,
            }
        return stream_id

    async def attach_task(self, stream_id: str, task: asyncio.Task) -> None:
        async with self._lock:
            s = self._streams.get(stream_id)
            if s:
                s["task"] = task

    def get(self, stream_id: str) -> dict | None:
        return self._streams.get(stream_id)

    async def cancel(self, stream_id: str) -> bool:
        s = self._streams.get(stream_id)
        if not s:
            return False
        s["cancel"].set()
        return True

    async def remove(self, stream_id: str) -> None:
        async with self._lock:
            self._streams.pop(stream_id, None)

    def stats(self) -> dict:
        return {
            "active": sum(1 for s in self._streams.values() if not s["done"]),
            "total": len(self._streams),
        }


# ---- Pydantic models for the SPA API ----

class ChatRequest(BaseModel):
    message: str
    remember: bool = True


class IngestRequest(BaseModel):
    path: str
    tag: str | None = None


class SearchRequest(BaseModel):
    query: str
    k: int = 5


class RememberRequest(BaseModel):
    text: str
    tags: list[str] = []


# ---- App factory ----

def create_app(agent) -> FastAPI:
    app = FastAPI(title="Hermes Agent", version=agent.config.agent.version)

    # Persistent session store (Phase 2). Lives at <data_dir>/sessions.
    # Falls back to a per-process dir if data paths are not available.
    _data_base = agent.paths.get("base") if isinstance(agent.paths, dict) else None
    if _data_base:
        _sessions_dir = Path(_data_base) / "sessions"
    else:
        _sessions_dir = HERMES_ROOT / "hermes" / "data" / "sessions"
    session_store = SessionStore(_sessions_dir)
    logger.info(f"SessionStore at {_sessions_dir}")

    # In-process SSE stream registry (Phase 1). Lost on restart; the
    # persisted session messages survive, so a refresh after restart
    # can still render whatever was completed before the crash.
    stream_registry = _StreamRegistry()

    @app.on_event("startup")
    async def startup():
        await agent.initialize()
        logger.info(f"SessionStore ready: {session_store.stats()}")

    # ---- API endpoints ----

    @app.get("/api/status")
    async def api_status():
        return {
            "version": agent.config.agent.version,
            "agent_name": agent.config.agent.name,
            "mode": agent._mode_str(),
            "llm_available": agent.cloud_available or agent.local_available or agent.mock_available,
            "cloud": agent.cloud_available,
            "local": agent.local_available,
            "mock": agent.mock_available,
            "memory": {
                **agent.memory.stats(),
                "recent": [
                    {"text": it.text, "tags": it.tags, "id": it.id}
                    for it in agent.memory.items[-30:]
                ] if hasattr(agent.memory, "items") else [],
            },
            "knowledge": agent.knowledge.stats(),
            "skills": [{"name": s["name"], "description": s.get("description",""), "category": "builtin" if s.get("path") is None else "custom"} for s in agent.skills.list()],
            "session": agent.session_id,
            "turn_count": agent.turn_count,
            "data_dir": str(agent.paths["base"]),
            "providers": [
                {"name": name, "url": getattr(p, "base_url", "?")}
                for name, p in agent.router.providers.items()
            ],
        }

    @app.get("/api/dashboard")
    async def api_dashboard():
        """Comprehensive dashboard status (inspired by ComfyUI-aki-v3 launcher)."""
        memory_stats = agent.memory.stats()
        kb_stats = agent.knowledge.stats()
        skills_list = agent.skills.list()

        # Network / mirror status
        network_info = {}
        try:
            from hermes.mirror import get_mirror_config
            mc = get_mirror_config()
            network_info = {
                "proxy": mc.proxy_address or "none",
                "mirror_pypi": mc.mirror_pypi,
                "mirror_huggingface": mc.mirror_huggingface,
                "mirror_git": mc.mirror_git,
                "pypi_mirror": mc.pypi_mirror if mc.mirror_pypi else "direct",
                "hf_mirror": mc.hf_mirror if mc.mirror_huggingface else "direct",
            }
        except Exception:
            network_info = {"proxy": "unknown", "mirror_pypi": False, "mirror_huggingface": False, "mirror_git": False}

        # Download support
        download_info = {}
        try:
            from hermes.download import find_aria2c
            a2 = find_aria2c()
            download_info = {"aria2c": str(a2) if a2 else None, "gopeed": None}
        except Exception:
            download_info = {"aria2c": None, "gopeed": None}

        return {
            "system": {
                "name": agent.config.agent.name,
                "version": agent.config.agent.version,
                "platform": sys.platform,
                "python": sys.version.split()[0],
                "data_dir": str(agent.paths["base"]),
            },
            "llm": {
                "mode": agent._mode_str(),
                "cloud_available": agent.cloud_available,
                "local_available": agent.local_available,
                "mock_available": agent.mock_available,
                "providers": [
                    {"name": name, "url": getattr(p, "base_url", "?")}
                    for name, p in agent.router.providers.items()
                ],
                "turn_count": agent.turn_count,
            },
            "components": {
                "memory": {
                    "backend": agent.config.memory.backend,
                    "total_items": memory_stats.get("total_items", 0),
                    "path": str(agent.paths["memory"]),
                },
                "knowledge": {
                    "total_chunks": kb_stats.get("total_chunks", 0),
                    "total_sources": kb_stats.get("total_sources", 0),
                    "chunk_size": agent.config.knowledge.chunk_size,
                },
                "skills": {
                    "count": len(skills_list),
                    "list": [{
                        "name": s["name"],
                        "description": s.get("description", ""),
                        "category": "builtin" if s.get("path") is None else "custom",
                    } for s in skills_list],
                },
            },
            "network": network_info,
            "download": download_info,
        }

    @app.get("/api/config")
    async def api_config():
        return agent.config.model_dump()

    @app.get("/api/config/defaults")
    async def api_config_defaults():
        return agent.config.model_dump()  # same as config for now

    @app.get("/api/config/schema")
    async def api_config_schema():
        # Return a simple schema (the SPA will render fields)
        return {
            "fields": {
                "agent.name": {"type": "string", "default": "hermes"},
                "agent.persona": {"type": "text", "default": ""},
                "llm.router.on_timeout_ms": {"type": "int", "default": 8000},
                "server.port": {"type": "int", "default": 7860},
                "memory.max_results": {"type": "int", "default": 5},
                "knowledge.chunk_size": {"type": "int", "default": 500},
            },
            "category_order": ["agent", "llm", "memory", "knowledge", "server"],
        }

    @app.put("/api/config")
    async def api_save_config(req: dict):
        # In a real implementation, save to config file
        logger.info(f"Config update requested: {list(req.keys())}")
        return {"ok": True}

    @app.get("/api/config/raw")
    async def api_config_raw():
        from hermes.config import _expand_env
        import yaml
        # Read raw YAML
        cfg_path = Path(agent.paths["base"]).parent / "config" / "hermes.yaml"
        if cfg_path.exists():
            return {"yaml": cfg_path.read_text(encoding="utf-8")}
        return {"yaml": ""}

    @app.get("/api/env")
    async def api_env():
        import os
        env_keys = [
            ("OPENAI_API_KEY", "OpenAI", "openai.com"),
            ("ANTHROPIC_API_KEY", "Anthropic", "anthropic.com"),
            ("OPENROUTER_API_KEY", "OpenRouter", "openrouter.ai"),
            ("GOOGLE_API_KEY", "Google Gemini", "ai.google.dev"),
        ]
        return {
            k: {
                "is_set": bool(os.environ.get(k)),
                "redacted_value": ("sk-***" + os.environ.get(k, "")[-4:]) if os.environ.get(k) else None,
                "description": desc,
                "url": url,
                "category": "llm",
                "is_password": True,
                "tools": [],
                "advanced": False,
            }
            for k, desc, url in env_keys
        }

    @app.put("/api/env")
    async def api_set_env(req: dict):
        import os
        key = req.get("key", "")
        value = req.get("value", "")
        if not key:
            raise HTTPException(400, "key required")
        # Write to .env file
        env_file = Path(agent.paths["base"]).parent / ".env"
        try:
            lines = []
            if env_file.exists():
                lines = env_file.read_text(encoding="utf-8").splitlines()
            # Replace or append
            found = False
            for i, line in enumerate(lines):
                if line.startswith(f"{key}="):
                    lines[i] = f"{key}={value}"
                    found = True
                    break
            if not found:
                lines.append(f"{key}={value}")
            env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return {"ok": True}
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.delete("/api/env")
    async def api_del_env(req: dict):
        import os
        key = req.get("key", "")
        if not key:
            raise HTTPException(400, "key required")
        os.environ.pop(key, None)
        return {"ok": True}

    @app.get("/api/skills")
    async def api_skills():
        return [
            {
                "name": s.name,
                "description": s.description,
                "category": "builtin" if s.path is None else "custom",
                "enabled": True,
            }
            for s in agent.skills.skills.values()
        ]

    @app.put("/api/skills/toggle")
    async def api_toggle_skill(req: dict):
        return {"ok": True}

    @app.get("/api/model/info")
    async def api_model_info():
        return {
            "model": "qwen3.5-35b-a3b",
            "provider": "local",
            "auto_context_length": 4096,
            "config_context_length": 4096,
            "effective_context_length": 4096,
            "capabilities": {
                "supports_tools": True,
                "supports_vision": False,
                "context_window": 32768,
                "model_family": "qwen3.5",
            },
        }

    @app.get("/api/model/options")
    async def api_model_options():
        return {
            "providers": [
                {"name": "local", "slug": "local", "is_current": True, "total_models": 1, "models": ["Qwen2.5-3B-Instruct"]},
                {"name": "openai", "slug": "openai", "total_models": 0, "models": []},
                {"name": "anthropic", "slug": "anthropic", "total_models": 0, "models": []},
            ],
            "model": "Qwen2.5-3B-Instruct",
            "provider": "local",
        }

    @app.get("/api/model/auxiliary")
    async def api_aux_models():
        return {"main": {"provider": "local", "model": "qwen2.5-3b-instruct"}, "tasks": []}

    @app.post("/api/model/set")
    async def api_set_model(req: dict):
        return {"ok": True, "scope": req.get("scope"), "provider": req.get("provider"), "model": req.get("model")}

    @app.get("/api/memory")
    async def api_memory():
        stats = agent.memory.stats()
        return {
            "active": "hermes",
            "providers": [
                {"name": "hermes", "description": "Built-in JSONL store", "configured": True},
                {"name": "hash", "description": "In-memory hash-based (testing only)", "configured": True},
            ],
            "builtin_files": {"memory": stats["total_items"], "user": 0},
        }

    @app.post("/api/memory/reset")
    async def api_memory_reset(req: dict):
        target = req.get("target", "all")
        if target in ("all", "memory"):
            agent.memory.clear()
        return {"ok": True, "deleted": [target]}

    @app.post("/api/chat")
    async def api_chat(req: ChatRequest):
        try:
            reply = await agent.chat(req.message, remember=req.remember)
            return {"reply": reply, "ok": True}
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.post("/api/task")
    async def api_task(req: dict):
        """Autonomous plan-and-execute a goal.

        Body: {"goal": "...", "wait": true|false}
        - wait=true: returns the final result (blocking, may take minutes)
        - wait=false: returns a task_id immediately, poll /api/task/{id}
        """
        goal = req.get("goal", "").strip()
        if not goal:
            raise HTTPException(400, "goal required")
        wait = bool(req.get("wait", True))

        if wait:
            try:
                result = await agent.run_task(goal)
                return {"ok": True, "result": result.to_dict()}
            except Exception as e:
                raise HTTPException(500, str(e))
        else:
            # Fire-and-forget: spawn task, return id
            import asyncio
            import uuid
            task_id = str(uuid.uuid4())[:8]
            # Store placeholder
            if not hasattr(agent, "_async_tasks"):
                agent._async_tasks = {}
            agent._async_tasks[task_id] = {"status": "running", "goal": goal, "result": None}
            async def _run_and_store():
                try:
                    result = await agent.run_task(goal)
                    agent._async_tasks[task_id] = {
                        "status": "done" if result.success else "failed",
                        "goal": goal, "result": result.to_dict(),
                    }
                except Exception as e:
                    agent._async_tasks[task_id] = {"status": "error", "error": str(e)}
            asyncio.create_task(_run_and_store())
            return {"ok": True, "task_id": task_id, "status": "running"}

    @app.get("/api/task/{task_id}")
    async def api_task_status(task_id: str):
        tasks = getattr(agent, "_async_tasks", {})
        if task_id not in tasks:
            raise HTTPException(404, f"task {task_id} not found")
        return tasks[task_id]

    @app.get("/api/sessions")
    async def api_sessions(limit: int = 20, offset: int = 0):
        # Return recent memories as "sessions"
        items = list(reversed(agent.memory.items))
        total = len(items)
        sliced = items[offset:offset+limit]
        return {
            "sessions": [
                {
                    "id": it.id,
                    "title": it.text[:60] + "...",
                    "preview": it.text[:120],
                    "started_at": int(it.created_at),
                    "ended_at": int(it.last_access),
                    "last_active": int(it.last_access),
                    "is_active": False,
                    "message_count": 1,
                    "tool_call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
                for it in sliced
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/logs")
    async def api_logs(file: str = None, lines: int = 100, level: str = "ALL", component: str = "all"):
        log_dir = Path(agent.paths["logs"])
        target = log_dir / (file or "hermes.log")
        if not target.exists():
            return {"file": str(target), "lines": []}
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            file_lines = content.splitlines()
            return {"file": str(target), "lines": file_lines[-lines:]}
        except Exception as e:
            return {"file": str(target), "lines": [f"Error: {e}"]}

    @app.get("/api/analytics/usage")
    async def api_analytics_usage(days: int = 30):
        return {
            "daily": [],
            "by_model": [],
            "totals": {
                "total_input": 0, "total_output": 0, "total_estimated_cost": 0,
                "total_sessions": 0, "total_api_calls": 0,
            },
            "skills": {"summary": {}, "top_skills": []},
        }

    @app.get("/api/analytics/models")
    async def api_analytics_models(days: int = 30):
        return {"models": [], "totals": {}, "period_days": days}

    # ---- Hermes WebUI integration (nesquena/hermes-webui) ----
    # These routes must be registered BEFORE the /api/{path:path} catch-all
    # below; otherwise the catch-all swallows them and returns its stub.
    # The client-side adapter (static/api-adapter.js) translates the new
    # UI's expected endpoints onto our /api/chat/* + /v1/* backends.

    @app.get("/", include_in_schema=False)
    async def root_fallback():
        # Serve the Hermes WebUI (nesquena/hermes-webui) from hermes/static/.
        # Static dir is required — if missing, the install is broken; 503 it.
        static_dir = HERMES_ROOT / "hermes" / "static"
        if not static_dir.is_dir() or not (static_dir / "index.html").is_file():
            return HTMLResponse(
                "<h1>Hermes WebUI not installed</h1>"
                "<p>Expected hermes/static/index.html — reinstall the hermes-webui assets.</p>",
                status_code=503,
            )
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        # Replace server-side template placeholders the new UI expects.
        html = html.replace("__WEBUI_VERSION__", "1.0.0-hermes")
        html = html.replace("__MAX_UPLOAD_BYTES__", str(50 * 1024 * 1024))
        html = html.replace("__CSRF_TOKEN_JSON__", "null")
        return HTMLResponse(html)

    @app.get("/api/webui/settings")
    async def webui_settings_get():
        return {}

    @app.post("/api/webui/settings")
    async def webui_settings_post(req: dict = None):
        return {"ok": True, "settings": req or {}}

    @app.get("/api/webui/profile/active")
    async def webui_profile_active():
        return {"name": "default", "is_default": True}

    @app.get("/api/webui/profiles")
    async def webui_profiles():
        return {"profiles": [{"name": "default", "is_default": True}]}

    @app.get("/api/webui/auth/status")
    async def webui_auth_status():
        return {"enabled": False, "user": None, "mode": "open"}

    @app.get("/api/webui/dashboard/config")
    async def webui_dashboard_config_get():
        return {"config": {}}

    @app.post("/api/webui/dashboard/config")
    async def webui_dashboard_config_post(req: dict = None):
        return {"ok": True, "config": req or {}}

    @app.get("/api/webui/workspaces")
    async def webui_workspaces():
        return {"workspaces": []}

    @app.post("/api/webui/session/new")
    async def webui_session_new(req: dict = None):
        sid = "sess_" + uuid.uuid4().hex[:12]
        return {
            "session": {
                "session_id": sid,
                "id": sid,
                "title": (req or {}).get("title", "New chat"),
                "created_at": time.time(),
                "updated_at": time.time(),
                "message_count": 0,
                "profile": "default",
                "archived": False,
                "pinned": False,
                "messages": [],
            }
        }

    @app.post("/api/webui/session/rename")
    async def webui_session_rename(req: dict = None):
        req = req or {}
        sid = req.get("session_id") or req.get("sessionKey") or ""
        title = req.get("title") or "Chat"
        if sid:
            data = session_store.get(sid)
            if data is not None:
                data["title"] = title
                await session_store.save(sid, data)
                # Keep legacy mirror consistent
                if hasattr(agent, "_chat_sessions"):
                    agent._chat_sessions[sid] = data
        return {"ok": True, "session_id": sid, "title": title}

    @app.api_route("/api/webui/noop", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def webui_noop():
        """Catch-all stub for endpoints the new UI calls but we don't implement."""
        return {"ok": True}

    # ---- Catch-all for unknown API endpoints (SPA graceful degradation) ----

    @app.get("/api/{path:path}")
    @app.post("/api/{path:path}")
    @app.put("/api/{path:path}")
    @app.delete("/api/{path:path}")
    async def api_fallback(path: str, request: Request):
        """Return safe empty defaults for unimplemented API endpoints."""
        return JSONResponse(
            status_code=200,
            content={"ok": True, "_stub": True, "endpoint": f"/api/{path}"}
        )

    # ---- Simple API (legacy, also used by the basic UI) ----

    @app.get("/health")
    async def health():
        """Liveness probe — returns JSON status (for monitoring/load balancers)."""
        return {
            "status": "ok",
            "version": "2.0.0",
            "cloud_available": getattr(agent, "cloud_available", None),
            "local_available": getattr(agent, "local_available", None),
            "mode": agent._mode_str() if hasattr(agent, "_mode_str") else "unknown",
        }

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "version": agent.config.agent.version}

    # ---- Model launcher UI (replaces deprecated web_dist admin SPA) ----

    LAUNCHER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Hermes - Model Launcher</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      background: #0a0e14; color: #e6edf3; margin: 0; padding: 24px;
      max-width: 980px; margin: 0 auto;
    }
    h1 { font-size: 22px; margin: 0 0 4px 0; }
    .sub { color: #8b95a1; font-size: 13px; margin-bottom: 20px; }
    .row {
      background: #151b23; border: 1px solid #1f2937; border-radius: 8px;
      padding: 14px 16px; margin-bottom: 10px; display: flex;
      align-items: center; gap: 16px;
    }
    .row.active { border-color: #2dd4bf; background: #0e1e1c; }
    .badge {
      display: inline-block; padding: 2px 8px; border-radius: 4px;
      font-size: 11px; font-weight: 600;
    }
    .badge.active { background: #2dd4bf; color: #042f2e; }
    .badge.idle { background: #374151; color: #9ca3af; }
    .name { flex: 1; font-weight: 500; font-family: ui-monospace, Menlo, monospace; font-size: 13px; word-break: break-all; }
    .meta { color: #8b95a1; font-size: 12px; }
    .meta b { color: #cbd5e1; font-weight: 500; }
    button {
      background: #2563eb; color: white; border: none; padding: 6px 14px;
      border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 500;
    }
    button:hover { background: #1d4ed8; }
    button:disabled { background: #374151; cursor: not-allowed; }
    button.switch { background: #2dd4bf; color: #042f2e; }
    button.switch:hover { background: #14b8a6; }
    .toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
    .toolbar a { color: #2dd4bf; text-decoration: none; font-size: 12px; padding: 6px 12px; background: #1f2937; border-radius: 4px; }
    .toolbar a:hover { background: #374151; }
    .dl-input { display: flex; gap: 6px; margin-top: 12px; }
    .dl-input input {
      flex: 1; background: #0a0e14; color: #e6edf3; border: 1px solid #374151;
      padding: 6px 10px; border-radius: 4px; font-size: 12px;
    }
    .msg { padding: 8px 12px; border-radius: 4px; margin: 10px 0; font-size: 12px; }
    .msg.ok { background: #064e3b; color: #6ee7b7; }
    .msg.err { background: #7f1d1d; color: #fca5a5; }
    .footer { color: #6b7280; font-size: 11px; margin-top: 24px; text-align: center; }
  </style>
</head>
<body>
  <h1>Hermes Model Launcher</h1>
  <p class="sub">Local GGUF models in <code>data/models</code>. Click <b>Switch</b> to load (restarts llama-server).</p>
  <div class="toolbar">
    <a href="/launcher?refresh=1">Refresh</a>
    <a href="/launcher?add=1">+ Add Model (URL)</a>
    <a href="/health" target="_blank">Health</a>
    <a href="/api/status" target="_blank">Status</a>
  </div>
  <div id="msg"></div>
  <div id="models">{{MODELS_HTML}}</div>
  <div class="footer">Hermes Agent v{{VERSION}} &middot; <a href="/" style="color:#8b95a1">home</a></div>
  <script>
    function showMsg(text, isErr) {
      var el = document.getElementById('msg');
      el.innerHTML = '<div class="msg ' + (isErr ? 'err' : 'ok') + '">' + text + '</div>';
      setTimeout(function() { el.innerHTML = ''; }, 5000);
    }
    function switchModel(name, btn) {
      if (!confirm('Switch to ' + name + '? This restarts llama-server (~30s).')) return;
      btn.disabled = true;
      btn.textContent = 'Switching...';
      fetch('/launcher/switch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name})
      }).then(r => r.json()).then(j => {
        if (j.ok) {
          showMsg('Switched. Reloading in 3s...', false);
          setTimeout(function() { location.reload(); }, 3000);
        } else {
          showMsg('Failed: ' + (j.error || 'unknown'), true);
          btn.disabled = false; btn.textContent = 'Switch';
        }
      }).catch(e => {
        showMsg('Error: ' + e, true);
        btn.disabled = false; btn.textContent = 'Switch';
      });
    }
    function downloadModel() {
      var url = document.getElementById('dl-url').value.trim();
      var name = document.getElementById('dl-name').value.trim();
      if (!url) { showMsg('URL required', true); return; }
      fetch('/launcher/download', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url: url, name: name})
      }).then(r => r.json()).then(j => {
        if (j.ok) {
          showMsg('Download task started: ' + j.task_id + ' (watch http://127.0.0.1:9999)', false);
        } else {
          showMsg('Failed: ' + (j.error || 'unknown'), true);
        }
      });
    }
  </script>
</body>
</html>"""

    @app.get("/launcher", include_in_schema=False)
    async def launcher(request: Request, refresh: int = 0, add: int = 0):
        """Multi-model launcher UI (replaces the deprecated web_dist SPA)."""
        try:
            from hermes.gguf import list_gguf_models, current_model_from_bat
            models = list_gguf_models(HERMES_ROOT / "data" / "models")
            current = current_model_from_bat(HERMES_ROOT) or ""
        except Exception as e:
            return HTMLResponse(f"<h1>launcher error</h1><pre>{e}</pre>", status_code=500)

        rows = []
        for m in models:
            mark_active = current and current in m["name"]
            arch = (m.get("arch") or "?")
            ctx = m.get("ctx_len", 0)
            ctx_str = f"{ctx // 1024}K" if ctx else "?"
            nt = m.get("n_tensors", 0)
            quant = m.get("quant", "?")
            sz = m.get("size_gb", 0)
            row_class = "row active" if mark_active else "row"
            badge = '<span class="badge active">ACTIVE</span>' if mark_active else '<span class="badge idle">idle</span>'
            btn = ('<button class="switch" disabled>current</button>' if mark_active
                   else f'<button class="switch" onclick="switchModel(\'{m["name"]}\', this)">Switch</button>')
            rows.append(
                f'<div class="{row_class}">'
                f'{badge}'
                f'<div class="name">{m["name"]}</div>'
                f'<div class="meta"><b>{sz} GB</b> &middot; <b>{quant}</b> &middot; arch=<b>{arch}</b> &middot; ctx=<b>{ctx_str}</b> &middot; tensors=<b>{nt}</b></div>'
                f'{btn}'
                f'</div>'
            )
        models_html = "\n".join(rows) if rows else "<p>No models. Click <b>+ Add Model</b> to download one.</p>"

        download_form = ""
        if add:
            download_form = (
                '<div class="row">'
                '<div class="name">Add Model via gopeed-web</div></div>'
                '<div class="dl-input">'
                '<input id="dl-url" placeholder="https://huggingface.co/.../model.gguf" />'
                '<input id="dl-name" placeholder="filename.gguf (optional)" style="max-width:240px" />'
                '<button onclick="downloadModel()">Start Download</button>'
                '</div>'
            )

        html = LAUNCHER_HTML.replace("{{MODELS_HTML}}", models_html + download_form)
        html = html.replace("{{VERSION}}", agent.config.agent.version)
        return HTMLResponse(html)

    @app.post("/launcher/switch")
    async def launcher_switch(req: dict):
        """Switch active model. Spawns switch-model.bat, returns when llama-server is ready."""
        name = (req.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "name required"}
        bat = HERMES_ROOT / "bin" / "switch-model.bat"
        if not bat.exists():
            return {"ok": False, "error": f"{bat} not found"}
        try:
            proc = await asyncio.create_subprocess_exec(
                "cmd.exe", "/c", str(bat), name,
                cwd=str(HERMES_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return {
                "ok": proc.returncode == 0,
                "rc": proc.returncode,
                "stdout_tail": (stdout or b"").decode("utf-8", errors="ignore")[-500:],
                "stderr_tail": (stderr or b"").decode("utf-8", errors="ignore")[-500:],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/launcher/download")
    async def launcher_download(req: dict):
        """Create a gopeed-web download task (Python communication bridge)."""
        url = (req.get("url") or "").strip()
        name = (req.get("name") or "").strip() or None
        if not url:
            return {"ok": False, "error": "url required"}
        try:
            from hermes.gopeed_client import GopeedClient
            c = GopeedClient()
            if not c.available():
                return {"ok": False, "error": "gopeed-web unreachable on :9999"}
            models_dir = HERMES_ROOT / "data" / "models"
            # gopeed-web 'path' is a directory; 'name' is the filename
            save_dir = str(models_dir).replace("\\", "/")
            task_id = c.create_task(url, save_dir=save_dir, name=name)
            return {"ok": True, "task_id": task_id, "save_dir": save_dir, "name": name}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.get("/status")
    async def status():
        return {
            "mode": agent._mode_str(),
            "llm_available": agent.cloud_available or agent.local_available or agent.mock_available,
            "cloud": agent.cloud_available,
            "local": agent.local_available,
            "memory": agent.memory.stats(),
            "knowledge": agent.knowledge.stats(),
            "skills": [s["name"] for s in agent.skills.list()],
            "session": agent.session_id,
            "turn_count": agent.turn_count,
        }

    @app.post("/chat")
    async def chat(req: ChatRequest):
        try:
            reply = await agent.chat(req.message, remember=req.remember)
            return {"reply": reply}
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest):
        async def gen():
            try:
                ctx = await agent.build_context(req.message)
                messages = [
                    agent.llm.Message("system", f"{ctx['system']}\n\nMEMORIES:\n{ctx['memory']}\n\nKB:\n{ctx['knowledge']}"),
                    agent.llm.Message("user", req.message),
                ]
                stream = await agent.router.chat(messages, stream=True)
                async for chunk in stream:
                    yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/memory")
    async def remember(req: RememberRequest):
        item = await agent.memory.remember(req.text, tags=req.tags)
        return {"id": item.id}

    @app.post("/kb/ingest")
    async def ingest(req: IngestRequest):
        try:
            count = agent.knowledge.ingest(req.path, tag=req.tag)
            return {"chunks_added": count}
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/kb/search")
    async def search(req: SearchRequest):
        results = await agent.knowledge.search(req.query, k=req.k)
        return {
            "results": [
                {"text": c.text, "source": c.source, "score": s, "metadata": c.metadata}
                for c, s in results
            ]
        }

    # ---- API shims ----

    @app.get("/api/ping")
    async def ws_ping():
        """Health check endpoint (workspace-ui compatible)."""
        return {"ok": True, "status": 200}

    @app.get("/api/sessions")
    async def ws_sessions(sessionKey: str = "", friendlyId: str = ""):
        """List sessions (Phase 2: SessionStore-backed; workspace-ui shape)."""
        out = []
        for s in session_store.list(limit=200):
            ts_updated_ms = int(s.get("updated_at", 0) * 1000)
            ts_created_ms = int(s.get("created_at", 0) * 1000)
            out.append({
                "key": s["id"],
                "friendlyId": s["id"],
                "title": s.get("title") or "Chat",
                "lastMessage": s.get("last_message", "")[:100],
                "messageCount": s.get("message_count", 0),
                "updatedAt": ts_updated_ms,
                "createdAt": ts_created_ms,
            })
        return {"sessions": out}

    @app.get("/api/history")
    async def ws_history(sessionKey: str = "", friendlyId: str = "", limit: int = 1000):
        """Get message history (Phase 2: SessionStore-backed)."""
        session_id = sessionKey or friendlyId
        messages = []
        if session_id:
            data = session_store.get(session_id)
            if data:
                for m in data.get("messages", []):
                    content = m.get("content", "")
                    messages.append({
                        "role": m.get("role", "assistant"),
                        "content": [{"type": "text", "text": content}],
                        "timestamp": m.get("timestamp", 0),
                    })
        return {"sessionKey": session_id, "friendlyId": session_id, "messages": messages}

    @app.delete("/api/sessions")
    async def ws_delete_session(sessionKey: str = ""):
        """Delete a session (Phase 2: SessionStore-backed)."""
        ok = await session_store.delete(sessionKey) if sessionKey else False
        if hasattr(agent, "_chat_sessions") and sessionKey:
            agent._chat_sessions.pop(sessionKey, None)
        return {"ok": ok, "sessionKey": sessionKey, "deleted": ok}

    # ===================================================================
    # Real SSE streaming + SessionStore-backed chat endpoints (Phase 1+2)
    # ===================================================================
    # These endpoints replace the previous blocking /api/chat/send with
    # a non-blocking pattern that survives process restarts.
    #
    # Flow:
    #   1. Client POSTs to /api/chat/start with {message, session_id?, model?, ...}
    #   2. Server returns {stream_id, session_id, effective_model, ...} immediately
    #      and starts a background asyncio task that:
    #        - calls agent.llm.stream_chat(messages)
    #        - pushes each delta onto an asyncio.Queue
    #        - patches the persisted session message after every chunk
    #   3. Client opens GET /api/chat/stream/{stream_id} (text/event-stream)
    #      and receives:  data: {"type":"delta","content":"..."}
    #                    data: {"type":"done","session_id":"..."}
    #   4. Client may POST /api/chat/cancel/{stream_id} to abort
    #
    # The legacy /api/chat/send is kept below for backward compat
    # (CLI, tests, scripts that don't want to handle SSE).
    # -------------------------------------------------------------------

    async def _stream_runner(
        stream_id: str,
        session_id: str,
        user_message: str,
        model_hint: str,
        profile: str,
    ) -> None:
        """Background coroutine: drive the LLM, push SSE events, persist
        each delta to the SessionStore. Runs until the LLM finishes,
        raises, or the cancel event is set.
        """
        entry = stream_registry.get(stream_id)
        if not entry:
            logger.error(f"[_stream_runner] stream {stream_id} vanished before start")
            return
        queue: asyncio.Queue = entry["queue"]
        cancel: asyncio.Event = entry["cancel"]
        provider_hint = entry.get("provider", "")
        full_text_chunks: list[str] = []
        last_persist_len = 0

        async def _push(event: dict) -> None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop the event but keep streaming so the
                # next chunk has a slot. We log so this is visible.
                logger.warning(f"[_stream_runner] queue full on stream {stream_id}; dropped event")

        try:
            # Build context (system + memory + KB) exactly like agent.chat does
            ctx = await agent.build_context(user_message)
            messages = [
                Message("system", f"{ctx['system']}\n\nRELEVANT MEMORIES:\n{ctx['memory']}\n\nRELEVANT KNOWLEDGE:\n{ctx['knowledge']}"),
                Message("user", user_message),
            ]
            # Push the initial "starting" event so the UI knows the
            # connection is live even if the first token takes a while.
            await _push({
                "type": "starting",
                "stream_id": stream_id,
                "session_id": session_id,
                "model": model_hint or None,
                "provider": provider_hint or None,
            })

            # Persist a placeholder assistant message so chat history
            # always shows an in-flight row.
            await session_store.append_message(
                session_id, "assistant", "",
                model=model_hint or None, provider=provider_hint or None,
                stream_id=stream_id, in_progress=True,
            )

            # The actual streaming loop. We walk chunks one at a time,
            # checking the cancel flag between them.
            try:
                # Snapshot attributes for the post-loop "done" event
                provider_name = ""
                model_name = model_hint
                # We need access to which provider served the request;
                # router.stream_chat() sets self._last_stream_provider
                # on the router. We pull it AFTER the loop terminates.
                async for delta in agent.router.stream_chat(
                    messages, model_hint=model_hint or None,
                ):
                    if cancel.is_set():
                        await _push({"type": "cancelled", "stream_id": stream_id})
                        break
                    full_text_chunks.append(delta)
                    await _push({
                        "type": "delta",
                        "content": delta,
                        "stream_id": stream_id,
                    })
                    # Persist incrementally — but only every N chars to
                    # avoid disk thrash on token-by-token models.
                    cumulative = "".join(full_text_chunks)
                    if len(cumulative) - last_persist_len >= 32:
                        await session_store.patch_message(
                            session_id, -1, content=cumulative,
                        )
                        last_persist_len = len(cumulative)
                else:
                    # Stream finished without a cancel
                    pass
            except Exception as llm_err:
                logger.error(f"[_stream_runner] LLM error on {stream_id}: {llm_err}")
                await _push({
                    "type": "error",
                    "error": str(llm_err),
                    "stream_id": stream_id,
                })

            # Final persistence: write the full accumulated text
            final_text = "".join(full_text_chunks)
            provider_name = getattr(agent.router, "_last_stream_provider", provider_hint or "unknown")
            model_name = getattr(agent.router, "_last_stream_model", model_hint or "unknown")
            await session_store.patch_message(
                session_id, -1,
                content=final_text,
                in_progress=False,
                cancelled=cancel.is_set(),
                provider=provider_name,
                model=model_name,
            )
            # Memory persistence mirrors the blocking path so the agent's
            # long-term memory still gets the exchange. Best-effort.
            try:
                await agent.memory.remember(
                    f"User: {user_message}\nAssistant: {final_text[:500]}",
                    tags=["conversation"],
                )
            except Exception as mem_err:
                logger.debug(f"[_stream_runner] memory.remember failed: {mem_err}")

            # Send the final "done" event
            if not cancel.is_set():
                await _push({
                    "type": "done",
                    "stream_id": stream_id,
                    "session_id": session_id,
                    "provider": provider_name,
                    "model": model_name,
                    "content_length": len(final_text),
                })
            entry["done"] = True
            entry["provider"] = provider_name
            entry["model"] = model_name
        except Exception as e:
            logger.exception(f"[_stream_runner] fatal: {e}")
            await _push({"type": "error", "error": str(e), "stream_id": stream_id})
            entry["done"] = True
        finally:
            # Always signal end-of-stream so the SSE consumer closes.
            try:
                queue.put_nowait({"type": "__eof__"})
            except Exception:
                pass
            # Schedule cleanup of the registry entry after a delay so
            # the SSE consumer has time to read the final events.
            async def _cleanup_later():
                await asyncio.sleep(30)
                await stream_registry.remove(stream_id)
            asyncio.create_task(_cleanup_later())

    @app.post("/api/chat/start")
    async def chat_start(req: dict):
        """Start a chat stream. Non-blocking.

        Body: { message: str, session?: str, model?: str, profile?: str,
                workspace?, model_provider? }
        Returns immediately with { stream_id, session_id, effective_model, ... }
        """
        message = (req.get("message") or "").strip()
        if not message:
            raise HTTPException(400, "message required")
        session_id = (req.get("session") or req.get("session_id") or "").strip()
        model_hint = (req.get("model") or "").strip() or None
        profile = (req.get("profile") or "default").strip() or "default"

        # Create or reuse the session
        existing = session_store.get(session_id) if session_id else None
        if existing is None:
            if not session_id:
                session_id = "sess_" + uuid.uuid4().hex[:12]
            new_data = session_store.create(
                message, model=model_hint or "", profile=profile, session_id=session_id,
            )
            existing = new_data

        # Determine an effective model — same heuristic as the WebUI uses.
        effective_model = model_hint
        if not effective_model:
            try:
                # Prefer the local provider's default if it has one
                local_prov = agent.router.get("local")
                if local_prov and hasattr(local_prov, "_models"):
                    effective_model = local_prov._models.get("default") or list(local_prov._models.values())[0]
            except Exception:
                pass
        if not effective_model:
            effective_model = "mock"

        # Register the stream and start the background runner
        stream_id = await stream_registry.create(
            session_id, model_hint or effective_model,
            provider_hint="local" if agent.local_available else (
                "cloud" if agent.cloud_available else "mock"
            ),
        )
        task = asyncio.create_task(_stream_runner(
            stream_id=stream_id,
            session_id=session_id,
            user_message=message,
            model_hint=model_hint or effective_model or "",
            profile=profile,
        ))
        await stream_registry.attach_task(stream_id, task)

        # Mirror to the legacy in-memory dict so any code that still
        # looks at agent._chat_sessions (e.g. /api/sessions legacy
        # handler) sees the session. Best-effort.
        if not hasattr(agent, "_chat_sessions"):
            agent._chat_sessions = {}
        agent._chat_sessions[session_id] = existing

        return {
            "ok": True,
            "stream_id": stream_id,
            "session_id": session_id,
            "title": existing.get("title", message[:60]),
            "effective_model": effective_model,
            "effective_model_provider": "local" if agent.local_available else (
                "cloud" if agent.cloud_available else "mock"
            ),
            "pending_started_at": time.time(),
        }

    @app.get("/api/chat/stream/{stream_id}")
    async def chat_stream_sse(stream_id: str, request: Request):
        """Server-Sent Events endpoint for an in-flight chat stream.

        Each frame is a JSON object on a single ``data:`` line:
            data: {"type":"starting",...}
            data: {"type":"delta","content":"hello",...}
            data: {"type":"done","session_id":"...",...}
            data: {"type":"cancelled",...}
            data: {"type":"error","error":"...",...}
        The connection is closed after the ``done`` / ``cancelled`` /
        ``error`` event arrives. Heartbeat comments are sent every 15s
        so the connection survives corporate proxies.
        """
        entry = stream_registry.get(stream_id)
        if not entry:
            # Stream is gone (server restart, expired). Tell the client
            # so it can re-render the partial session from disk.
            async def _gone_gen():
                yield f"data: {json.dumps({'type': 'error', 'error': 'stream not found', 'stream_id': stream_id, 'restarted': True})}\n\n"
            return StreamingResponse(_gone_gen(), media_type="text/event-stream", status_code=200)

        queue: asyncio.Queue = entry["queue"]
        session_id = entry["session_id"]

        async def event_gen():
            # Initial comment so the browser sees the connection open
            yield ": connected\n\n"
            # If the stream already completed before we connected, replay
            # the persisted session's last assistant message as a single
            # delta so the UI doesn't render an empty bubble.
            if entry.get("done"):
                try:
                    sess = session_store.get(session_id)
                    if sess:
                        last_asst = None
                        for m in reversed(sess.get("messages", [])):
                            if m.get("role") == "assistant":
                                last_asst = m
                                break
                        if last_asst and last_asst.get("content"):
                            yield f"data: {json.dumps({'type': 'replay', 'content': last_asst['content'], 'stream_id': stream_id, 'session_id': session_id, 'provider': entry.get('provider'), 'model': entry.get('model')})}\n\n"
                        yield f"data: {json.dumps({'type': 'done', 'stream_id': stream_id, 'session_id': session_id, 'replayed': True})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'error': str(e), 'stream_id': stream_id})}\n\n"
                return

            last_heartbeat = time.time()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Heartbeat keeps the connection alive through proxies
                    yield ": ping\n\n"
                    last_heartbeat = time.time()
                    continue
                if event.get("type") == "__eof__":
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") in ("done", "cancelled", "error"):
                    break

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.post("/api/chat/cancel/{stream_id}")
    async def chat_cancel(stream_id: str):
        """Cancel an in-flight chat stream."""
        ok = await stream_registry.cancel(stream_id)
        return {"ok": ok, "stream_id": stream_id, "cancelled": ok}

    @app.get("/api/chat/stream/status")
    async def chat_stream_status(stream_id: str):
        """Quick status check (debug + adapter compatibility)."""
        entry = stream_registry.get(stream_id)
        if not entry:
            return {"stream_id": stream_id, "active": False, "status": "gone"}
        return {
            "stream_id": stream_id,
            "session_id": entry["session_id"],
            "active": not entry.get("done", False),
            "status": "running" if not entry.get("done", False) else "done",
            "model": entry.get("model"),
            "provider": entry.get("provider"),
        }

    @app.get("/api/chat/sessions")
    async def chat_sessions():
        """List chat sessions (Phase 2: SessionStore-backed)."""
        sessions = session_store.list(limit=200)
        return {"sessions": sessions, "total": len(sessions)}

    @app.get("/api/chat/history")
    async def chat_history(session: str = ""):
        """Get message history for a session (Phase 2: SessionStore-backed)."""
        if not session:
            return {"session_id": "", "messages": []}
        data = session_store.get(session)
        if not data:
            return {"session_id": session, "messages": []}
        return {"session_id": session, "messages": data.get("messages", [])}

    @app.post("/api/chat/send")
    async def chat_send(req: dict):
        """Legacy blocking send (kept for backward compat and CLI use).

        Body: {"message": "text", "session": "optional-session-id"}
        Returns the full assistant reply without streaming.
        """
        message = (req.get("message") or "").strip()
        if not message:
            raise HTTPException(400, "message required")

        session_id = (req.get("session") or req.get("session_id") or "").strip() \
            or ("sess_" + uuid.uuid4().hex[:12])
        # Ensure the session exists
        if session_store.get(session_id) is None:
            session_store.create(message, session_id=session_id)
        # Append user turn
        await session_store.append_message(session_id, "user", message)
        # Sync LLM call (no streaming on this path)
        try:
            reply = await agent.chat(message, remember=True)
        except Exception as e:
            logger.error(f"Chat send failed: {e}")
            raise HTTPException(500, str(e))
        # Append assistant turn
        await session_store.append_message(
            session_id, "assistant", reply,
            provider="(blocking)", model="(blocking)",
        )
        # Mirror to legacy in-memory dict (defensive)
        if not hasattr(agent, "_chat_sessions"):
            agent._chat_sessions = {}
        existing = session_store.get(session_id) or {}
        agent._chat_sessions[session_id] = existing
        # Return the latest assistant message
        last = (existing.get("messages") or [{}])[-1]
        return {
            "ok": True,
            "session_id": session_id,
            "message": last,
            "user_message": {"role": "user", "content": message},
        }

    @app.delete("/api/chat/sessions/{session_id}")
    async def chat_delete_session(session_id: str):
        """Delete a chat session (Phase 2: SessionStore-backed)."""
        ok = await session_store.delete(session_id)
        # Also clear from legacy in-memory dict if present
        if hasattr(agent, "_chat_sessions"):
            agent._chat_sessions.pop(session_id, None)
        return {"ok": ok, "session_id": session_id, "deleted": ok}

    # ---- OpenAI-compatible shim endpoints ----

    # Pick the best available embedder: real (sbert) if installed, else hash.
    try:
        from hermes.embeddings import make_embedder
        _embedder = make_embedder(prefer=os.environ.get("HERMES_EMBEDDER", "auto"))
        logger.info(f"Embedder: {type(_embedder).__name__}")
    except Exception as e:
        logger.warning(f"Embedder init failed: {e}, using inline hash")
        _embedder = None

    @app.post("/v1/embeddings")
    async def v1_embeddings(req: dict):
        """OpenAI-compatible embeddings endpoint.

        Returns real semantic vectors if sentence-transformers + a model
        are installed; otherwise falls back to deterministic hash vectors
        (RAG UI still works, just search quality is poor).
        """
        model = req.get("model", "nomic-embed")
        texts = req.get("input", [])
        if isinstance(texts, str):
            texts = [texts]

        if _embedder is not None:
            try:
                vecs = await _embedder.embed(texts)
                data = [
                    {"object": "embedding", "embedding": vec, "index": i}
                    for i, vec in enumerate(vecs)
                ]
                return {"object": "list", "data": data, "model": model, "usage": {"prompt_tokens": 0, "total_tokens": 0}}
            except Exception as e:
                logger.warning(f"Real embedder failed ({e}), falling back to hash")

        # Fallback: hash-based pseudo-embeddings
        import hashlib
        dim = 384
        data = []
        for i, t in enumerate(texts):
            h = hashlib.sha512(t.encode("utf-8")).digest()
            vec = []
            for j in range(dim):
                vec.append(((h[j % len(h)] / 255.0) - 0.5) * 2.0)
            data.append({"object": "embedding", "embedding": vec, "index": i})
        return {"object": "list", "data": data, "model": model, "usage": {"prompt_tokens": 0, "total_tokens": 0}}

    @app.get("/v1/models")
    async def v1_models():
        """OpenAI-compatible models listing.

        Resolution order:
          1. Proxy live to llama-server at LLAMA_PORT (so the UI sees the
             exact model llama-server has loaded, with its --alias).
          2. Fall back to scanning hermes/data/models/*.gguf via the GGUF
             header parser (hermes/gguf.py), exposing the filename stem
             as the model id (matches llama-server's default alias).
          3. Empty list (UI handles no-models gracefully).
        """
        # 1) Live proxy to llama-server
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"http://127.0.0.1:{LLAMA_PORT}/v1/models")
                if r.status_code == 200:
                    return r.json()
        except Exception:
            pass

        # 2) Scan local GGUF files
        try:
            from hermes.gguf import list_gguf_models
            models_dir = HERMES_ROOT / "data" / "models"
            ggufs = list_gguf_models(models_dir)
            data = []
            for g in ggufs:
                stem = g["name"]
                if stem.lower().endswith(".gguf"):
                    stem = stem[:-5]
                data.append({
                    "id": stem,
                    "object": "model",
                    "created": int(g["path"].stat().st_mtime) if False else int(time.time()),
                    "owned_by": "local",
                    # extras the adapter/UI may use
                    "_size_gb": g.get("size_gb"),
                    "_arch": g.get("arch"),
                    "_ctx_len": g.get("ctx_len"),
                    "_quant": g.get("quant"),
                    "_filename": g["name"],
                    "_source": "gguf_scan",
                })
            return {"object": "list", "data": data}
        except Exception as e:
            logger.warning(f"GGUF scan failed: {e}")

        # 3) Empty
        return {"object": "list", "data": []}

    # ---- Web fallback ----

    @app.get("/health", include_in_schema=False)
    async def root_health_alias():
        # /health is a real JSON endpoint above. If the request reaches here
        # with Accept: text/html (e.g. someone browsed to it), serve the UI.
        static_dir = HERMES_ROOT / "hermes" / "static"
        if not static_dir.is_dir() or not (static_dir / "index.html").is_file():
            return JSONResponse({"status": "install_missing"}, status_code=503)
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        html = html.replace("__WEBUI_VERSION__", "1.0.0-hermes")
        html = html.replace("__MAX_UPLOAD_BYTES__", str(50 * 1024 * 1024))
        html = html.replace("__CSRF_TOKEN_JSON__", "null")
        return HTMLResponse(html)

    # ---- Static asset mount for Hermes WebUI ----
    # Mounted AFTER all /api/* and / routes so they take precedence.
    static_dir = HERMES_ROOT / "hermes" / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir), html=False), name="hermes-webui-static")
        logger.info(f"Mounted Hermes WebUI static at /static from {static_dir}")

    return app


def run_server(agent, host: str = "0.0.0.0", port: int = 7860):
    import uvicorn
    app = create_app(agent)
    logger.info(f"Starting on http://{host}:{port}")
    logger.info("Hermes API + Chat at http://%s:%s", host, port)
    # loop="asyncio" + http="h11" avoids Windows httptools compatibility issues
    uvicorn.run(app, host=host, port=port, log_level="info",
                loop="asyncio", http="h11", ws="none")
