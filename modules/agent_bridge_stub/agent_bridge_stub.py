"""
modules/agent_bridge_stub/agent_bridge_stub.py

Minimal TCP stub for the npm hermes-web-ui Agent Bridge broker.

Why this exists
---------------
The npm package `hermes-web-ui@0.6.21` ships a Node backend that, on
session resume, calls `bridge.statusIfLoaded(session_id)` on a broker
that is supposed to listen on `tcp://127.0.0.1:18765`. The real broker
is `hermes_bridge.py` (Python) shipped in the hermes-agent source repo,
but the npm install in this project can't find that script:

  - webui's `G4I()` searches 3 hardcoded paths:
      <__dirname>/agent-bridge/python/hermes_bridge.py
      <__dirname>/services/hermes/agent-bridge/python/hermes_bridge.py
      <cwd>/packages/server/src/services/hermes/agent-bridge/python/hermes_bridge.py
  - but the npm install places the script under
      node_modules/.hermes-web-ui-<hash>/dist/server/agent-bridge/python/hermes_bridge.py
    (pnpm-style flat-installation cache) -- none of G4I's candidates
  - so webui's spawn() throws "agent bridge Python script not found"
    and the broker never starts

The result is that any chat session with `source="api_server"` (which
is what the local :7860 FastAPI bridge writes into state.db) triggers a
broker lookup on session resume, which throws:

    Unable to confirm Agent Bridge status while resuming:
    connect ECONNREFUSED [redacted endpoint]

The real broker runs the "autonomous agent loop" feature. This project
doesn't use that feature -- the local FastAPI bridge on :7860 covers
all session / icarus / memory / RAG needs, and the resume path we DO
care about (Icarus "Resume previous conversation?" toast) is served
from :7860 by `modules/webui_proxy._proxy_to_bridge()`, which never
touches the npm broker.

So we stub the broker with a minimal newline-delimited JSON TCP server
that always answers `{"ok": true, "running": false, ...}`. When webui
calls `statusIfLoaded()` and gets `running === false`, its
`reattachBridgeRun()` takes the early-return branch (`if (!a || !Z)
return;`) and never enters the catch block that emits the error toast.

Wire protocol (recovered from webui's `dist/server/index.js` class
`_I`, the `request()` method, ~line 1389454):
   request  := JSON.stringify(payload) + "\\n"
   response := JSON line `{"ok": bool, ...}`

Actions handled (all return ok:true with running:false -- the stub
never has running work):
   ping             -> {ok: true, running: false}
   status           -> {ok: true, running: false, current_run_id: null}
   status_if_loaded -> {ok: true, running: false, current_run_id: null}
   list             -> {ok: true, running: false, runs: []}
   get_history      -> {ok: true, messages: []}
   destroy          -> {ok: true}
   shutdown         -> {ok: true} (then close the connection)
   anything else    -> {ok: true}

Stdlib only -- no third-party deps. 50 lines.
"""
from __future__ import annotations

import json
import socketserver
import sys
import threading
import time
from typing import Any

HOST = "127.0.0.1"
PORT = 18765

# Lightweight connection / request counters so the operator can spot
# broker traffic in the supervisor log without enabling TRACE.
_lock = threading.Lock()
_active = 0
_total_conns = 0
_total_reqs = 0


def _log(msg: str) -> None:
    sys.stderr.write(
        f"[bridge-stub {time.strftime('%H:%M:%S')}] {msg}\n"
    )
    sys.stderr.flush()


def _respond(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a response for a given action. Always ok:true; never running."""
    action = str(payload.get("action", ""))
    base: dict[str, Any] = {"ok": True}
    if action in ("status", "status_if_loaded", "ping", "list"):
        base["running"] = False
        base["current_run_id"] = None
    if action == "list":
        base["runs"] = []
    if action == "get_history":
        base["messages"] = []
    # destroy / shutdown / cancel / abort / approve / reject -> ok:true only
    return base


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        global _active, _total_conns, _total_reqs
        with _lock:
            _active += 1
            _total_conns += 1
            conn_id = _total_conns
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        _log(f"client connected from {peer} (id={conn_id}, active={_active})")
        try:
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                with _lock:
                    _total_reqs += 1
                try:
                    req = json.loads(line)
                except json.JSONDecodeError as e:
                    _log(f"  bad json ({e}); raw={line!r}")
                    self.wfile.write(
                        b'{"ok": false, "error": "invalid json"}\n'
                    )
                    self.wfile.flush()
                    continue
                action = str(req.get("action", "?"))
                session = req.get("session_id", "") or ""
                _log(f"  action={action} session={session}")
                resp = _respond(req)
                out = (json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8")
                self.wfile.write(out)
                self.wfile.flush()
                if action == "shutdown":
                    _log("  shutdown requested; closing connection")
                    break
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            with _lock:
                _active -= 1
            _log(f"client disconnected (id={conn_id}, active={_active})")


class _ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    server = _ThreadedServer((HOST, PORT), _Handler)
    _log(
        f"listening on tcp://{HOST}:{PORT} "
        f"(no real agent work; resume-safe stub; "
        f"answers status_if_loaded with running:false so webui's "
        f"reattachBridgeRun takes the early-return path)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("shutting down (KeyboardInterrupt)")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
