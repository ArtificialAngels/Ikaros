"""
Configuration loader for Hermes.

Reads YAML config + .env, with sensible defaults.
Supports bash-style env var expansion: ${VAR} and ${VAR:-default}.
"""
from __future__ import annotations
import os
import re
import yaml
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field
from dotenv import load_dotenv


# ---- Config sections ----

class PersonaConfig(BaseModel):
    name: str = "hermes"
    version: str = "2.0.0"
    persona: str = "You are Hermes, a helpful AI assistant."


class RouterConfig(BaseModel):
    strategy: str = "fallback_chain"
    primary: str = "cloud"
    fallback_order: list[str] = Field(default_factory=lambda: ["cloud", "local"])
    on_status: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])
    on_timeout_ms: int = 8000
    on_network_error: bool = True
    max_retries: int = 1


class CloudProviderConfig(BaseModel):
    name: str
    provider: str
    base_url: str
    api_key: str = ""
    models: dict[str, str] = Field(default_factory=dict)
    enabled_if: str = "true"


class LocalProviderConfig(BaseModel):
    provider: str = "openai"
    base_url: str = "http://127.0.0.1:8080/v1"
    api_key: str = "not-needed"
    models: dict[str, str] = Field(default_factory=dict)
    enabled_if: str = "true"


class LLMConfig(BaseModel):
    router: RouterConfig = Field(default_factory=RouterConfig)
    cloud: list[CloudProviderConfig] = Field(default_factory=list)
    local: LocalProviderConfig = Field(default_factory=LocalProviderConfig)


class EmbeddingConfig(BaseModel):
    provider: str = "openai"
    base_url: str = "http://127.0.0.1:8080/v1"
    api_key: str = "not-needed"
    model: str = "nomic-embed"
    dimensions: int = 768


class MemoryConfig(BaseModel):
    backend: str = "simple"  # simple | chroma
    recency_decay: float = 0.95
    max_results: int = 5


class KnowledgeConfig(BaseModel):
    chunk_size: int = 500
    chunk_overlap: int = 50
    max_results: int = 5


class SkillsConfig(BaseModel):
    builtin: list[str] = Field(default_factory=lambda: ["time", "calc"])
    hot_reload: bool = False


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 7860
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


class NetworkConfig(BaseModel):
    """Network preferences: proxy + mirrors (inspired by ComfyUI-aki-v3)."""
    proxy_address: str = ""
    mirror_pypi: bool = False
    mirror_huggingface: bool = False
    mirror_git: bool = False
    pypi_mirror: str = "aliyun"
    hf_mirror: str = "hf-mirror"
    git_mirror: str = "gh-proxy"


class HermesConfig(BaseModel):
    agent: PersonaConfig = Field(default_factory=PersonaConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)

    # Runtime
    data_dir: str = "/data"
    log_level: str = "INFO"


# ---- Env var expansion (bash-style: ${VAR} and ${VAR:-default}) ----

_ENV_VAR_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z_0-9]*)(?::-([^}]*))?\}')


def _expand_env_str(s: str) -> str:
    """Expand ${VAR} and ${VAR:-default} in a string."""
    if not isinstance(s, str) or "$" not in s:
        return s

    def repl(m: re.Match) -> str:
        var, default = m.group(1), m.group(2)
        val = os.environ.get(var)
        if val is None or val == "":
            return default if default is not None else ""
        return val

    # First do our bash-style expansion
    s = _ENV_VAR_RE.sub(repl, s)
    # Then do os.path.expandvars for $VAR style (in case we missed any)
    s = os.path.expandvars(s)
    return s


def _expand_env(value: Any) -> Any:
    """Recursively expand env vars in strings (supports ${VAR:-default})."""
    if isinstance(value, str):
        return _expand_env_str(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(config_path: str | Path | None = None) -> HermesConfig:
    """
    Load config from YAML, with .env and sensible defaults.

    Search order:
        1. Explicit config_path argument
        2. HERMES_CONFIG env var
        3. ./config/hermes.yaml
        4. /data/config/hermes.yaml
        5. Built-in defaults
    """
    # Load .env from multiple candidates (cwd + hermes package parent + standard fallbacks)
    hermes_pkg = Path(__file__).resolve().parent  # .../hermes/
    candidates_env = [
        Path.cwd() / ".env",
        Path.cwd() / ".." / ".env",
        hermes_pkg.parent / ".env",  # project root .env (HERMES_ROOT)
        hermes_pkg / ".env",          # .../hermes/.env
        Path("/data/.env"),
    ]
    for env_path in candidates_env:
        if env_path.exists():
            load_dotenv(env_path, override=False)
            break

    # Find config file. Search order:
    #   1. Explicit config_path argument
    #   2. HERMES_CONFIG env var
    #   3. ./config/hermes.yaml (cwd-relative)
    #   4. ./hermes/config/hermes.yaml (cwd-relative)
    #   5. <HERMES_ROOT>/config/hermes.yaml (portable, no hardcoded drive letter)
    #   6. /data/config/hermes.yaml (legacy data-dir fallback)
    #   7. Built-in defaults
    candidates = []
    if config_path:
        candidates.append(Path(config_path))
    if os.getenv("HERMES_CONFIG"):
        candidates.append(Path(os.getenv("HERMES_CONFIG")))
    candidates.extend([
        Path("config/hermes.yaml"),
        Path("hermes/config/hermes.yaml"),
    ])
    # Portable fallback: derive from HERMES_ROOT env var (set by deps/hermes-env.bat).
    hermes_root_env = os.getenv("HERMES_ROOT")
    if hermes_root_env:
        candidates.append(Path(hermes_root_env) / "config" / "hermes.yaml")
    # Legacy data-dir fallback (back-compat; no hardcoded drive letter).
    candidates.append(Path("/data/config/hermes.yaml"))

    config_file = None
    for c in candidates:
        if c.exists():
            config_file = c
            break

    raw: dict = {}
    if config_file:
        with open(config_file, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    # Expand env vars
    raw = _expand_env(raw)

    # Apply runtime overrides from env
    if "HERMES_DATA_DIR" in os.environ:
        raw.setdefault("data_dir", os.environ["HERMES_DATA_DIR"])

    return HermesConfig(**raw)


def resolve_data_paths(cfg: HermesConfig, data_dir: str | Path | None = None) -> dict[str, Path]:
    """Resolve all data paths to absolute Paths."""
    base = Path(data_dir or cfg.data_dir)
    if not base.is_absolute():
        # Portable resolution: prefer HERMES_ROOT env var (set by deps/hermes-env.bat)
        # so the project works from any drive letter or directory name.
        hermes_root_env = os.getenv("HERMES_ROOT")
        if hermes_root_env:
            base = Path(hermes_root_env) / "data"
        else:
            # Final fallback: resolve against cwd. No hardcoded drive letters.
            base = base.resolve()

    base.mkdir(parents=True, exist_ok=True)

    return {
        "base": base,
        "memory": base / "memory",
        "knowledge": base / "knowledge",
        "skills": base / "skills",
        "logs": base / "logs",
        "cache": base / "cache",
        "models": base / "models",
        "config": base / "config",
    }
