#!/usr/bin/env python3
"""Thin patcher that layers studio-update on top of the standard dashboard.

Import order matters — we patch class attributes BEFORE any requests
are handled.

This script is *not* tracked by git (lives under bin/) so it survives
``git checkout`` of the tools/ikaros-dashboard/ tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── add dashboard dir to path ──────────────────────────────────────────
HERE = Path(__file__).resolve().parent
DASHBOARD = HERE.parent / "tools" / "ikaros-dashboard"
sys.path.insert(0, str(DASHBOARD))

# ── import server first (its globals like ENV/component_start are used
#    by studio_update later) ────────────────────────────────────────────
import server  # noqa: E402

# override which HTML file is served as the home page
server.INDEX_HTML = DASHBOARD / "panel.html"

# patch _send_html to add Cache-Control (prevent stale cached page)
_original_send_html = server.DashboardHandler._send_html

def _send_html_patched(self) -> None:
    if not server.INDEX_HTML.exists():
        self.send_error(404, "panel.html not found")
        return
    with open(str(server.INDEX_HTML), "rb") as f:
        body = f.read()
    self.send_response(200)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
    self.send_header("Pragma", "no-cache")
    self.send_header("Expires", "0")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

server.DashboardHandler._send_html = _send_html_patched

# ── now import studio_update (needs server globals) ────────────────────
import studio_update  # noqa: E402

# ── monkey-patch do_GET / do_POST to add studio-update routes ──────────
_original_do_GET = server.DashboardHandler.do_GET
_original_do_POST = server.DashboardHandler.do_POST

def _do_GET_patched(self) -> None:
    parsed = server.urllib.parse.urlparse(self.path)
    path = parsed.path.rstrip("/") or "/"
    if path == "/api/studio/update" or path == "/api/studio/update/log":
        with studio_update._studio_update_mlock:
            self._send_json({
                "lines": list(studio_update._studio_update_lines),
                "updating": studio_update._studio_updating,
            })
        return
    return _original_do_GET(self)

def _do_POST_patched(self) -> None:
    parsed = server.urllib.parse.urlparse(self.path)
    path = parsed.path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "studio" and parts[2] == "update":
        with studio_update.studio_update_lock:
            if studio_update._studio_updating:
                self._send_json({"ok": False, "msg": "Studio 更新进行中，请稍候"})
                return
        server.threading.Thread(target=studio_update.run_studio_local_update, daemon=True).start()
        self._send_json({"ok": True, "msg": "已启动 Studio 本地更新"})
        return
    return _original_do_POST(self)

server.DashboardHandler.do_GET = _do_GET_patched
server.DashboardHandler.do_POST = _do_POST_patched

# adjust the startup log to mention the new endpoints
_original_main = server.main

def _main_patched() -> None:
    # Save original main reference so we don't lose it
    import logging
    log = logging.getLogger("ikaros-dashboard")
    # Start the server normally
    _original_main()

server.main = _main_patched

if __name__ == "__main__":
    server.main()
