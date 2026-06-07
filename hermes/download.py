r"""
Hermes Agent - Download manager with aria2c support (inspired by ComfyUI-aki-v3).

Uses aria2c for multi-threaded downloads when available, falling back to
urllib. Integrates with mirror.py for automatic mirror URL rewriting.

ComfyUI-aki-v3 bundles aria2c.exe in .launcher/ for model/plugin downloads.
We detect aria2c from:
  1. System PATH (if user installed aria2)
  2. runtime/aria2c.exe (if we bundle it)
  3. Fall back to urllib

Usage:
    from hermes.download import DownloadManager
    dm = DownloadManager()
    dm.download("https://huggingface.co/.../model.gguf", "data/models/")
"""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# Hermes root (parent of hermes/ package)
HERMES_ROOT = Path(__file__).resolve().parent.parent
RUNTIME = HERMES_ROOT / "runtime"


def find_aria2c() -> Optional[Path]:
    """Find aria2c executable (system PATH or bundled)."""
    # Check bundled first
    bundled = RUNTIME / "aria2c.exe"
    if bundled.exists():
        return bundled
    # Check system PATH
    system = shutil.which("aria2c") or shutil.which("aria2c.exe")
    if system:
        return Path(system)
    return None


class DownloadManager:
    """
    Download manager with automatic mirror + aria2c support.

    on_progress(percent, speed_str, eta_str) is called periodically
    during aria2c downloads.
    """

    def __init__(
        self,
        use_aria2: bool = True,
        on_progress: callable = None,
        mirror_enabled: bool = True,
    ):
        self._aria2: Optional[Path] = find_aria2c() if use_aria2 else None
        self._on_progress = on_progress
        self._mirror_enabled = mirror_enabled

    @property
    def has_aria2(self) -> bool:
        return self._aria2 is not None

    def _apply_mirror(self, url: str) -> str:
        """Rewrite URL through mirrors if enabled."""
        if not self._mirror_enabled:
            return url
        try:
            from hermes.mirror import mirror_url
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
        """
        Download a file. Uses aria2c if available, else urllib.

        Returns the path to the downloaded file.

        Args:
            url: Source URL (will be mirror-rewritten if mirror_enabled).
            dest_dir: Directory to save to.
            filename: Save as this name (auto from URL if None).
            connections: Number of concurrent connections (aria2c only).
            timeout: Total timeout in seconds (0 = no limit).
        """
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

    def _download_aria2(
        self, url: str, dest: Path, connections: int, timeout: int
    ) -> Path:
        """Download using aria2c with multi-threading."""
        cmd = [
            str(self._aria2),
            "--max-connection-per-server=" + str(connections),
            "--split=" + str(connections),
            "--min-split-size=1M",
            "--console-log-level=error",
            "--summary-interval=0",
            "--dir=" + str(dest.parent),
            "--out=" + dest.name,
            url,
        ]
        if timeout > 0:
            cmd.insert(1, "--timeout=" + str(timeout))

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        # Parse aria2c output for progress
        last_report = 0
        for line in proc.stdout:
            line = line.strip()
            # aria2c progress lines look like:
            # [#SIZE 192KiB/2.3MiB(8%) CN:1 SPD:1.2MiB ETA:2s]
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
                            eta_part = line[line.index("ETA:") : ]
                            eta_str = eta_part.split("]")[0]
                        self._on_progress(
                            int(pct_str), spd_str, eta_str
                        )
                    except Exception:
                        pass

        proc.wait()

        if proc.returncode != 0:
            raise RuntimeError(f"aria2c exited with code {proc.returncode}")

        if not dest.exists():
            raise FileNotFoundError(f"aria2c completed but file not found: {dest}")

        return dest

    def _download_urllib(self, url: str, dest: Path, timeout: int) -> Path:
        """Fallback: download using urllib (single-thread)."""
        import urllib.request

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
            # Clean up partial download
            if dest.exists():
                dest.unlink()
            raise RuntimeError(f"Download failed: {e}")

        return dest

    def download_many(
        self,
        urls: list[str],
        dest_dir: str | Path,
        connections: int = 8,
    ) -> list[Path]:
        """
        Download multiple files sequentially. Returns list of paths.
        (aria2c could do true batch, but sequential is simpler and safer.)
        """
        results = []
        for url in urls:
            result = self.download(url, dest_dir, connections=connections)
            results.append(result)
        return results


# ---- Convenience ----

def download_file(
    url: str,
    dest_dir: str | Path,
    filename: Optional[str] = None,
    use_aria2: bool = True,
    mirror: bool = True,
    on_progress: callable = None,
) -> Path:
    """Quick one-shot download with mirror + aria2 support."""
    dm = DownloadManager(
        use_aria2=use_aria2,
        on_progress=on_progress,
        mirror_enabled=mirror,
    )
    return dm.download(url, dest_dir, filename)
