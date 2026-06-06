r"""
Hermes Agent - gopeed-web HTTP API client (Python communication bridge).

Gopeed-web is a headless downloader (Go, single-exe ~89MB) running on :9999.
Hermes Python code uses this client to:
- List active download tasks
- Create new download tasks (URL + save dir)
- Pause / resume / delete tasks
- Poll progress

API note: gopeed-web differs from the desktop gopeed build:
- POST body wraps URL in {"req": {"url": "..."}}
- "path" is a *directory* (not a file path); "name" is the filename
- POST response: data is the task id (string), not a task object
- GET response: data is the full task object

No dependencies: uses only urllib (no requests/httpx needed).
"""
from __future__ import annotations
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional


DEFAULT_BASE = "http://127.0.0.1:9999"


class GopeedClient:
    """Thin HTTP client for gopeed-web's /api/v1/* endpoints."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 10.0):
        self.base_url = (base_url or os.environ.get("HERMES_GOPEED_URL") or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout

    # ---- low-level HTTP ----

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = json.loads(r.read().decode("utf-8"))
                if payload.get("code") != 0:
                    raise RuntimeError(f"gopeed API error: {payload.get('msg')}")
                return payload.get("data")
        except urllib.error.URLError as e:
            raise ConnectionError(f"gopeed-web unreachable at {self.base_url}: {e}") from e

    def available(self) -> bool:
        try:
            self._request("GET", "/api/v1/tasks")
            return True
        except Exception:
            return False

    # ---- task lifecycle ----

    def list_tasks(self) -> list[dict]:
        """Return all tasks (active + history)."""
        return self._request("GET", "/api/v1/tasks") or []

    def get_task(self, task_id: str) -> dict:
        return self._request("GET", f"/api/v1/tasks/{task_id}")

    def create_task(self, url: str, save_dir: Optional[str] = None,
                    name: Optional[str] = None,
                    connections: int = 16,
                    extra: Optional[dict] = None) -> str:
        """Create a new download task. Returns the task id.

        save_dir: directory to save into (defaults to gopeed's downloadDir)
        name: filename hint (gopeed infers from URL if not set)
        """
        req: dict[str, Any] = {"url": url}
        if extra:
            req.update(extra)
        body: dict[str, Any] = {"req": req}
        if save_dir or name or connections:
            opts: dict[str, Any] = {}
            if save_dir:
                opts["path"] = save_dir
            if name:
                opts["name"] = name
            opts["extra"] = {"connections": connections}
            body["opts"] = opts
        result = self._request("POST", "/api/v1/tasks", body)
        # POST response data is a string (task id)
        if not isinstance(result, str):
            raise RuntimeError(f"gopeed create returned non-string: {result!r}")
        return result

    def delete_task(self, task_id: str) -> dict:
        return self._request("DELETE", f"/api/v1/tasks/{task_id}")

    def pause_task(self, task_id: str) -> dict:
        return self._request("POST", f"/api/v1/tasks/{task_id}/pause")

    def resume_task(self, task_id: str) -> dict:
        return self._request("POST", f"/api/v1/tasks/{task_id}/resume")

    # ---- config ----

    def get_config(self) -> dict:
        return self._request("GET", "/api/v1/config")

    def update_config(self, **kwargs) -> dict:
        return self._request("PUT", "/api/v1/config", kwargs)

    # ---- convenience: poll until done ----

    def wait_for_task(self, task_id: str, poll_interval: float = 5.0,
                      timeout: float = 86400.0) -> dict:
        """Poll a task until it reaches a terminal state. Returns final state dict."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            t = self.get_task(task_id)
            state = (t.get("status") or "").lower()
            if state in ("done", "succeed", "success"):
                return t
            if state in ("error", "failed", "canceled", "cancelled"):
                raise RuntimeError(f"task {task_id} ended in state={state}: {t}")
            time.sleep(poll_interval)
        raise TimeoutError(f"task {task_id} did not finish in {timeout}s")

    def download(self, url: str, save_dir: str, name: Optional[str] = None,
                on_progress=None, poll_interval: float = 5.0) -> str:
        """High-level: create task, poll until done, return task id."""
        task_id = self.create_task(url, save_dir=save_dir, name=name)
        if on_progress:
            while True:
                t = self.get_task(task_id)
                prog = (t.get("meta", {}).get("progress") or
                        t.get("progress") or {})
                pct = int(prog.get("downloaded", 0) / max(1, prog.get("used", 1)) * 100) if prog.get("used") else 0
                on_progress(pct, t)
                state = (t.get("status") or "").lower()
                if state in ("done", "succeed", "success"):
                    return task_id
                if state in ("error", "failed", "canceled", "cancelled"):
                    raise RuntimeError(f"task ended in state={state}")
                time.sleep(poll_interval)
        else:
            self.wait_for_task(task_id, poll_interval=poll_interval)
            return task_id


# ---- module-level convenience ----

def get_client(base_url: Optional[str] = None) -> GopeedClient:
    return GopeedClient(base_url=base_url)


# ---- CLI for testing ----

if __name__ == "__main__":
    import sys
    c = GopeedClient()
    if not c.available():
        print(f"[FAIL] gopeed-web unreachable at {c.base_url}")
        sys.exit(1)
    print(f"[OK] gopeed-web at {c.base_url}")
    tasks = c.list_tasks()
    if not tasks:
        print("  no tasks")
    for t in tasks[:10]:
        url = t.get("meta", {}).get("req", {}).get("url", "?")
        print(f"  {t['id']}  {t.get('status','?'):<10}  {url[:60]}")
