r"""
Hermes Agent - Mirror / proxy configuration.

Inspired by ComfyUI-aki-v3.

Provides URL mirroring for PyPI, HuggingFace, Git remotes to accelerate
downloads for users on slow networks (especially in mainland China).

Mirror sources:
- PyPI:     https://mirrors.aliyun.com/pypi/simple/
            https://pypi.tuna.tsinghua.edu.cn/simple/
- HuggingFace: https://hf-mirror.com
- Git:      https://gh-proxy.com/ (GitHub proxy)

Usage:
    from modules.model_manager.mirror import MirrorConfig, get_mirror_config
    cfg = MirrorConfig.from_yaml(hermes_config)
    hf_url = cfg.mirror_url("https://huggingface.co/Qwen/Qwen2.5-3B/resolve/main/ggml-model-q4_k_m.gguf")
    # -> "https://hf-mirror.com/Qwen/Qwen2.5-3B/resolve/main/ggml-model-q4_k_m.gguf"
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse


# ---- Mirror presets ----

MIRROR_PRESETS = {
    "pypi": {
        "aliyun": "https://mirrors.aliyun.com/pypi/simple/",
        "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple/",
        "ustc": "https://pypi.mirrors.ustc.edu.cn/simple/",
    },
    "huggingface": {
        "hf-mirror": "https://hf-mirror.com",
    },
    "git": {
        "gh-proxy": "https://gh-proxy.com/",
        "gitclone": "https://gitclone.com/github.com/",
        "fastgit": "https://hub.fastgit.xyz/",
    },
}


@dataclass
class MirrorConfig:
    """Network preference configuration, akin to ComfyUI-aki-v3's preference.json."""

    # Proxy
    proxy_address: str = ""

    # Mirror toggles
    mirror_pypi: bool = False
    mirror_huggingface: bool = False
    mirror_git: bool = False

    # Mirror site selection (index into MIRROR_PRESETS)
    pypi_mirror: str = "aliyun"       # "aliyun" | "tsinghua" | "ustc"
    hf_mirror: str = "hf-mirror"     # "hf-mirror"
    git_mirror: str = "gh-proxy"     # "gh-proxy" | "gitclone" | "fastgit"

    @classmethod
    def from_yaml(cls, config: dict) -> "MirrorConfig":
        """Parse from hermes.yaml 'network' section."""
        net = config.get("network", {})
        return cls(
            proxy_address=net.get("proxy_address", ""),
            mirror_pypi=net.get("mirror_pypi", False),
            mirror_huggingface=net.get("mirror_huggingface", False),
            mirror_git=net.get("mirror_git", False),
            pypi_mirror=net.get("pypi_mirror", "aliyun"),
            hf_mirror=net.get("hf_mirror", "hf-mirror"),
            git_mirror=net.get("git_mirror", "gh-proxy"),
        )

    @classmethod
    def from_env(cls) -> "MirrorConfig":
        """Parse from environment variables (HERMES_MIRROR_*)."""
        return cls(
            proxy_address=os.environ.get("HERMES_PROXY", os.environ.get("HTTP_PROXY", "")),
            mirror_pypi=os.environ.get("HERMES_MIRROR_PYPI", "").lower() == "true",
            mirror_huggingface=os.environ.get("HERMES_MIRROR_HF", "").lower() == "true",
            mirror_git=os.environ.get("HERMES_MIRROR_GIT", "").lower() == "true",
            pypi_mirror=os.environ.get("HERMES_PYPI_MIRROR", "aliyun"),
            hf_mirror=os.environ.get("HERMES_HF_MIRROR", "hf-mirror"),
            git_mirror=os.environ.get("HERMES_GIT_MIRROR", "gh-proxy"),
        )

    def get_pypi_index_url(self) -> str:
        """Get the pip index URL (for pip install --index-url)."""
        if self.mirror_pypi:
            preset = MIRROR_PRESETS["pypi"]
            return preset.get(self.pypi_mirror, preset["aliyun"])
        return "https://pypi.org/simple/"

    def mirror_url(self, url: str) -> str:
        """Apply mirror rewriting to a URL."""
        parsed = urlparse(url)
        host = parsed.hostname or ""

        # HuggingFace -> hf-mirror.com
        if self.mirror_huggingface and (
            "huggingface.co" in host or "hf.co" in host
        ):
            preset = MIRROR_PRESETS["huggingface"]
            mirror_base = preset.get(self.hf_mirror, preset["hf-mirror"])
            return url.replace("https://huggingface.co", mirror_base).replace(
                "https://hf.co", mirror_base
            )

        # GitHub -> proxy
        if self.mirror_git and "github.com" in host:
            preset = MIRROR_PRESETS["git"]
            mirror_base = preset.get(self.git_mirror, preset["gh-proxy"])
            # gh-proxy format: https://gh-proxy.com/https://github.com/...
            if self.git_mirror in ("gh-proxy",):
                return f"{mirror_base.rstrip('/')}/{url}"
            # gitclone/fastgit: replace host
            return url.replace("https://github.com", mirror_base.rstrip("/"))

        return url


# ---- Convenience singleton ----

_default_config: Optional[MirrorConfig] = None


def get_mirror_config() -> MirrorConfig:
    """Get the global mirror config (lazy init from env)."""
    global _default_config
    if _default_config is None:
        _default_config = MirrorConfig.from_env()
    return _default_config


def init_mirror_config(config: dict) -> MirrorConfig:
    """Initialize mirror config from hermes.yaml. Also sets env vars."""
    global _default_config
    _default_config = MirrorConfig.from_yaml(config)
    net = config.get("network", {})
    if net.get("proxy_address"):
        os.environ["HERMES_PROXY"] = net["proxy_address"]
        os.environ["HTTP_PROXY"] = net["proxy_address"]
        os.environ["HTTPS_PROXY"] = net["proxy_address"]
    return _default_config


def mirror_url(url: str) -> str:
    """Convenience: apply mirror rewriting with global config."""
    cfg = get_mirror_config()
    return cfg.mirror_url(url)
