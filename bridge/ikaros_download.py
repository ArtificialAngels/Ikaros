"""
Ikaros Downloader - 备选下载通道
================================
优先用 aria2c（轻量、CLI 集成），复杂任务用 Gopeed REST API。
两个都用上：覆盖简单到复杂的所有下载场景。
"""
import os
import json
import time
import subprocess
import urllib.request
from typing import Optional
from pathlib import Path

# === 路径常量 ===
ARIA2C = r"E:\Hermes Agent\runtime\aria2c.exe"
GOPEED_API = "http://127.0.0.1:9999/api/v1"


class Aria2Downloader:
    """CLI 集成。简单直接，适合小到中等文件。"""

    def __init__(self, connections: int = 16):
        self.connections = connections
        assert os.path.exists(ARIA2C), f"aria2c not found: {ARIA2C}"

    def download(self, url: str, out_dir: str, out_file: Optional[str] = None) -> str:
        """下载文件。返回保存路径。"""
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        out_file = out_file or url.split("/")[-1].split("?")[0]
        out_path = os.path.join(out_dir, out_file)

        cmd = [
            ARIA2C,
            f"-x{self.connections}",   # 每服务器连接数
            "-s16",                    # 分片数
            "-c",                      # 断点续传
            "-d", out_dir,
            "-o", out_file,
            url,
        ]
        print(f"[aria2] {url[:80]}... → {out_path}")
        subprocess.run(cmd, check=True)
        return out_path


class GopeedClient:
    """
    Gopeed REST API 客户端。
    适合：BT/磁力、大文件、需要 GUI 监控、需要 webhook 通知。
    端点：
        GET  /tasks?status=      列表
        POST /tasks              创建 {url, ...}
        DELETE /tasks            删 {id}
    """

    def __init__(self, base_url: str = GOPEED_API, token: Optional[str] = None):
        self.base = base_url
        self.token = token or os.environ.get("GOPEED_TOKEN", "")

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("X-Api-Token", self.token)
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
        if payload.get("code") != 0:
            raise RuntimeError(f"Gopeed error: {payload}")
        return payload.get("data", {})

    def is_alive(self) -> bool:
        """检查 gopeed-web 是否在跑。"""
        try:
            self._request("GET", "/config")
            return True
        except Exception:
            return False

    def add(self, url: str, out_dir: Optional[str] = None) -> str:
        """添加下载任务。返回 task id。"""
        opts = {}
        if out_dir:
            opts["path"] = out_dir
        body = {"url": url, "options": opts}
        result = self._request("POST", "/tasks", body)
        task_id = result.get("id", "")
        print(f"[gopeed] added task {task_id} for {url[:80]}")
        return task_id

    def list_tasks(self, status: str = "all") -> list:
        return self._request("GET", f"/tasks?status={status}")

    def wait_for(self, task_id: str, timeout_s: int = 300) -> dict:
        """轮询等到任务完成（done/failed）。"""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            tasks = self.list_tasks("running")
            for t in tasks:
                if t.get("id") == task_id:
                    status = t.get("status", "")
                    progress = t.get("progress", 0)
                    print(f"[gopeed] task {task_id}: {status} {progress:.0%}")
                    if status in ("done", "failed", "cancel"):
                        return t
                    break
            time.sleep(2)
        raise TimeoutError(f"task {task_id} not done in {timeout_s}s")

    def remove(self, task_id: str) -> None:
        self._request("DELETE", f"/tasks?id={task_id}")


# === 统一入口 ===

def download(url: str, out_dir: str, prefer: str = "auto", **kwargs) -> str:
    """
    统一下载入口。
    prefer:
      - "aria2": 走 aria2c
      - "gopeed": 走 Gopeed REST API
      - "auto": BT/磁力走 gopeed，其他走 aria2c
    """
    is_bt = url.startswith(("magnet:", "bt://")) or ".torrent" in url
    if prefer == "auto":
        prefer = "gopeed" if is_bt else "aria2"

    if prefer == "aria2":
        return Aria2Downloader().download(url, out_dir)
    elif prefer == "gopeed":
        client = GopeedClient()
        if not client.is_alive():
            print("[warn] Gopeed not running, fallback to aria2c")
            return Aria2Downloader().download(url, out_dir)
        task_id = client.add(url, out_dir=out_dir)
        result = client.wait_for(task_id, timeout_s=kwargs.get("timeout", 600))
        # 找到文件路径
        files = result.get("files", [])
        if files:
            return os.path.join(out_dir, files[0].get("name", ""))
        return out_dir
    else:
        raise ValueError(f"unknown prefer: {prefer}")


# === CLI ===

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: icarus_download.py <url> <out_dir> [aria2|gopeed|auto]")
        sys.exit(1)

    url, out_dir = sys.argv[1], sys.argv[2]
    prefer = sys.argv[3] if len(sys.argv) > 3 else "auto"
    result = download(url, out_dir, prefer=prefer)
    print(f"Done: {result}")
