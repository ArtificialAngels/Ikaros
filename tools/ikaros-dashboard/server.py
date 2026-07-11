#!/usr/bin/env python3
"""ikaros-dashboard — real-time monitoring dashboard server (stdlib-only).

Endpoints:
  GET  /             → index.html
  GET  /api/log      → last 200 events from ikaros-monitor.jsonl (JSON)
  GET  /api/state    → V5 affect.json + pending_thought.json (JSON)
  GET  /api/events   → SSE stream of new events (tail -f on JSONL)
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import pathlib
import socket
import threading
import time
import urllib.parse

# ── config ─────────────────────────────────────────────────────────────
PORT = 9100
HERMES_ROOT = pathlib.Path(os.environ.get("HERMES_ROOT", "E:\\Ikaros"))
MONITOR_FILE = HERMES_ROOT / "data" / "logs" / "ikaros-monitor.jsonl"
AFFECT_FILE = HERMES_ROOT / "Ikaros-memory" / "data" / "v5" / "affect.json"
PENDING_THOUGHT_FILE = HERMES_ROOT / "Ikaros-memory" / "data" / "v5" / "pending_thought.json"
HERE = pathlib.Path(__file__).resolve().parent
INDEX_HTML = HERE / "index.html"
POLL_INTERVAL = 0.8  # seconds between file polls for SSE

log = logging.getLogger("ikaros-dashboard")

# ── file → in-memory cache ────────────────────────────────────────────
_log_cache: list[dict] = []
_log_cache_lock = threading.Lock()
_log_file_pos: int = 0  # bytes we've already read from MONITOR_FILE


def _normalize(entry: dict) -> dict:
    """Normalize old-format (type) and new-format (kind) entries into a
    uniform shape for the front‑end."""
    kind = entry.get("kind") or entry.get("type", "unknown")
    text = entry.get("text", "")
    ts = entry.get("ts", time.time())

    display_kind = kind
    # map cryptic internal kinds → front‑end labels
    kind_map = {
        "user_msg": "user_msg",
        "assistant_msg": "assistant_msg",
        "thought": "thought",
        "status": "status",
        "state": "state",
        "stt": "stt",
    }
    display_kind = kind_map.get(kind, kind)

    return {
        "kind": kind,
        "display_kind": display_kind,
        "text": text,
        "ts": ts,
        "session_id": entry.get("session_id", ""),
        "mood": entry.get("mood", ""),
        "intensity": entry.get("intensity"),
        "raw": entry,
    }


def _reload_cache() -> None:
    """Reload file position → tail into _log_cache."""
    global _log_file_pos
    if not MONITOR_FILE.exists():
        return
    with _log_cache_lock:
        try:
            with open(str(MONITOR_FILE), "rb") as f:
                f.seek(_log_file_pos)
                while True:
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        _log_cache.append(_normalize(raw))
                    except json.JSONDecodeError:
                        continue
                _log_file_pos = f.tell()
            # keep at most 500
            if len(_log_cache) > 500:
                _log_cache[:] = _log_cache[-500:]
        except Exception:
            log.exception("reload_cache")


def _read_tail(count: int = 200) -> list[dict]:
    """Return last *count* normalized events."""
    _reload_cache()
    with _log_cache_lock:
        return _log_cache[-count:]


def _read_v5_state() -> dict:
    """Read affect.json + pending_thought.json."""
    state: dict = {}
    for path, key in [(AFFECT_FILE, "affect"), (PENDING_THOUGHT_FILE, "thought")]:
        if path.exists():
            try:
                with open(str(path), "r", encoding="utf-8") as f:
                    state[key] = json.load(f)
            except Exception:
                state[key] = None
        else:
            state[key] = None
    return state


# ── SSE helpers ────────────────────────────────────────────────────────

def _sse_event(wfile, data: dict, event: str | None = None) -> None:
    """Write one SSE event to *wfile*."""
    payload = json.dumps(data, ensure_ascii=False)
    if event:
        wfile.write(f"event: {event}\n".encode())
    wfile.write(f"data: {payload}\n\n".encode())
    wfile.flush()


# ── HTTP request handler ───────────────────────────────────────────────

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    # silence per-request logs from stdlib
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _send_json(self, data: dict | list, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        if not INDEX_HTML.exists():
            self.send_error(404, "index.html not found")
            return
        with open(str(INDEX_HTML), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_sse(self) -> None:
        """SSE endpoint — long‑poll tailing the JSONL file."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # send initial keepalive
        _sse_event(self.wfile, {"status": "connected"}, event="connected")

        last_count = 0
        last_ping = time.time()
        try:
            while not self.server._stop:
                _reload_cache()
                with _log_cache_lock:
                    new_entries = _log_cache[last_count:]
                    current_count = len(_log_cache)

                if new_entries:
                    for entry in new_entries:
                        _sse_event(self.wfile, entry, event="monitor")
                    last_count = current_count
                    last_ping = time.time()
                else:
                    # keepalive every 15 s
                    if time.time() - last_ping > 15:
                        _sse_event(self.wfile, {"ts": time.time()}, event="ping")
                        last_ping = time.time()

                time.sleep(POLL_INTERVAL)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client disconnected
        except Exception:
            log.exception("SSE handler")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._send_html()
        elif path == "/api/log":
            events = _read_tail(200)
            self._send_json(events)
        elif path == "/api/state":
            state = _read_v5_state()
            self._send_json(state)
        elif path == "/api/events":
            self._handle_sse()
        else:
            self.send_error(404)

    do_POST = do_PUT = do_DELETE = lambda s: s.send_error(405)


# ── threaded server ────────────────────────────────────────────────────

class ThreadedSSEServer(http.server.ThreadingHTTPServer):
    """HTTP server with a stop flag for SSE threads."""
    _stop: bool = False

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.daemon_threads = True


def _find_free_port(start: int = PORT) -> int:
    port = start
    while port < start + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError(f"no free port in [{start}, {start+100})")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    port = _find_free_port(PORT)
    server = ThreadedSSEServer(("0.0.0.0", port), DashboardHandler)
    log.info("ikaros-dashboard listening on http://127.0.0.1:%d", port)
    log.info("  /            → dashboard UI")
    log.info("  /api/log     → last 200 events (JSON)")
    log.info("  /api/state   → V5 affect + thought state (JSON)")
    log.info("  /api/events   → SSE real-time stream")
    print(f"\n  🪶 Ikaros Dashboard → http://127.0.0.1:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server._stop = True
        server.shutdown()
        log.info("server stopped")


if __name__ == "__main__":
    main()
