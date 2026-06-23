"""modules/webui_proxy/webui_proxy.py — Thin HTTP proxy in front of hermes-web-ui.

Listens on :8648 and forwards everything to the upstream npm package on :8649,
EXCEPT the `/api/hermes/usage/stats` endpoint which we serve ourselves with
corrected SQL (npm package's GROUP BY model ignores `billing_provider` /
`profile` / `billing_base_url` and does not exclude internal sessions).

Why a separate proxy instead of patching the npm package?
- The npm package is updated via `npm install -g hermes-web-ui` and any
  hand-edit to dist/server/index.js is overwritten.
- The npm package's Vue 3 frontend is hard-coded to fetch
  `/api/hermes/usage/stats`; we cannot change the path.
- A thin Python proxy in front intercepts only the broken endpoint and
  forwards everything else (chat SSE / WebSocket / static assets) unchanged.

Usage:
    python webui_proxy.py [--port 8648] [--upstream http://127.0.0.1:8649]
                          [--state-db data/hermes-agent/state.db]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Standard library only. No third-party deps.

# ============== Defaults ==============
DEFAULT_PORT = 8648
DEFAULT_UPSTREAM = "http://127.0.0.1:8649"
DEFAULT_STATE_DB = "data/hermes-agent/state.db"
# How long to wait for the upstream before giving up
DEFAULT_UPSTREAM_TIMEOUT = 60.0
# Path we intercept
USAGE_STATS_PATH = "/api/hermes/usage/stats"
# Webui self-update path. The npm package's built-in POST /api/hermes/update
# runs `npm install -g hermes-web-ui@latest` from within the webui process.
# On Windows this fails with EBUSY (the running webui Node process holds
# open the very files npm is trying to rename). We intercept this path in
# webui_proxy and write a marker file instead; bin/hermes-watchdog.py
# picks it up at the next tick and performs stop->npm install->start.
WEBUI_UPDATE_PATH = "/api/hermes/update"
WEBUI_UPDATE_MARKER = Path("data/webui/needs-update.json")
# Local llama-server restart path (2026-06-18, F方案). WebUI's
# `/api/hermes/provider-models/cache/refresh` only refreshes cloud
# provider catalogs (see webui dist/server/index.js:1035). It does NOT
# re-scan local `data/models/*.gguf`. To make the WebUI's model dropdown
# reflect disk changes, we intercept this path and proxy to the bridge
# (which shells out to the supervisor).
LLAMA_RESTART_PATH = "/api/hermes/llama/restart"
# Liveness path: WebUI's topbar can show a green/yellow/red dot to
# indicate "Icarus is alive" — this is the bridge endpoint behind it.
# WebUI fetches /api/hermes/liveness to render the badge.
LIVENESS_PATH = "/api/hermes/liveness"
DEFAULT_BRIDGE_URL = "http://127.0.0.1:7860"
# WebUI path → bridge path. The SPA uses /api/hermes/* namespacing; the
# bridge uses /v1/* (its OpenAI-compatible API). Map them explicitly so
# future endpoints can be added without rewriting _proxy_to_bridge.
WEBUI_TO_BRIDGE_PATH: dict[str, str] = {
    "/api/hermes/llama/restart": "/v1/llama/restart",
    "/api/hermes/liveness": "/v1/liveness",
    # Icarus session recovery: SPA → /api/hermes/icarus/* → bridge /v1/icarus/*
    "/api/hermes/icarus/last-session": "/v1/icarus/last-session",
    "/api/hermes/icarus/memories": "/v1/icarus/memories",
    "/api/hermes/icarus/awake-briefing": "/v1/icarus/awake-briefing",
    "/api/hermes/icarus/active-session": "/v1/icarus/active-session",
}
# Plugin management paths (new in 2026-06-17: E方案 — break hermes-web-ui's
# "read-only" stance so the UI can actually manage plugins, with safety:
#   - only listen on 127.0.0.1 (external CSRF impossible)
#   - whitelist of action verbs (no shell injection)
#   - 5s subprocess timeout
#   - proxy never reveals stderr to the SPA
PLUGINS_PREFIX = "/api/hermes/plugins/"

# Icarus session recovery: SPA-injected bridge UI.
# webui_proxy intercepts /index.html and prepends <script src="/_icarus/recovery.js">,
# then serves that script inline so it can never be clobbered by webui npm
# updates. webui_proxy is git-tracked; the upstream SPA is not.
ICARUS_JS_PATH = "/_icarus/recovery.js"
ICARUS_RECOVERY_API_PREFIX = "/api/hermes/icarus/"
PLUGIN_ALLOWED_ACTIONS = frozenset({"list", "enable", "disable", "install", "remove"})
# Whitelist for plugin names: bundle-prefixed identifiers like
# "browser-browser-use", "browser-browserbase". Reject anything that
# looks like a path traversal, shell metachar, or git URL.
PLUGIN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PLUGIN_SOURCE_PATTERN = re.compile(r"^(?:https?://|git@)[A-Za-z0-9._:/\-@?&=]+$|^[A-Za-z0-9._\-]+/[A-Za-z0-9._\-]+$")
PLUGIN_HERMES_TIMEOUT_S = 5.0
# Resolve hermes CLI once at import time
PLUGIN_HERMES_PY = shutil.which("python") or sys.executable


# ============== SQL: correct usage aggregation ==============
# Compared to the npm package's Pw() SQL:
#   - GROUP BY model, billing_provider, billing_base_url
#     (so the same model name across providers / base URLs is NOT merged)
#   - WHERE excludes source='tool', id LIKE 'compress_%',
#     parent_session_id IS NOT NULL, archived=1
#     (internal sessions don't pollute the user-facing model breakdown)
#   - 30 / 90 / 365 day window from now() (matches the npm package's behavior)
#   - Note: state.db's sessions table has no `profile` column (profile is
#     enforced per-request via the X-Hermes-Profile header upstream and not
#     persisted in the session row), so the npm package's GROUP BY
#     `model, billing_provider, profile, billing_base_url` would error out.
#     We split by (model, provider, base_url) only — the two dimensions
#     that actually identify a billing lane in the local DB.
USAGE_STATS_SQL_OVERVIEW = """
SELECT
    COALESCE(SUM(input_tokens), 0) AS input_tokens,
    COALESCE(SUM(output_tokens), 0) AS output_tokens,
    COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
    COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
    COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
    COALESCE(SUM(COALESCE(actual_cost_usd, estimated_cost_usd, 0)), 0) AS cost,
    COUNT(*) AS sessions,
    COALESCE(SUM(COALESCE(api_call_count, 0)), 0) AS total_api_calls
FROM sessions
WHERE started_at > ?
  AND model IS NOT NULL AND model != ''
  AND source != 'tool'
  AND id NOT LIKE 'compress_%'
  AND (parent_session_id IS NULL OR parent_session_id = '')
  AND COALESCE(archived, 0) = 0
"""

USAGE_STATS_SQL_BY_MODEL = """
SELECT
    model AS model,
    billing_provider AS provider,
    billing_base_url AS base_url,
    COALESCE(SUM(input_tokens), 0) AS input_tokens,
    COALESCE(SUM(output_tokens), 0) AS output_tokens,
    COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
    COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
    COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
    COALESCE(SUM(COALESCE(actual_cost_usd, estimated_cost_usd, 0)), 0) AS cost,
    COUNT(*) AS sessions,
    COALESCE(SUM(COALESCE(api_call_count, 0)), 0) AS api_calls
FROM sessions
WHERE started_at > ?
  AND model IS NOT NULL AND model != ''
  AND source != 'tool'
  AND id NOT LIKE 'compress_%'
  AND (parent_session_id IS NULL OR parent_session_id = '')
  AND COALESCE(archived, 0) = 0
GROUP BY model, billing_provider, billing_base_url
ORDER BY (COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0)) DESC
"""

USAGE_STATS_SQL_BY_DAY = """
SELECT
    date(started_at, 'unixepoch') AS date,
    COALESCE(SUM(input_tokens), 0) AS input_tokens,
    COALESCE(SUM(output_tokens), 0) AS output_tokens,
    COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
    COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
    COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
    COUNT(*) AS sessions,
    COALESCE(SUM(COALESCE(actual_cost_usd, estimated_cost_usd, 0)), 0) AS cost
FROM sessions
WHERE started_at > ?
  AND model IS NOT NULL AND model != ''
  AND source != 'tool'
  AND id NOT LIKE 'compress_%'
  AND (parent_session_id IS NULL OR parent_session_id = '')
  AND COALESCE(archived, 0) = 0
GROUP BY date
ORDER BY date ASC
"""


def _to_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _to_float(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def compute_usage_stats(state_db: Path, days: int) -> dict[str, Any]:
    """Run the corrected SQL and shape the response like the npm package
    frontend expects (camel-ish flat keys + by_model + by_day)."""
    if not state_db.is_file():
        return {
            "error": f"state.db not found at {state_db}",
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_read_tokens": 0,
            "total_cache_write_tokens": 0,
            "total_reasoning_tokens": 0,
            "total_sessions": 0,
            "total_cost": 0.0,
            "total_api_calls": 0,
            "period_days": days,
            "model_usage": [],
            "daily_usage": [],
        }

    cutoff = int(time.time()) - max(1, min(int(days), 365)) * 86400
    out: dict[str, Any] = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_read_tokens": 0,
        "total_cache_write_tokens": 0,
        "total_reasoning_tokens": 0,
        "total_sessions": 0,
        "total_cost": 0.0,
        "total_api_calls": 0,
        "period_days": max(1, min(int(days), 365)),
        "model_usage": [],
        "daily_usage": [],
    }
    try:
        con = sqlite3.connect(str(state_db))
        con.row_factory = sqlite3.Row
        try:
            # Overview
            row = con.execute(USAGE_STATS_SQL_OVERVIEW, (cutoff,)).fetchone()
            if row:
                out["total_input_tokens"] = _to_int(row["input_tokens"])
                out["total_output_tokens"] = _to_int(row["output_tokens"])
                out["total_cache_read_tokens"] = _to_int(row["cache_read_tokens"])
                out["total_cache_write_tokens"] = _to_int(row["cache_write_tokens"])
                out["total_reasoning_tokens"] = _to_int(row["reasoning_tokens"])
                out["total_sessions"] = _to_int(row["sessions"])
                out["total_cost"] = _to_float(row["cost"])
                out["total_api_calls"] = _to_int(row["total_api_calls"])

            # By-model (with provider / base_url breakdown)
            by_model: list[dict[str, Any]] = []
            for r in con.execute(USAGE_STATS_SQL_BY_MODEL, (cutoff,)).fetchall():
                by_model.append({
                    "model": _to_str(r["model"]),
                    "provider": _to_str(r["provider"]),
                    "base_url": _to_str(r["base_url"]),
                    "input_tokens": _to_int(r["input_tokens"]),
                    "output_tokens": _to_int(r["output_tokens"]),
                    "cache_read_tokens": _to_int(r["cache_read_tokens"]),
                    "cache_write_tokens": _to_int(r["cache_write_tokens"]),
                    "reasoning_tokens": _to_int(r["reasoning_tokens"]),
                    "cost": _to_float(r["cost"]),
                    "sessions": _to_int(r["sessions"]),
                    "api_calls": _to_int(r["api_calls"]),
                })
            out["model_usage"] = by_model

            # By-day (fill missing days with zero rows so the chart x-axis is dense)
            by_day_map: dict[str, dict[str, Any]] = {}
            today = time.strftime("%Y-%m-%d")
            for r in con.execute(USAGE_STATS_SQL_BY_DAY, (cutoff,)).fetchall():
                by_day_map[_to_str(r["date"])] = {
                    "date": _to_str(r["date"]),
                    "input_tokens": _to_int(r["input_tokens"]),
                    "output_tokens": _to_int(r["output_tokens"]),
                    "cache_read_tokens": _to_int(r["cache_read_tokens"]),
                    "cache_write_tokens": _to_int(r["cache_write_tokens"]),
                    "reasoning_tokens": _to_int(r["reasoning_tokens"]),
                    "sessions": _to_int(r["sessions"]),
                    "errors": 0,
                    "cost": _to_float(r["cost"]),
                }
            # Fill dense date axis (no gaps in the chart)
            from datetime import date, timedelta
            dense: list[dict[str, Any]] = []
            start = date.fromtimestamp(cutoff)
            end = date.fromisoformat(today)
            cur = start
            while cur <= end:
                key = cur.isoformat()
                if key in by_day_map:
                    dense.append(by_day_map[key])
                else:
                    dense.append({
                        "date": key,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "reasoning_tokens": 0,
                        "sessions": 0,
                        "errors": 0,
                        "cost": 0.0,
                    })
                cur += timedelta(days=1)
            out["daily_usage"] = dense
        finally:
            con.close()
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# ============== Proxy ==============

class ProxyHandler(BaseHTTPRequestHandler):
    # Injected by `make_handler` (avoids subclass boilerplate per-instance)
    upstream_base: str = DEFAULT_UPSTREAM
    state_db: Path = Path(DEFAULT_STATE_DB)
    upstream_timeout: float = DEFAULT_UPSTREAM_TIMEOUT

    server_version = "webui_proxy/1.0"

    # Suppress default access log to keep logs readable; emit one-liners instead.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        sys.stderr.write(f"[webui_proxy] {self.address_string()} - {format % args}\n")

    # ---------- Routing ----------
    def do_GET(self) -> None:
        if self.path.startswith(USAGE_STATS_PATH):
            self._handle_usage_stats()
        elif self.path.startswith(PLUGINS_PREFIX):
            # /api/hermes/plugins/list is the only GET endpoint
            self._handle_plugin_action("list")
        elif self.path == LIVENESS_PATH:
            # WebUI can poll liveness to render an "Icarus alive" badge.
            # Proxied to bridge /v1/liveness which probes local + clouds.
            self._proxy_to_bridge()
        elif self.path == ICARUS_JS_PATH:
            # Recovery JS — git-tracked inline source. Inline here rather
            # than as a method so we don't have to bind it into ProxyHandler.
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(_RECOVERY_JS_SOURCE)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(_RECOVERY_JS_SOURCE)
        elif self.path.startswith(ICARUS_RECOVERY_API_PREFIX):
            # /api/hermes/icarus/* → bridge /v1/icarus/* (mapped in WEBUI_TO_BRIDGE_PATHS)
            self._proxy_to_bridge()
        else:
            self._proxy_pass()

    def do_POST(self) -> None:
        if self.path.startswith(PLUGINS_PREFIX):
            # Read body first, then route to handler. The handler does NOT
            # forward the request to upstream — it shells out to hermes CLI.
            self._handle_plugin_with_body()
        elif self.path == LLAMA_RESTART_PATH:
            # Proxy to bridge /v1/llama/restart (which calls the supervisor).
            # WebUI's built-in refresh-cache button only refreshes cloud
            # providers; this is the bridge-level escape hatch for local.
            self._proxy_to_bridge()
        elif self.path == WEBUI_UPDATE_PATH:
            # Intercept: write needs-update marker for the watchdog to pick
            # up. Do NOT forward to upstream — the upstream endpoint is
            # the one that EBUSYs on Windows. The watchdog (10s tick) will
            # stop webui, run npm install -g, and start the new version.
            self._handle_update_marker()
        else:
            self._proxy_pass()

    def do_PUT(self) -> None:
        self._proxy_pass()

    def do_DELETE(self) -> None:
        self._proxy_pass()

    def do_PATCH(self) -> None:
        self._proxy_pass()

    def do_OPTIONS(self) -> None:
        # CORS preflight: forward to upstream (or short-circuit OK).
        self._proxy_pass()

    def _handle_usage_stats(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        try:
            days = int(qs.get("days", ["30"])[0])
        except (TypeError, ValueError):
            days = 30
        if days <= 0 or days > 365:
            days = 30
        result = compute_usage_stats(self.state_db, days)
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    # ---------- Plugin management (E方案, 2026-06-17) ----------
    def _handle_plugin_with_body(self) -> None:
        """Read JSON body then dispatch. POST endpoints: enable/disable/install/remove."""
        # Action is the last path segment: /api/hermes/plugins/<action>
        action = self.path[len(PLUGINS_PREFIX):].split("?", 1)[0].split("/", 1)[0]
        if action not in PLUGIN_ALLOWED_ACTIONS or action == "list":
            # list is GET-only; everything else except these 4 is rejected
            self._plugin_send_error(HTTPStatus.METHOD_NOT_ALLOWED,
                                    f"action '{action}' is not allowed on POST")
            return
        # Read body
        body: dict[str, Any] = {}
        if "Content-Length" in self.headers:
            try:
                clen = int(self.headers["Content-Length"])
                if clen > 0 and clen < 64 * 1024:
                    raw = self.rfile.read(clen)
                    if raw:
                        try:
                            body = json.loads(raw.decode("utf-8"))
                            if not isinstance(body, dict):
                                body = {}
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            self._plugin_send_error(HTTPStatus.BAD_REQUEST,
                                                    "body must be JSON object")
                            return
            except (TypeError, ValueError):
                body = {}
        # Dispatch
        if action in ("enable", "disable", "remove"):
            name = body.get("name", "")
            if not isinstance(name, str) or not PLUGIN_NAME_PATTERN.match(name):
                self._plugin_send_error(HTTPStatus.BAD_REQUEST,
                                        "field 'name' required, must match "
                                        "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
                return
            self._run_hermes_plugin(action, [name])
        elif action == "install":
            source = body.get("source", "")
            if not isinstance(source, str) or not PLUGIN_SOURCE_PATTERN.match(source):
                self._plugin_send_error(HTTPStatus.BAD_REQUEST,
                                        "field 'source' required, must be a "
                                        "git URL (https://, git@) or owner/repo")
                return
            self._run_hermes_plugin(action, [source])

    def _handle_plugin_action(self, action: str) -> None:
        """GET /api/hermes/plugins/<action> (action is hardcoded by router)."""
        self._run_hermes_plugin(action, [])

    def _run_hermes_plugin(self, action: str, args: list[str]) -> None:
        """Shell out to hermes CLI, capture stdout, return JSON envelope."""
        if action not in PLUGIN_ALLOWED_ACTIONS:
            self._plugin_send_error(HTTPStatus.BAD_REQUEST, "unknown action")
            return
        # Build argv — list form, no shell. subprocess passes args verbatim.
        argv = [PLUGIN_HERMES_PY, "-m", "hermes", "plugins", action, *args]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=PLUGIN_HERMES_TIMEOUT_S,
                cwd=str(Path(__file__).resolve().parent.parent.parent),  # HERMES_ROOT
            )
            stdout = proc.stdout
            stderr = proc.stderr
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            self._plugin_send_error(HTTPStatus.GATEWAY_TIMEOUT,
                                    f"hermes plugins {action} timed out after "
                                    f"{PLUGIN_HERMES_TIMEOUT_S}s")
            return
        except FileNotFoundError:
            self._plugin_send_error(HTTPStatus.INTERNAL_SERVER_ERROR,
                                    "python interpreter not found")
            return
        # Try to parse list output as a table → return as structured JSON
        if action == "list":
            plugins = self._parse_hermes_plugin_table(stdout)
            payload = {
                "action": action,
                "ok": rc == 0,
                "plugins": plugins,
                "raw": stdout[-4096:],
                "stderr": (stderr or "")[-1024:] if rc != 0 else "",
            }
        else:
            # enable/disable/install/remove: return a simple envelope
            payload = {
                "action": action,
                "ok": rc == 0,
                "args": args,
                "raw": stdout[-2048:],
                "stderr": (stderr or "")[-1024:] if rc != 0 else "",
                "exit_code": rc,
            }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status = HTTPStatus.OK if rc == 0 else HTTPStatus.INTERNAL_SERVER_ERROR
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _parse_hermes_plugin_table(self, stdout: str) -> list[dict[str, str]]:
        """Best-effort parse of `hermes plugins list` table output (no JSON CLI flag)."""
        out: list[dict[str, str]] = []
        for line in stdout.splitlines():
            # Skip borders / headers
            if not line.strip() or set(line.strip()) <= {"┌", "┐", "└", "┘", "─", "│", "├", "┤", "┬", "┴", "┼"}:
                continue
            if line.strip().startswith(("Name", "Plugins")):
                continue
            if line.startswith("│") and line.count("│") >= 6:
                # rough split by │ — but columns can wrap so this is fragile
                parts = [p.strip() for p in line.strip("│").split("│")]
                if len(parts) >= 5 and parts[0] and parts[0] not in ("Name",):
                    out.append({
                        "name": parts[0],
                        "status": parts[1] if len(parts) > 1 else "",
                        "version": parts[2] if len(parts) > 2 else "",
                        "description": parts[3] if len(parts) > 3 else "",
                        "source": parts[4] if len(parts) > 4 else "",
                    })
        return out

    def _plugin_send_error(self, status: HTTPStatus, msg: str) -> None:
        body = json.dumps({"ok": False, "error": msg}, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _proxy_pass(self) -> None:
        url = self.upstream_base + self.path
        # Read incoming body (if any)
        body: bytes | None = None
        if "Content-Length" in self.headers:
            try:
                clen = int(self.headers["Content-Length"])
                if clen > 0:
                    body = self.rfile.read(clen)
            except (TypeError, ValueError):
                body = None

        # Build upstream request
        # Forward only safe-ish headers; drop hop-by-hop + body-specific ones
        fwd_headers: dict[str, str] = {}
        skip = {
            "host", "content-length", "connection", "keep-alive",
            "proxy-authenticate", "proxy-authorization", "te", "trailers",
            "transfer-encoding", "upgrade",
        }
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in skip:
                continue
            fwd_headers[k] = v

        req = urllib.request.Request(
            url,
            data=body,
            method=self.command,
            headers=fwd_headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.upstream_timeout) as resp:
                status = resp.status
                resp_body = resp.read()
                # Filter hop-by-hop response headers too
                resp_headers = []
                for k, v in resp.getheaders():
                    lk = k.lower()
                    if lk in skip:
                        continue
                    resp_headers.append((k, v))
        except urllib.error.HTTPError as e:
            # Pass upstream's error body verbatim
            try:
                resp_body = e.read()
            except Exception:
                resp_body = str(e).encode("utf-8")
            status = e.code
            resp_headers = [("Content-Type", "text/plain; charset=utf-8")]
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Upstream not ready / connection refused (e.g. webui still booting)
            msg = f"upstream unavailable: {type(e).__name__}: {e}".encode("utf-8")
            self.send_response(HTTPStatus.BAD_GATEWAY)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(msg)
            return

        self.send_response(status)
        for k, v in resp_headers:
            # Don't allow upstream to override our CORS open policy for the
            # intercepted endpoint, but for forwarded paths let upstream decide.
            if k.lower() == "access-control-allow-origin" and self.path.startswith(USAGE_STATS_PATH):
                continue
            self.send_header(k, v)
        if not any(k.lower() == "content-length" for k, _ in resp_headers):
            self.send_header("Content-Length", str(len(resp_body)))

        # ---- Icarus recovery injection ----
        # If this is the SPA entry HTML, prepend a <script src="/_icarus/recovery.js">
        # so the SPA always shows a "Resume previous conversation?" toast on load,
        # even after webui npm updates overwrite any patches inside the npm package.
        if (
            self.command == "GET"
            and resp_body
            and _should_inject_recovery(self.path, resp_headers, resp_body)
        ):
            injection = _build_recovery_injection()
            resp_body = _inject_before_closing_head(resp_body, injection)
            self.send_header("Content-Length", str(len(resp_body)))

        self.end_headers()
        self.wfile.write(resp_body)

    def _handle_update_marker(self) -> None:
        """Intercept /api/hermes/update and write a needs-update marker.

        Why: the upstream webui's update endpoint runs
        `npm install -g hermes-web-ui@latest` from inside the webui
        Node process. On Windows the rename step in that install hits
        EBUSY because the running webui still holds the very files
        npm wants to replace. We write a marker instead and let
        bin/hermes-watchdog.py handle the actual stop -> npm install ->
        start sequence on its next tick (within 10s of the click).
        """
        # Resolve project root (where data/webui/ lives). The proxy is
        # at modules/webui_proxy/webui_proxy.py; HERMES_ROOT is three
        # levels up.
        try:
            here = Path(__file__).resolve()
            project_root = here.parent.parent.parent
            marker_path = project_root / "data" / "webui" / "needs-update.json"
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "requested_at": int(time.time()),
                "trigger": "webui_update_button",
            }
            # Atomic-ish: write to .tmp then rename (POSIX-style); on
            # Windows the rename fails if target exists, so we just
            # overwrite directly — the watchdog only cares about the
            # file existing.
            marker_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            self._send_update_response(
                success=False,
                code=HTTPStatus.INTERNAL_SERVER_ERROR,
                msg=f"failed to write update marker: {e}",
            )
            return
        self._send_update_response(
            success=True,
            code=HTTPStatus.OK,
            msg=(
                "hermes-web-ui update scheduled. The watchdog will stop the "
                "current webui, run npm install, and start the new version "
                "within 10 seconds. The UI will reload automatically."
            ),
        )

    def _send_update_response(self, success: bool, code: HTTPStatus, msg: str) -> None:
        body = json.dumps(
            {"success": success, "message": msg},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _proxy_to_bridge(self) -> None:
        """Forward the current request to the bridge (no shell, list args).

        The original WebUI proxy_pass forwards to the webui upstream. This
        method is for paths that need to reach the bridge directly (e.g.
        llama restart). Mirrors `_proxy_pass` for response handling so
        clients see the same wire format they'd get from the bridge.

        Path mapping
        ------------
        WebUI's SPA fetches `/api/hermes/llama/restart` (matches the
        convention of `/api/hermes/*` for app-level endpoints). The bridge
        exposes the same handler at `/v1/llama/restart` (matches its
        OpenAI-compatible API). We map them explicitly via WEBUI_TO_BRIDGE_PATH.
        """
        # Map webui paths → bridge paths. Default: strip /api/hermes prefix.
        bridge_path = WEBUI_TO_BRIDGE_PATH.get(self.path)
        if bridge_path is None:
            # Fall back: strip the /api/hermes prefix and forward the rest.
            for prefix in ("/api/hermes", "/api/hermes/"):
                if self.path == prefix.rstrip("/"):
                    bridge_path = "/"
                    break
                if self.path.startswith(prefix):
                    bridge_path = self.path[len(prefix.rstrip("/")):]
                    if not bridge_path.startswith("/"):
                        bridge_path = "/" + bridge_path
                    break
            else:
                bridge_path = self.path
        url = self.bridge_url + bridge_path
        body: bytes | None = None
        if "Content-Length" in self.headers:
            try:
                clen = int(self.headers["Content-Length"])
                if clen > 0:
                    body = self.rfile.read(clen)
            except (TypeError, ValueError):
                body = None

        fwd_headers: dict[str, str] = {}
        skip = {
            "host", "content-length", "connection", "keep-alive",
            "proxy-authenticate", "proxy-authorization", "te", "trailers",
            "transfer-encoding", "upgrade",
        }
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in skip:
                continue
            fwd_headers[k] = v

        req = urllib.request.Request(url, data=body, method=self.command, headers=fwd_headers)
        try:
            with urllib.request.urlopen(req, timeout=self.upstream_timeout) as resp:
                status = resp.status
                resp_body = resp.read()
                resp_headers = [(k, v) for k, v in resp.getheaders() if k.lower() not in skip]
        except urllib.error.HTTPError as e:
            try:
                resp_body = e.read()
            except Exception:
                resp_body = str(e).encode("utf-8")
            status = e.code
            resp_headers = [("Content-Type", "text/plain; charset=utf-8")]
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            msg = f"bridge unavailable: {type(e).__name__}: {e}".encode("utf-8")
            self.send_response(HTTPStatus.BAD_GATEWAY)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(msg)
            return

        self.send_response(status)
        for k, v in resp_headers:
            self.send_header(k, v)
        if not any(k.lower() == "content-length" for k, _ in resp_headers):
            self.send_header("Content-Length", str(len(resp_body)))
        # Bridge doesn't set CORS, but the WebUI SPA at :8649 may fetch
        # this via fetch() from :8648 (same origin, so no CORS needed).
        # Add an open CORS header just in case a tool hits it cross-origin.
        if not any(k.lower() == "access-control-allow-origin" for k, _ in resp_headers):
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(resp_body)


def make_handler(upstream_base: str, state_db: Path, upstream_timeout: float,
                 bridge_url: str = DEFAULT_BRIDGE_URL) -> type[ProxyHandler]:
    """Factory to inject config into the handler class."""
    class _Bound(ProxyHandler):
        pass
    _Bound.upstream_base = upstream_base
    _Bound.state_db = state_db
    _Bound.upstream_timeout = upstream_timeout
    _Bound.bridge_url = bridge_url  # type: ignore[attr-defined]
    return _Bound


# ============== Entrypoint ==============

def main() -> int:
    parser = argparse.ArgumentParser(description="webui_proxy — :8648 → :8649 with usage/stats fix")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM,
                        help="Upstream base URL (the npm package webui)")
    parser.add_argument("--bridge", default=DEFAULT_BRIDGE_URL,
                        help="Bridge base URL (for paths like /api/hermes/llama/restart)")
    parser.add_argument("--state-db", default=DEFAULT_STATE_DB,
                        help="Path to state.db (relative to HERMES_ROOT if not absolute)")
    parser.add_argument("--upstream-timeout", type=float, default=DEFAULT_UPSTREAM_TIMEOUT)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    state_db = Path(args.state_db)
    if not state_db.is_absolute():
        # Resolve relative to HERMES_ROOT (the start.ps1 sets this)
        root = os.environ.get("HERMES_ROOT")
        if root:
            state_db = Path(root) / state_db
        else:
            state_db = state_db.resolve()

    handler_cls = make_handler(args.upstream, state_db, args.upstream_timeout, args.bridge)
    httpd = ThreadingHTTPServer((args.bind, args.port), handler_cls)

    sys.stderr.write(
        f"[webui_proxy] listening on http://{args.bind}:{args.port}  "
        f"upstream={args.upstream}  state_db={state_db}\n"
    )
    sys.stderr.write(
        f"[webui_proxy] intercepting: {USAGE_STATS_PATH}  (passing through everything else)\n"
    )
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("[webui_proxy] shutting down (SIGINT)\n")
    finally:
        httpd.server_close()
    return 0





# ===================================================================
# Icarus recovery injection (UI restart conversation continuity)
# ===================================================================

_INJECT_TARGETS = frozenset({"/", "/index.html", "/index.htm", ""})


def _should_inject_recovery(
    path: str, headers: list[tuple[str, str]], body: bytes
) -> bool:
    bare = path.split("?", 1)[0]
    if bare not in _INJECT_TARGETS:
        return False
    ctype = ""
    for k, v in headers:
        if k.lower() == "content-type":
            ctype = v.lower()
            break
    if "text/html" not in ctype:
        return False
    head = body[:512].lower()
    if b"<!doctype" not in head and b"<html" not in head:
        return False
    return True


_INJECTION_SENTINEL = b"<!-- icarus-recovery:v1 -->"


def _build_recovery_injection() -> bytes:
    return (
        _INJECTION_SENTINEL
        + b'<script src="/_icarus/recovery.js" defer></script>'
    )


def _inject_before_closing_head(body: bytes, injection: bytes) -> bytes:
    head_close = body.lower().find(b"</head>")
    if head_close >= 0:
        return body[:head_close] + injection + body[head_close:]
    body_open = body.lower().find(b"<body")
    if body_open >= 0:
        return body[:body_open] + injection + body[body_open:]
    return body + injection


# Recovery JS — embedded inline. Always git-tracked alongside the proxy
# so webui npm updates can NEVER clobber the integration.
_RECOVERY_JS_SOURCE = b'// Icarus session recovery \xe2\x80\x94 runs in webui SPA context\n// Loaded by webui_proxy via <script defer>. Talks to bridge via webui_proxy.\n//\n// What this does:\n//   1. On page load, fetches /api/hermes/icarus/active-session\n//   2. If a recent active session exists, shows a "Resume previous conversation?"\n//      toast in the corner\n//   3. On Resume click, calls /api/hermes/icarus/session/{id}/resume-context,\n//      stores the system prompt in localStorage, then navigates to /chat\n//   4. SPA chat page (when loaded) can read the stored context and seed\n//      the new conversation with the previous context\n//\n// Design constraints:\n//   - Pure vanilla JS \xe2\x80\x94 no framework deps (loaded before SPA hydrates)\n//   - Defensive: respects user dismissals (sessionStorage)\n//   - Self-cleaning: removes toast if SPA navigates away\n\n(function () {\n  "use strict";\n\n  const API = "/api/hermes/icarus";\n  const STORAGE_KEY = "icarus_recovery_state";\n  const RESUME_CONTEXT_KEY = "icarus_resume_context";\n  let shown = false;\n\n  function escapeHtml(s) {\n    return String(s).replace(/[<>&"\']/g, function (c) {\n      return ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", \'"\': "&quot;", "\'": "&#39;" })[c];\n    });\n  }\n\n  function mountToast(html) {\n    const div = document.createElement("div");\n    div.id = "icarus-recovery-toast";\n    div.setAttribute("role", "alert");\n    div.style.cssText = [\n      "position:fixed", "top:16px", "right:16px", "z-index:999999",\n      "max-width:380px", "background:#1f2937", "color:#f3f4f6",\n      "border:1px solid #4b5563", "border-radius:8px",\n      "padding:14px 18px", "font:13px/1.5 system-ui,-apple-system,sans-serif",\n      "box-shadow:0 6px 24px rgba(0,0,0,.45)",\n      "animation:icarus-fade-in .3s ease-out"\n    ].join(";");\n    div.innerHTML = html;\n    document.body.appendChild(div);\n  }\n\n  function dismiss(reason) {\n    const el = document.getElementById("icarus-recovery-toast");\n    if (el) el.remove();\n    try {\n      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({\n        dismissed: reason,\n        ts: Date.now(),\n      }));\n    } catch (e) {}\n  }\n\n  async function check() {\n    if (shown) return;\n    shown = true;\n\n    // Respect user\'s recent dismiss (within last 30 minutes)\n    try {\n      const raw = sessionStorage.getItem(STORAGE_KEY);\n      if (raw) {\n        const s = JSON.parse(raw);\n        if (s.dismissed && (Date.now() - s.ts) < 30 * 60 * 1000) return;\n        if (s.dismissed === "never") return;\n      }\n    } catch (e) {}\n\n    let info;\n    try {\n      const r = await fetch(API + "/active-session", {\n        credentials: "include",\n      });\n      if (!r.ok) return;\n      info = await r.json();\n    } catch (e) {\n      return;\n    }\n    if (!info || !info.found) return;\n    // Only suggest if the session was active within the last 24h\n    if (info.age_seconds != null && info.age_seconds > 24 * 60 * 60) return;\n\n    const ageStr = info.age_human || "recently";\n    const lastMsg = (info.last_messages || []).slice(-1)[0] || {};\n    const excerpt = (lastMsg.content_excerpt || "").slice(0, 160);\n    const safeTitle = escapeHtml(info.title || "(untitled conversation)");\n    const safeExcerpt = escapeHtml(excerpt);\n\n    const html = [\n      \'<div style="font-weight:600;margin-bottom:6px;font-size:14px;display:flex;align-items:center;gap:6px">\',\n        \'\xf0\x9f\xaa\xb6 <span>\xe4\xbc\x8a\xe5\x8d\xa1\xe6\xb4\x9b\xe6\x96\xaf: \xe4\xb8\x8a\xe6\xac\xa1\xe5\xaf\xb9\xe8\xaf\x9d\xe6\x9c\xaa\xe5\xae\x8c\xe6\x88\x90</span>\',\n      \'</div>\',\n      \'<div style="opacity:.85;margin-bottom:6px">\',\n        \'<b>\', safeTitle, \'</b>\',\n        \' \xc2\xb7 \', String(info.message_count || 0), \' \xe6\x9d\xa1\xe6\xb6\x88\xe6\x81\xaf \xc2\xb7 \', ageStr,\n      \'</div>\',\n      excerpt ? (\'<div style="opacity:.7;margin-bottom:12px;font-style:italic;border-left:2px solid #4b5563;padding-left:8px">&quot;\' +\n        safeExcerpt + \'&quot;</div>\') : \'\',\n      \'<div style="display:flex;gap:8px;margin-top:8px">\',\n        \'<button id="icarus-resume" style="background:#10b981;color:#fff;border:0;padding:6px 14px;border-radius:4px;cursor:pointer;font-weight:500">\xe7\xbb\xa7\xe7\xbb\xad\xe4\xb8\x8a\xe6\xac\xa1</button>\',\n        \'<button id="icarus-dismiss" style="background:transparent;color:#9ca3af;border:1px solid #4b5563;padding:6px 14px;border-radius:4px;cursor:pointer">\xe4\xbb\xa5\xe5\x90\x8e\xe5\x86\x8d\xe8\xaf\xb4</button>\',\n        \'<button id="icarus-dismiss-session" style="background:transparent;color:#6b7280;border:0;padding:6px 14px;cursor:pointer;text-decoration:underline;font-size:12px">\xe4\xb8\x8d\xe5\x86\x8d\xe6\x8f\x90\xe9\x86\x92</button>\',\n      \'</div>\',\n    ].join("");\n\n    function showNow() {\n      if (document.body) {\n        mountToast(html);\n        const resumeBtn = document.getElementById("icarus-resume");\n        const dismissBtn = document.getElementById("icarus-dismiss");\n        const neverBtn = document.getElementById("icarus-dismiss-session");\n        if (resumeBtn) resumeBtn.onclick = () => resume(info);\n        if (dismissBtn) dismissBtn.onclick = () => dismiss("later");\n        if (neverBtn) neverBtn.onclick = () => dismiss("never");\n      } else {\n        setTimeout(showNow, 100);\n      }\n    }\n    showNow();\n  }\n\n  async function resume(info) {\n    const btn = document.getElementById("icarus-resume");\n    if (btn) {\n      btn.disabled = true;\n      btn.textContent = "\xe5\x8a\xa0\xe8\xbd\xbd\xe4\xb8\x8a\xe4\xb8\x8b\xe6\x96\x87...";\n    }\n    try {\n      const r = await fetch(\n        API + "/session/" + encodeURIComponent(info.session_id) + "/resume-context",\n        { method: "POST", credentials: "include" }\n      );\n      if (!r.ok) throw new Error("HTTP " + r.status);\n      const ctx = await r.json();\n      try {\n        localStorage.setItem(RESUME_CONTEXT_KEY, JSON.stringify({\n          session_id: info.session_id,\n          title: info.title,\n          system_prompt: ctx.system_prompt,\n          summary: ctx.summary,\n          ts: Date.now(),\n        }));\n      } catch (e) {}\n      // Navigate to chat. SPA typically routes /chat or / (default).\n      // We use /chat \xe2\x80\x94 adjust if SPA uses a different path.\n      dismiss("resumed");\n      const profile = info.profile && info.profile !== "default"\n        ? "?profile=" + encodeURIComponent(info.profile) : "";\n      window.location.href = "/chat" + profile;\n    } catch (e) {\n      if (btn) {\n        btn.disabled = false;\n        btn.textContent = "\xe9\x87\x8d\xe8\xaf\x95";\n      }\n    }\n  }\n\n  // Inject a small CSS animation for fade-in\n  function injectCss() {\n    if (document.getElementById("icarus-recovery-css")) return;\n    const s = document.createElement("style");\n    s.id = "icarus-recovery-css";\n    s.textContent = "@keyframes icarus-fade-in { from { opacity:0; transform:translateY(-8px) } to { opacity:1; transform:translateY(0) } }";\n    (document.head || document.documentElement).appendChild(s);\n  }\n\n  function init() {\n    injectCss();\n    check();\n  }\n\n  if (document.readyState === "loading") {\n    document.addEventListener("DOMContentLoaded", init);\n  } else {\n    init();\n  }\n})();'


def _serve_recovery_js(self) -> None:
    body = _RECOVERY_JS_SOURCE
    self.send_response(HTTPStatus.OK)
    self.send_header("Content-Type", "application/javascript; charset=utf-8")
    self.send_header("Content-Length", str(len(body)))
    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
    self.send_header("Access-Control-Allow-Origin", "*")
    self.end_headers()
    self.wfile.write(body)


if __name__ == "__main__":
    raise SystemExit(main())


