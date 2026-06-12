"""
Hermes - Unified download manager.

Merged from hermes/download.py + hermes/gopeed_client.py.
Supports aria2c multi-threaded downloads, urllib fallback, and gopeed-web API.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

HERMES_ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME = HERMES_ROOT / "runtime"

DEFAULT_GOPEED_BASE = "http://127.0.0.1:9999"


# ============================================================================
# Aria2c / urllib download manager
# ============================================================================

def find_aria2c() -> Optional[Path]:
    """Find aria2c executable (bundled or system PATH)."""
    bundled = RUNTIME / "aria2c.exe"
    if bundled.exists():
        return bundled
    system = shutil.which("aria2c") or shutil.which("aria2c.exe")
    if system:
        return Path(system)
    return None


class DownloadManager:
    """Download manager with automatic mirror + aria2c support."""

    def __init__(
        self,
        use_aria2: bool = True,
        on_progress: Callable | None = None,
        mirror_enabled: bool = True,
    ):
        self._aria2: Optional[Path] = find_aria2c() if use_aria2 else None
        self._on_progress = on_progress
        self._mirror_enabled = mirror_enabled

    @property
    def has_aria2(self) -> bool:
        return self._aria2 is not None

    def _apply_mirror(self, url: str) -> str:
        if not self._mirror_enabled:
            return url
        try:
            from modules.model_manager.mirror import mirror_url
            return mirror_url(url)
        except Exception:
            return url

    def download(
        self,
        url: str,
        dest_dir: str | Path,
        filename: Optional[str] = None,
        connections: int = 8,
        timeout: int = 0,
    ) -> Path:
        url = self._apply_mirror(url)
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        if filename is None:
            parsed = urlparse(url)
            fname = parsed.path.rsplit("/", 1)[-1] or "download"
        else:
            fname = filename

        dest = dest_dir / fname

        if self._aria2:
            return self._download_aria2(url, dest, connections, timeout)
        else:
            return self._download_urllib(url, dest, timeout)

    def _download_aria2(self, url: str, dest: Path, connections: int, timeout: int) -> Path:
        cmd = [
            str(self._aria2),
            f"--max-connection-per-server={connections}",
            f"--split={connections}",
            "--min-split-size=1M",
            "--console-log-level=error",
            "--summary-interval=0",
            f"--dir={dest.parent}",
            f"--out={dest.name}",
            url,
        ]
        if timeout > 0:
            cmd.insert(1, f"--timeout={timeout}")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        last_report = 0
        for line in proc.stdout:
            line = line.strip()
            if self._on_progress and line.startswith("[") and "%" in line:
                t = time.time()
                if t - last_report >= 0.3:
                    last_report = t
                    try:
                        pct_str = line[line.index("(") + 1 : line.index("%)")]
                        spd_str = ""
                        eta_str = ""
                        if "SPD:" in line:
                            spd_part = line[line.index("SPD:") + 4 :]
                            spd_str = spd_part.split()[0]
                        if "ETA:" in line:
                            eta_part = line[line.index("ETA:") :]
                            eta_str = eta_part.split("]")[0]
                        self._on_progress(int(pct_str), spd_str, eta_str)
                    except Exception:
                        pass

        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"aria2c exited with code {proc.returncode}")
        if not dest.exists():
            raise FileNotFoundError(f"aria2c completed but file not found: {dest}")
        return dest

    def _download_urllib(self, url: str, dest: Path, timeout: int) -> Path:
        if dest.exists():
            dest.unlink()

        def _report(count, block_size, total_size):
            if total_size <= 0 or not self._on_progress:
                return
            pct = min(100, int(count * block_size / total_size * 100))
            self._on_progress(pct, "", "")

        try:
            urllib.request.urlretrieve(
                url, str(dest), reporthook=_report if self._on_progress else None
            )
        except Exception as e:
            if dest.exists():
                dest.unlink()
            raise RuntimeError(f"Download failed: {e}")
        return dest

    def download_many(self, urls: list[str], dest_dir: str | Path, connections: int = 8) -> list[Path]:
        results = []
        for url in urls:
            results.append(self.download(url, dest_dir, connections=connections))
        return results


def download_file(
    url: str,
    dest_dir: str | Path,
    filename: Optional[str] = None,
    use_aria2: bool = True,
    mirror: bool = True,
    on_progress: Callable | None = None,
) -> Path:
    """Quick one-shot download with mirror + aria2 support."""
    dm = DownloadManager(use_aria2=use_aria2, on_progress=on_progress, mirror_enabled=mirror)
    return dm.download(url, dest_dir, filename)


# ============================================================================
# Gopeed-web HTTP API client
# ============================================================================

class GopeedClient:
    """Thin HTTP client for gopeed-web's /api/v1/* endpoints."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 10.0):
        self.base_url = (base_url or os.environ.get("HERMES_GOPEED_URL") or DEFAULT_GOPEED_BASE).rstrip("/")
        self.timeout = timeout

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

    def list_tasks(self) -> list[dict]:
        return self._request("GET", "/api/v1/tasks") or []

    def get_task(self, task_id: str) -> dict:
        return self._request("GET", f"/api/v1/tasks/{task_id}")

    def create_task(self, url: str, save_dir: Optional[str] = None,
                    name: Optional[str] = None, connections: int = 16,
                    extra: Optional[dict] = None) -> str:
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
        if not isinstance(result, str):
            raise RuntimeError(f"gopeed create returned non-string: {result!r}")
        return result

    def delete_task(self, task_id: str) -> dict:
        return self._request("DELETE", f"/api/v1/tasks/{task_id}")

    def pause_task(self, task_id: str) -> dict:
        return self._request("POST", f"/api/v1/tasks/{task_id}/pause")

    def resume_task(self, task_id: str) -> dict:
        return self._request("POST", f"/api/v1/tasks/{task_id}/resume")

    def get_config(self) -> dict:
        return self._request("GET", "/api/v1/config")

    def update_config(self, **kwargs) -> dict:
        return self._request("PUT", "/api/v1/config", kwargs)

    def wait_for_task(self, task_id: str, poll_interval: float = 5.0,
                      timeout: float = 86400.0) -> dict:
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
        task_id = self.create_task(url, save_dir=save_dir, name=name)
        if on_progress:
            while True:
                t = self.get_task(task_id)
                prog = (t.get("meta", {}).get("progress") or t.get("progress") or {})
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


def get_gopeed_client(base_url: Optional[str] = None) -> GopeedClient:
    return GopeedClient(base_url=base_url)


# ---- CLI for testing ----

if __name__ == "__main__":
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
