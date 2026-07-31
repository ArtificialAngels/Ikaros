"""LLMManager — LLM model discovery, selection & persistence.

Stub implementation for ikaros-desktop-pet.
Scans local GGUF files + queries llama-server :8080 for loaded models.
Cloud model names come from config/models.yaml profiles.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("ikaros.llm_manager")

# GGUF scan roots (relative to HERMES_ROOT)
_GGUF_DIRS = [
    "data/models",
    "Ikaros-memory/models",
]

# Cloud model names (from config/models.yaml profiles — non-local models)
# These are the model aliases the cloud provider exposes.
_CLOUD_MODELS = {
    "MiniMax-M3", "DeepSeek-V3", "DeepSeek-R1",
    "gpt-4o", "gpt-4o-mini", "claude-3.5-sonnet",
}


@dataclass
class PreloadResult:
    """Result of a model preload operation."""
    success: bool = True
    detail: str = "ok"


class LLMManager:
    """Singleton manager for LLM model discovery and selection."""

    _instance: "LLMManager | None" = None

    @classmethod
    def instance(cls, hermes_root: Path | str) -> "LLMManager":
        if cls._instance is None:
            cls._instance = cls(hermes_root)
        return cls._instance

    def __init__(self, hermes_root: Path | str):
        self._root = Path(hermes_root)
        self._cache_file = self._root / "data" / "model_cache.json"
        self._last_model_file = self._root / "data" / "last_model.txt"
        self._available_models: list[str] = []
        self._cloud_model_set: set[str] = set(_CLOUD_MODELS)
        self._is_fetching = False

    # ─── Properties ────────────────────────────────

    @property
    def available_models(self) -> list[str]:
        return list(self._available_models)

    @property
    def cloud_model_set(self) -> set[str]:
        return set(self._cloud_model_set)

    @property
    def is_fetching(self) -> bool:
        return self._is_fetching

    # ─── Persistence ───────────────────────────────

    def get_current_model(self) -> str | None:
        """Read last-used model from disk."""
        try:
            if self._last_model_file.exists():
                return self._last_model_file.read_text(encoding="utf-8").strip() or None
        except Exception:
            pass
        return None

    def save_last_model(self, model_id: str) -> None:
        """Persist last-used model to disk."""
        try:
            self._last_model_file.parent.mkdir(parents=True, exist_ok=True)
            self._last_model_file.write_text(model_id, encoding="utf-8")
        except Exception as e:
            log.warning("save_last_model failed: %s", e)

    def load_cache(self) -> dict | None:
        """Load cached model list from disk. Returns {"models": [...], "cloud": [...]}."""
        try:
            if self._cache_file.exists():
                data = json.loads(self._cache_file.read_text(encoding="utf-8"))
                if "models" in data:
                    return data
        except Exception as e:
            log.warning("load_cache failed: %s", e)
        return None

    def _save_cache(self) -> None:
        """Write current model list to disk cache."""
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "models": self._available_models,
                "cloud": list(self._cloud_model_set),
            }
            self._cache_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            log.warning("_save_cache failed: %s", e)

    # ─── Model discovery ───────────────────────────

    def _scan_local_ggufs(self) -> list[str]:
        """Scan GGUF directories for model files."""
        models: list[str] = []
        for rel_dir in _GGUF_DIRS:
            d = self._root / rel_dir
            if not d.is_dir():
                continue
            for gguf in sorted(d.rglob("*.gguf")):
                # Use the GGUF filename stem as model name
                name = gguf.stem
                # Skip embedding models
                if "embed" in name.lower():
                    continue
                if name not in models:
                    models.append(name)
        return models

    def _query_server_models(self) -> list[str]:
        """Query llama-server :8080 for loaded models."""
        import urllib.request
        try:
            url = "http://127.0.0.1:8080/v1/models"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
            models: list[str] = []
            for m in data.get("data", []):
                mid = m.get("id", "")
                if mid:
                    models.append(mid)
            return models
        except Exception as e:
            log.debug("_query_server_models failed: %s", e)
            return []

    def fetch_models(self, blocking: bool = False) -> None:
        """Fetch available models (local GGUFs + server + cloud)."""
        if blocking:
            self._do_fetch()
        else:
            self._is_fetching = True
            t = threading.Thread(target=self._fetch_thread, daemon=True)
            t.start()

    def _fetch_thread(self) -> None:
        try:
            self._do_fetch()
        except Exception as e:
            log.error("fetch_models thread error: %s", e)
        finally:
            self._is_fetching = False

    def _do_fetch(self) -> None:
        """Core fetch logic: scan local + query server + merge cloud."""
        local = self._scan_local_ggufs()
        server = self._query_server_models()

        # Merge: server models first (they're loaded), then local
        seen: set[str] = set()
        merged: list[str] = []
        for m in server + local:
            if m not in seen:
                seen.add(m)
                merged.append(m)

        # Add cloud models
        for cm in sorted(self._cloud_model_set):
            if cm not in seen:
                seen.add(cm)
                merged.append(cm)

        self._available_models = merged
        self._save_cache()
        log.info("fetched %d models (%d local, %d server, %d cloud)",
                 len(merged), len(local), len(server), len(self._cloud_model_set))

    # ─── Model selection ───────────────────────────

    def select_model(self, model_id: str) -> None:
        """Select a model (persist to disk)."""
        self.save_last_model(model_id)
        log.info("selected model: %s", model_id)

    def preload_model_async(
        self,
        model_id: str,
        old_model: str | None = None,
        on_complete: Callable[[PreloadResult], Any] | None = None,
    ) -> None:
        """Pre-load a model asynchronously. Calls on_complete(result) when done.

        For local models, this would tell llama-server to switch models.
        For cloud models, this is a no-op (always succeeds immediately).
        """
        def _run():
            result = PreloadResult(success=True, detail="cloud model — no preload needed")
            if model_id not in self._cloud_model_set:
                # Local model — would need to restart llama-server with new model
                # For now, just report success (the watchdog manages :8080)
                result = PreloadResult(success=True, detail=f"local model {model_id}")
            if on_complete:
                on_complete(result)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
