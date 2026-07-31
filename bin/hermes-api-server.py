#!/usr/bin/env python3
"""Hermes Gateway API Server — 独立 aiohttp 服务 (:8642).

提供 OpenAI-compatible POST /v1/chat/completions。
直接创建 Hermes Agent 的 AIAgent，完整 conversation_loop（工具/MCP/skills）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import asyncio
import uuid
import time
import subprocess
from pathlib import Path

from aiohttp import web

# ── Paths ──
HERE = Path(__file__).resolve().parent
IKAROS_ROOT = HERE.parent
HERMES_AGENT_PATH = IKAROS_ROOT / "hermes-agent"
HERMES_VENV = HERMES_AGENT_PATH / "venv" / "Scripts" / "python.exe"

os.environ.setdefault("API_SERVER_KEY", "ikaros-gateway-key")
os.environ.setdefault("API_SERVER_HOST", "127.0.0.1")
os.environ.setdefault("API_SERVER_PORT", "8642")

HOST = os.environ["API_SERVER_HOST"]
PORT = int(os.environ["API_SERVER_PORT"])
API_KEY = os.environ["API_SERVER_KEY"]

# Session store (in-memory, process-level)
_session_histories: dict[str, list] = {}
_session_lock = asyncio.Lock()

log = logging.getLogger("hermes-api-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (colors/bold) from Hermes CLI output."""
    return _ANSI_RE.sub("", text)


def _clean_hermes_output(raw: str) -> str:
    """Strip ANSI codes + CLI banner/footer, keep only the assistant reply
    (the text inside the ╭─ ⚕ Hermes ─╮ box)."""
    raw = _strip_ansi(raw)
    out: list[str] = []
    inside_box = False
    for ln in raw.split("\n"):
        if "╭" in ln and "Hermes" in ln:
            inside_box = True
            continue
        if "╰" in ln:
            inside_box = False
            continue
        if inside_box:
            out.append(ln.strip())
            continue
        s = ln.strip()
        if not s:
            continue
        if (s.startswith("Query:") or s.startswith("Initializing agent")
                or s.startswith("Resume this session") or s.startswith("hermes --resume")
                or s.startswith("Session:") or s.startswith("Duration:")
                or s.startswith("Messages:")):
            continue
        if set(s) <= set("─━┄┅─-="):
            continue
    return "\n".join(x for x in out if x).strip()


def _extract_hermes_session(raw: str) -> str:
    """Extract the real Hermes session id from the `Session: <id>` footer line."""
    raw = _strip_ansi(raw)
    for ln in raw.split("\n"):
        s = ln.strip()
        if s.startswith("Session:"):
            sid = s.split(":", 1)[1].strip()
            if sid:
                return sid
    return ""


async def handle_chat_completions(request):
    """POST /v1/chat/completions — OpenAI Chat Completions format."""
    # Auth check — skip if no key configured (loopback-only)
    if API_KEY:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {API_KEY}":
            return web.json_response(
                {"error": {"message": "Unauthorized", "type": "auth_error"}}, status=403
            )

    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": {"message": "Invalid JSON", "type": "invalid_request_error"}}, status=400
        )

    messages = body.get("messages", [])
    stream = body.get("stream", False)
    model = body.get("model", "hermes-agent")

    # Extract system prompt, conversation history, and user message
    system_prompt = None
    history = []
    user_text = ""

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system":
            system_prompt = (system_prompt or "") + "\n" + content
        elif role == "user":
            history.append({"role": "user", "content": content})
            user_text = content
        elif role == "assistant":
            history.append({"role": "assistant", "content": content})

    # Session continuity via X-Hermes-Session-Id
    session_id = request.headers.get("X-Hermes-Session-Id", "").strip()

    async with _session_lock:
        saved_history = _session_histories.get(session_id, [])
        # Merge saved history with request history
        if not saved_history:
            saved_history = history[:-1] if history else []

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
    created = int(time.time())

    # Run AIAgent with conversation_loop in executor
    def _run():
        """Run Hermes Agent conversation in a subprocess."""
        # Call hermes chat CLI with the message
        _hermes = str(HERMES_AGENT_PATH / "venv" / "Scripts" / "hermes.exe")
        _prompt = user_text

        # Build args: resume session if we have one (use raw session_id)
        _args = [_hermes, "chat", "-q", _prompt, "--max-turns", "10", "--pass-session-id"]
        if session_id:
            _args.extend(["--resume", session_id])

        result = subprocess.run(
            _args,
            capture_output=True, text=True, timeout=120,
            cwd=str(IKAROS_ROOT),
            env={**os.environ,
                 "HERMES_ROOT": str(IKAROS_ROOT),
                 "HERMES_HOME": str(IKAROS_ROOT / "data" / "hermes-agent"),
            },
        )

        # Parse + clean output: strip ANSI/banner, extract answer + real session id
        reply = _clean_hermes_output(result.stdout)
        sub_session_id = _extract_hermes_session(result.stdout)
        if not reply or result.returncode != 0:
            reply = _clean_hermes_output(result.stderr) or result.stderr.strip() or "(空回复)"

        return reply, sub_session_id

    try:
        loop = asyncio.get_event_loop()
        reply, sub_session_id = await loop.run_in_executor(None, _run)

        # Use real Hermes session_id from subprocess for continuity
        hermes_session_id = sub_session_id if sub_session_id else session_id
        if not hermes_session_id:
            hermes_session_id = f"hermes-{uuid.uuid4().hex[:16]}"

        # Save session
        async with _session_lock:
            saved = _session_histories.get(hermes_session_id, [])
            saved.append({"role": "user", "content": user_text})
            saved.append({"role": "assistant", "content": reply})
            if len(saved) > 80:
                saved = saved[-80:]
            _session_histories[hermes_session_id] = saved

    except Exception as e:
        log.error("conversation failed: %s", e)
        return web.json_response(
            {"error": {"message": str(e), "type": "server_error"}}, status=500
        )

    # Build response in OpenAI Chat Completions format
    resp_body = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": reply,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

    response = web.json_response(resp_body)
    response.headers["X-Hermes-Session-Id"] = hermes_session_id
    return response


async def handle_health(request):
    """GET /health — health check."""
    return web.json_response({"status": "ok", "port": PORT, "mode": "hermes-agent"})


async def build_app():
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    return app


async def main():
    app = await build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    log.info("Hermes Gateway API Server ready on http://%s:%s", HOST, PORT)
    log.info("  POST /v1/chat/completions (OpenAI-compatible)")
    log.info("  GET  /health")
    log.info("  API Key: %s", API_KEY)

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("stopped")
