r"""
LLM Manager — unified model discovery, selection, persistence & switching.

Extracted from ``bin/ikaros-desktop-pet/main.py`` (2026-06-30).
Provides a shared ``LLMManager`` class that any component can import:

    from modules.model_manager.llm_manager import LLMManager

    mgr = LLMManager(hermes_root)
    result = mgr.fetch_models()          # discover local + cloud
    ok, err = mgr.select_model("Phi-4")  # evict old → load new → persist
    info = mgr.get_model_info()          # GGUF metadata enrichment

Enhancements over the original main.py inline code:
  * GGUF metadata enrichment (size, quant, arch) via existing gguf.py
  * Thread-safe singleton (one fetch at a time across all consumers)
  * Callback hooks for GUI notifications (Live2D tip, tray update, …)
  * Model details cache (GGUF metadata keyed by model name)
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger("ikaros.llm_manager")


# ─── Data classes ───

@dataclass
class FetchResult:
    """Result of a model fetch operation."""
    local_models: list[str] = field(default_factory=list)
    cloud_models: list[str] = field(default_factory=list)

    @property
    def all_models(self) -> list[str]:
        return self.local_models + self.cloud_models


@dataclass
class PreloadResult:
    """Result of an async model preload."""
    success: bool
    model_id: str
    detail: str = ""


# ─── Cloud model registry ───

CLOUD_MODELS: dict[str, list[str]] = {
    "minimax-cn": ["MiniMax-M3", "MiniMax-M1", "abab6.5s-chat"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "openai": ["gpt-4o", "gpt-4o-mini"],
    "openrouter": ["openrouter/auto"],
}

# env var → provider name
CLOUD_KEY_MAP: dict[str, str] = {
    "MINIMAX_CN_API_KEY": "minimax-cn",
    "MINIMAX_API_KEY": "minimax-cn",
    "DEEPSEEK_API_KEY": "deepseek",
    "OPENAI_API_KEY": "openai",
    "OPENROUTER_API_KEY": "openrouter",
}


# ─── LLM Manager ───

class LLMManager:
    """Unified LLM model management.

    Thread-safe singleton — call ``LLMManager.instance(hermes_root)`` to get
    the shared instance (created on first call).

    Parameters
    ----------
    hermes_root : Path
        Project root (e.g. ``E:\\Ikaros``).
    cache_ttl : float
        Disk-cache time-to-live in seconds (default 12 h).
    """

    _instance: LLMManager | None = None
    _lock = threading.Lock()

    # ── Singleton ──

    @classmethod
    def instance(cls, hermes_root: Path | None = None) -> LLMManager:
        """Get (or create) the global LLMManager singleton."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None and hermes_root is not None:
                    cls._instance = cls(hermes_root)
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    # ── Init ──

    def __init__(self, hermes_root: Path, cache_ttl: float = 43200):
        self._hermes_root = Path(hermes_root)
        self._cache_ttl = cache_ttl

        # Paths
        self._models_dir = self._hermes_root / "data" / "models"
        self._log_dir = self._hermes_root / "data" / "logs"
        self._cache_path = self._hermes_root / "bin" / "ikaros-desktop-pet" / "llm_model_cache.json"
        self._persist_path = self._hermes_root / "bin" / "ikaros-desktop-pet" / "last_llm_model.json"

        # State
        self._available_models: list[str] = []
        self._cloud_model_set: set[str] = set()
        self._fetching = False
        self._fetch_lock = threading.Lock()

        # GGUF metadata cache (populated lazily by get_model_details)
        self._gguf_details: dict[str, dict] = {}

        # Optional callbacks — set by GUI consumers
        self.on_model_changed: Callable[[str], None] | None = None

    # ── Properties ──

    @property
    def hermes_root(self) -> Path:
        return self._hermes_root

    @property
    def available_models(self) -> list[str]:
        return list(self._available_models)

    @property
    def cloud_model_set(self) -> set[str]:
        return set(self._cloud_model_set)

    @property
    def is_fetching(self) -> bool:
        return self._fetching

    # ══════════════════════════════════════════════════════════════
    #  1. MODEL DISCOVERY
    # ══════════════════════════════════════════════════════════════

    def fetch_models(self, *, blocking: bool = True) -> FetchResult:
        """Discover available models (local + cloud).

        Three-layer local discovery:
          1. llama-server :8080 ``/v1/models``
          2. bridge :7860 ``/v1/models``
          3. Local GGUF file scan (fallback)

        Cloud models detected from API key env vars.

        Parameters
        ----------
        blocking : bool
            If True (default), runs synchronously and returns the result.
            If False, spawns a daemon thread and returns immediately
            with whatever was cached.
        """
        if blocking:
            return self._do_fetch()

        if self._fetching:
            return FetchResult(self._available_models, list(self._cloud_model_set))

        self._fetching = True

        def worker():
            try:
                self._do_fetch()
            finally:
                self._fetching = False

        threading.Thread(target=worker, daemon=True).start()
        return FetchResult(self._available_models, list(self._cloud_model_set))

    def _do_fetch(self) -> FetchResult:
        """Synchronous model discovery."""
        with self._fetch_lock:
            local_models: list[str] = []
            cloud_models: list[str] = []

            # 1. Local models — try API first, fall back to GGUF scan
            api_success = False
            for port in (8080, 7860):
                try:
                    url = f"http://127.0.0.1:{port}/v1/models"
                    req = urllib.request.Request(url, method="GET")
                    with urllib.request.urlopen(req, timeout=3.0) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    raw_ids = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
                    seen: set[str] = set()
                    for mid in raw_ids:
                        if "mmproj" in mid.lower():
                            continue
                        if re.search(r'-\d{5}-of-\d{5}', mid):
                            continue
                        base = mid.removesuffix(".gguf").removesuffix(".GGUF")
                        if base not in seen:
                            seen.add(base)
                            local_models.append(base)
                    api_success = True
                    break
                except Exception as exc:
                    log.debug("port %d models failed: %s", port, exc)
                    continue

            # 1b. Fallback: scan local GGUF files
            if not api_success:
                gguf_models = self.scan_local_gguf()
                if gguf_models:
                    local_models = gguf_models
                    log.info("fallback: scanned %d local GGUF files", len(gguf_models))

            # 2. Cloud models — detect API keys
            configured = self.detect_cloud_providers()
            for provider in configured:
                cloud_models.extend(CLOUD_MODELS.get(provider, []))

            # 3. Update state
            self._available_models = local_models + cloud_models
            self._cloud_model_set = set(cloud_models)

            # 4. Save disk cache
            self._save_cache(self._available_models, cloud_models)

            log.info("models fetched: %d local + %d cloud", len(local_models), len(cloud_models))
            return FetchResult(local_models, cloud_models)

    # ══════════════════════════════════════════════════════════════
    #  2. MODEL SELECTION & SWITCHING
    # ══════════════════════════════════════════════════════════════

    def select_model(self, model_id: str) -> None:
        """Persist model selection and sync to llama-server router.

        Updates ``llm-engine-last-launch.json`` so the next llama-server
        restart picks this model as preferred.
        """
        old = self.get_current_model()
        self.save_last_model(model_id)
        log.info("LLM model: %s → %s", old, model_id)
        if self.on_model_changed:
            try:
                self.on_model_changed(model_id)
            except Exception as exc:
                log.warning("on_model_changed callback failed: %s", exc)

    def preload_model(self, model_id: str, *, old_model: str | None = None) -> PreloadResult:
        """Pre-load model on llama-server (synchronous).

        1. Evict old model (if specified) via ``/v1/models/evict``
        2. Load new model via bridge :7860 or llama-server :8080

        Returns PreloadResult with success status and detail.
        """
        # Step 1: evict old model
        if old_model:
            try:
                evict_url = "http://127.0.0.1:7860/v1/models/evict"
                evict_body = json.dumps({"model": old_model}).encode("utf-8")
                evict_req = urllib.request.Request(
                    evict_url, data=evict_body,
                    headers={"Content-Type": "application/json", "X-Ikaros-Client": "pet"},
                    method="POST",
                )
                with urllib.request.urlopen(evict_req, timeout=15.0) as evict_resp:
                    log.info("model evict %s: %s", old_model, evict_resp.read().decode()[:200])
            except Exception as exc:
                log.debug("model evict %s skipped (non-fatal): %s", old_model, exc)

        # Step 2: load new model
        attempts = [
            (7860, "/v1/models/load"),
            (8080, "/models/load"),
        ]
        for port, path in attempts:
            try:
                url = f"http://127.0.0.1:{port}{path}"
                body = json.dumps({"model": model_id}).encode("utf-8")
                req = urllib.request.Request(
                    url, data=body,
                    headers={"Content-Type": "application/json", "X-Ikaros-Client": "pet"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30.0) as resp:
                    result = resp.read().decode("utf-8")
                    log.info("model pre-load %s on :%d%s: %s", model_id, port, path, result[:200])
                return PreloadResult(success=True, model_id=model_id, detail=f":{port}{path}")
            except urllib.error.HTTPError as exc:
                body_text = exc.read().decode("utf-8", errors="replace")
                if exc.code == 400:
                    log.info("model %s already loaded: %s", model_id, body_text[:100])
                    return PreloadResult(success=True, model_id=model_id, detail="(already loaded)")
                log.debug("pre-load :%d%s failed HTTP %d: %s", port, path, exc.code, body_text[:100])
            except Exception as exc:
                log.debug("pre-load :%d%s failed: %s", port, path, exc)

        return PreloadResult(success=False, model_id=model_id, detail="all endpoints failed")

    def preload_model_async(self, model_id: str, *, old_model: str | None = None,
                            on_complete: Callable[[PreloadResult], None] | None = None) -> None:
        """Pre-load model in background thread.

        Calls ``on_complete(result)`` on the worker thread when done.
        GUI consumers should use ``QTimer.singleShot(0, ...)`` to marshal
        back to the main thread.
        """
        def worker():
            result = self.preload_model(model_id, old_model=old_model)
            if on_complete:
                try:
                    on_complete(result)
                except Exception as exc:
                    log.warning("preload on_complete callback failed: %s", exc)

        threading.Thread(target=worker, daemon=True).start()

    # ══════════════════════════════════════════════════════════════
    #  3. PERSISTENCE
    # ══════════════════════════════════════════════════════════════

    def get_current_model(self) -> str | None:
        """Load the last-persisted model ID. Returns None if missing."""
        try:
            if not self._persist_path.exists():
                return None
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            return data.get("model")
        except Exception:
            return None

    def save_last_model(self, model_id: str) -> None:
        """Persist model selection + sync to llama-server router config."""
        # 1. Persist to last_llm_model.json
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps({"model": model_id, "ts": time.time()}),
                encoding="utf-8",
            )
            log.info("persisted last LLM model: %s", model_id)
        except Exception as exc:
            log.warning("save last LLM model FAILED: %s", exc)

        # 2. Sync to llm-engine-last-launch.json (router preferred model)
        try:
            llm_launch_path = self._log_dir / "llm-engine-last-launch.json"
            if llm_launch_path.exists():
                info = json.loads(llm_launch_path.read_text(encoding="utf-8"))
            else:
                info = {}
            info["preferred_model"] = model_id
            llm_launch_path.parent.mkdir(parents=True, exist_ok=True)
            llm_launch_path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            log.debug("sync preferred_model to llm-engine-last-launch.json FAILED: %s", exc)

    # ══════════════════════════════════════════════════════════════
    #  4. CLOUD PROVIDER DETECTION
    # ══════════════════════════════════════════════════════════════

    def detect_cloud_providers(self) -> list[str]:
        """Detect which cloud providers have API keys configured."""
        configured: set[str] = set()

        # Check os.environ
        for env_var, provider in CLOUD_KEY_MAP.items():
            key = os.environ.get(env_var, "").strip()
            if key and not key.startswith("$"):
                configured.add(provider)

        # Also check HERMES_HOME/.env directly (in case not loaded into env)
        try:
            hermes_env = self._hermes_root / "data" / "hermes-agent" / ".env"
            if hermes_env.exists():
                for line in hermes_env.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"").strip()
                    if v and not v.startswith("$"):
                        for env_var, provider in CLOUD_KEY_MAP.items():
                            if k == env_var:
                                configured.add(provider)
        except Exception:
            pass

        return list(configured)

    # ══════════════════════════════════════════════════════════════
    #  5. GGUF ENRICHMENT (enhancement over original)
    # ══════════════════════════════════════════════════════════════

    def scan_local_gguf(self) -> list[str]:
        """Scan data/models/*.gguf → list of model names (no suffix)."""
        return scan_local_gguf_files(self._models_dir)

    def get_model_details(self, model_name: str) -> dict:
        """Get enriched model info: GGUF metadata + cloud/local tag.

        Returns dict with keys: name, is_cloud, size_gb, arch, quant, ctx_len.
        """
        if model_name in self._cloud_model_set:
            return {"name": model_name, "is_cloud": True, "size_gb": None,
                    "arch": None, "quant": None, "ctx_len": None}

        # Check GGUF cache
        if model_name in self._gguf_details:
            detail = dict(self._gguf_details[model_name])
            detail["is_cloud"] = False
            return detail

        # Parse from GGUF file
        try:
            from modules.model_manager.gguf import parse_gguf_meta
            candidates = list(self._models_dir.glob(f"{model_name}*.gguf"))
            for c in candidates:
                if "mmproj" in c.name.lower():
                    continue
                if re.search(r'-\d{5}-of-\d{5}', c.name):
                    continue
                meta = parse_gguf_meta(c)
                detail = {
                    "name": model_name,
                    "is_cloud": False,
                    "size_gb": meta.get("size_gb"),
                    "arch": meta.get("arch"),
                    "quant": meta.get("quant"),
                    "ctx_len": meta.get("ctx_len"),
                }
                self._gguf_details[model_name] = detail
                return detail
        except Exception as exc:
            log.debug("get_model_details(%s) failed: %s", model_name, exc)

        return {"name": model_name, "is_cloud": False, "size_gb": None,
                "arch": None, "quant": None, "ctx_len": None}

    def get_all_model_details(self) -> list[dict]:
        """Get enriched details for all available models."""
        return [self.get_model_details(m) for m in self._available_models]

    # ══════════════════════════════════════════════════════════════
    #  6. DISK CACHE
    # ══════════════════════════════════════════════════════════════

    def load_cache(self) -> dict | None:
        """Load cached model list from disk. Returns None if missing or stale."""
        try:
            if not self._cache_path.exists():
                return None
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            ts = data.get("ts", 0)
            if time.time() - ts > self._cache_ttl:
                return None
            if not data.get("models"):
                return None
            return data
        except Exception:
            return None

    def _save_cache(self, models: list[str], cloud_models: list[str]) -> None:
        """Save model list to disk for fast startup."""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps({"models": models, "cloud": cloud_models, "ts": time.time()}),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("model cache save FAILED: %s", exc)


# ─── Module-level helpers (stateless, used by LLMManager) ───


def scan_local_gguf_files(models_dir: Path) -> list[str]:
    """Scan models_dir/*.gguf and return list of model names (no .gguf suffix).

    Excludes mmproj files and split parts (xxxxx-of-xxxxx).
    """
    if not models_dir.exists():
        return []
    seen: set[str] = set()
    result: list[str] = []
    try:
        for f in sorted(models_dir.glob("*.gguf")):
            name = f.stem
            if "mmproj" in name.lower():
                continue
            if "-of-" in name and re.search(r'\d{5}-of-\d{5}', name):
                continue
            if name not in seen:
                seen.add(name)
                result.append(name)
    except Exception as exc:
        log.warning("GGUF scan failed: %s", exc)
    return result
