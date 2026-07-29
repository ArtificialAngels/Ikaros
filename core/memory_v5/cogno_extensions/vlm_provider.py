# 详细说明见 docs/scripts/core/memory_v5/cogno_extensions/vlm_provider.md
from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("ikaros.cogno.vlm")

# 镜像 MewCo-AI:vlm.py:6-9 三个 image_format 常量
IMAGE_FORMAT_CAM = "jpg"
IMAGE_FORMAT_SCREEN = "jpg"
IMAGE_FORMAT_PHOTO = "png"


@dataclass
class VLMConfig:
    """单 provider 配置 (镜像 MewCo-AI 多 global 变量)."""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    timeout: float = 8.0
    gpu_mode: bool = False
    extra: dict = field(default_factory=dict)


# Provider handler 函数签名: (question, base64_img, img_format, cfg) -> str
VLMHandler = Callable[[str, str, str, VLMConfig], str]


# provider registry -- 镜像 MewCo-AI:llm.py vlm_mapping dict 模式
_PROVIDERS: dict[str, VLMHandler] = {}


def register_provider(name: str, handler: VLMHandler) -> None:
    """注册 VLM provider, handler 接受 (question, base64_img, img_format, cfg)."""
    _PROVIDERS[name] = handler
    logger.info("vlm: registered provider %s", name)


def list_providers() -> list[str]:
    return sorted(_PROVIDERS.keys())


def _echo_handler(question: str, base64_img: str, img_format: str,
                  cfg: VLMConfig) -> str:
    """默认 fallback handler (没真 provider 时用).

    镜像 MewCo-AI:agent.py 严格只输出无 schema 风格的"日常闲聊"
    fallback 行为, 不假装调用 cloud API.
    """
    return f"[vlm-no-provider:{cfg.model or 'unknown'}] (size={len(base64_img)})"


# 默认 register 一个 echo, 这样 router.query() 永不抛
register_provider("echo", _echo_handler)


def encode_image_b64(image_bytes: bytes, image_format: str = "jpg") -> str:
    """B64 编码 (镜像 MewCo-AI:vlm.py:11-13 encode_image, 但收 bytes)."""
    return base64.b64encode(image_bytes).decode("utf-8")


class VLMRouter:
    """VLM 主路由 (镜像 MewCo-AI:vlm.py vlm_mapping dict 模式)."""

    def __init__(self, default_provider: str = "echo",
                 default_config: VLMConfig | None = None):
        self._default_provider = default_provider
        self._configs: dict[str, VLMConfig] = {}
        if default_config:
            self._configs[default_provider] = default_config
        # 镜像 Live2DPet:vlm-extractor.js LRU + maxSituations=10 + retentionDays=7
        self._capture_count = 0
        self._last_capture_size = 0
        self._base_interval_ms = 15000  # 15s, Live2DPet 默认

    def set_config(self, provider: str, cfg: VLMConfig) -> None:
        self._configs[provider] = cfg

    def capture_screen(self) -> bytes | None:
        """截屏. 用 PIL (ImageGrab 已含) 或 mss, 不引 cv2.

        Return: PNG bytes, or None if no display.
        """
        try:
            # PIL.ImageGrab 真跨平台, 不需要 mss (轻)
            from PIL import ImageGrab
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            self._capture_count += 1
            self._last_capture_size = buf.tell()
            return buf.getvalue()
        except Exception as e:
            logger.debug("vlm: screen capture failed: %s", e)
            return None

    def capture_cam(self, device_index: int = 0) -> bytes | None:
        """摄像头帧 (JPG). 需要 cv2 / mediapipe, 留空 (缺依赖)."""
        logger.debug("vlm: cam capture not implemented (cv2 dep)")
        return None

    def query(self, image_bytes: bytes | None, question: str,
              provider: str | None = None) -> str:
        """Query VLM with image + question. Failure-silent (cogno 5D 要求).

        Returns "string description" or "[未知]" if all fail.
        """
        if image_bytes is None:
            return "[无图]"
        if not _PROVIDERS:
            return "[未知-vlm-no-providers]"
        provider = provider or self._default_provider
        handler = _PROVIDERS.get(provider)
        if handler is None:
            handler = _PROVIDERS.get("echo")
            if handler is None:
                return "[未知-vlm-no-handler]"
        cfg = self._configs.get(provider, VLMConfig())
        try:
            b64 = encode_image_b64(image_bytes, IMAGE_FORMAT_SCREEN)
            return handler(question, b64, IMAGE_FORMAT_SCREEN, cfg)
        except Exception as e:
            logger.debug("vlm: query failed: %s", e)
            return "[未知]"

    def stats(self) -> dict:
        """镜像 Live2DPet:vlm-extractor.js stats() 输出."""
        return {
            "providers": list_providers(),
            "default_provider": self._default_provider,
            "capture_count": self._capture_count,
            "last_capture_size_bytes": self._last_capture_size,
            "base_interval_ms": self._base_interval_ms,
        }


# 周期截屏器 (镜像 Live2DPet:vlm-extractor.js 5s 周期 + kf 队列)
class VLMPeriodicCapture:
    """Lives between Live2DPet 独立 captureTimer + cogno 5D 第 6 维.

    5s 周期 + last-N 队列 (默认 3 个 keyframe, 镜像 _kfSelectedMax=3)
    + 30s 短程池 (镜像 maxSituations=10 / retentionDays=7)
    """

    def __init__(self, router: VLMRouter, interval_ms: int = 5000,
                 kf_max: int = 3):
        self._router = router
        self._interval_ms = interval_ms
        self._kf_max = kf_max
        self._kf_ring: list[bytes] = []  # 镜像 _kfSelected
        self._timer_active = False

    def tick_once(self) -> bytes | None:
        """Poll 一次: 截屏 -> 入 ring buffer."""
        img = self._router.capture_screen()
        if img is None:
            return None
        self._kf_ring.append(img)
        if len(self._kf_ring) > self._kf_max:
            self._kf_ring.pop(0)
        return img

    def latest(self) -> bytes | None:
        return self._kf_ring[-1] if self._kf_ring else None

    def history(self) -> list[bytes]:
        return list(self._kf_ring)

    def clear(self) -> None:
        self._kf_ring.clear()

    @property
    def interval_ms(self) -> int:
        return self._interval_ms

    def configure_interval(self, ms: int) -> None:
        """镜像 Live2DPet:configure baseIntervalMs."""
        self._interval_ms = ms
