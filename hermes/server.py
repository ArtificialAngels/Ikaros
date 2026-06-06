"""
FastAPI web server for Hermes.

Provides the /api/* endpoints, built-in Chat Pro UI (/chat),
and OpenAI-compatible /v1/* shims for external clients.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("hermes.server")

# Hermes project root (parent of this hermes/ package)
HERMES_ROOT = Path(__file__).resolve().parent.parent

# ChatGPT-Next-Web (:7890) is the primary chat UI.

HTML_FALLBACK = """<!DOCTYPE html>
<html>
<head><title>Hermes Agent</title>
<style>body{font-family:system-ui;background:#0a0e14;color:#e6edf3;margin:40px;max-width:800px;line-height:1.5}</style>
</head>
<body>
<h1>Hermes Agent</h1>
<p>The Hermes API is running on this port.</p>
<p>Chat UI: <a href="http://localhost:7890">ChatGPT-Next-Web (:7890)</a></p>
<h2>Useful endpoints</h2>
<ul>
  <li><a href="/chat"><code>/chat</code></a> — Chat Pro UI</li>
  <li><a href="/health"><code>/health</code></a> — health probe (JSON)</li>
  <li><a href="/v1/models"><code>/v1/models</code></a> — OpenAI-compatible model list</li>
  <li><a href="/api/status"><code>/api/status</code></a> — agent status (memory, KB, skills)</li>
  <li><a href="/api/skills"><code>/api/skills</code></a> — available skills</li>
  <li><a href="/api/memory"><code>/api/memory</code></a> — memory store stats</li>
</ul>
</body>
</html>"""


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

    @app.on_event("startup")
    async def startup():
        await agent.initialize()

    # ---- API endpoints the SPA expects (most important) ----

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
            "memory": agent.memory.stats(),
            "knowledge": agent.knowledge.stats(),
            "skills": [s["name"] for s in agent.skills.list()],
            "session": agent.session_id,
            "turn_count": agent.turn_count,
            "data_dir": str(agent.paths["base"]),
            "providers": [
                {"name": name, "url": getattr(p, "base_url", "?")}
                for name, p in agent.router.providers.items()
            ],
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

    @app.get("/")
    async def index():
        """Home — redirects to ChatGPT-Next-Web."""
        return HTMLResponse(HTML_FALLBACK)

    @app.get("/chat")
    async def chat_ui():
        """Redirect to ChatGPT-Next-Web UI."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse("http://127.0.0.1:7890")

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

        Returns models available through the local llama-server.
        """
        return {
            "object": "list",
            "data": [
                {
                    "id": "qwen3.5-35b-a3b",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "local",
                },
                {
                    "id": "qwen2.5-7b-instruct",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "local",
                },
                {
                    "id": "qwen2.5-3b-instruct",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "local",
                },
            ],
        }

    # ---- Web fallback ----

    @app.get("/", include_in_schema=False)
    async def root_fallback():
        return HTMLResponse(HTML_FALLBACK)

    @app.get("/health", include_in_schema=False)
    async def root_health_alias():
        # /health is also handled above as a real JSON endpoint, but keep
        # this here as a no-op so the route exists in schema.
        return HTMLResponse(HTML_FALLBACK)

    return app


def run_server(agent, host: str = "0.0.0.0", port: int = 7860):
    import uvicorn
    app = create_app(agent)
    logger.info(f"Starting on http://{host}:{port}")
    logger.info("API mode (Chat Pro at /chat)")
    # loop="asyncio" + http="h11" avoids Windows httptools compatibility issues
    uvicorn.run(app, host=host, port=port, log_level="info",
                loop="asyncio", http="h11", ws="none")
