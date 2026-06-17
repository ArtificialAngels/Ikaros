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
# Plugin management paths (new in 2026-06-17: E方案 — break hermes-web-ui's
# "read-only" stance so the UI can actually manage plugins, with safety:
#   - only listen on 127.0.0.1 (external CSRF impossible)
#   - whitelist of action verbs (no shell injection)
#   - 5s subprocess timeout
#   - proxy never reveals stderr to the SPA
PLUGINS_PREFIX = "/api/hermes/plugins/"
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
        else:
            self._proxy_pass()

    def do_POST(self) -> None:
        if self.path.startswith(PLUGINS_PREFIX):
            # Read body first, then route to handler. The handler does NOT
            # forward the request to upstream — it shells out to hermes CLI.
            self._handle_plugin_with_body()
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

    # ---------- Handlers ----------
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
        self.end_headers()
        self.wfile.write(resp_body)


def make_handler(upstream_base: str, state_db: Path, upstream_timeout: float) -> type[ProxyHandler]:
    """Factory to inject config into the handler class."""
    class _Bound(ProxyHandler):
        pass
    _Bound.upstream_base = upstream_base
    _Bound.state_db = state_db
    _Bound.upstream_timeout = upstream_timeout
    return _Bound


# ============== Entrypoint ==============

def main() -> int:
    parser = argparse.ArgumentParser(description="webui_proxy — :8648 → :8649 with usage/stats fix")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM,
                        help="Upstream base URL (the npm package webui)")
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

    handler_cls = make_handler(args.upstream, state_db, args.upstream_timeout)
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


if __name__ == "__main__":
    raise SystemExit(main())
