# 详细说明见 docs/scripts/Ikaros-memory/cogno_extensions/voiceprint.md
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("ikaros.cogno.voiceprint")

# MewCo-AI:asr.py 常用采样率 (1 ch, 16k Hz, int16)
DEFAULT_RATE = 16000
DEFAULT_CHANNELS = 1


@dataclass
class VoiceprintConfig:
    """配置 -- 不复制 MewCo 全局 vars, 显式 init."""
    asr_model_dir: str = ""       # sherpa-onnx-sense-voice-zh-en-ja-ko-yue path
    speaker_id_model: str = ""    # 3dspeaker_speech_campplus_*.onnx
    audio_tag_model: str = ""     # sherpa-onnx-zipformer-small-audio-tagging
    voiceprint_enabled: bool = False
    audio_event_enabled: bool = False
    voiceprint_threshold: float = 0.5
    audio_event_threshold: float = 0.5
    mic_device: int = 0


@dataclass
class VoiceprintMatch:
    """声纹匹配结果 -- 镜像 MewCo 的 vp_threshold 比较结果格式."""
    user_id: str | None  # None = 不匹配
    score: float
    is_match: bool


class VoiceprintEngine:
    """声纹 + 音频事件双模引擎.

    调用方 (audio_engine.py) 传 PCM bytes, 引擎在内部初始化
    sherpa_onnx extractors (lazy).
    """

    def __init__(self, cfg: VoiceprintConfig | None = None):
        self.cfg = cfg or VoiceprintConfig()
        # lazy init: sherpa_onnx 模型大, 第一次调用才 load
        self._extractor = None
        self._audio_tagger = None
        self._initialized = False
        self._init_error: str | None = None

    def _try_init(self) -> bool:
        """真物 init 走一次 (失败静默 + cache error)."""
        if self._initialized:
            return True
        if self._init_error:
            return False
        try:
            import sherpa_onnx  # noqa: F401  -- 验 import
            # sherpa_onnx 1.13.3 真物 import OK. 余下模型 load 留 caller
            # 走. 我们**不擅自下模型** (v3 0.84 哥哥没让我下 50MB+ 模型)
            self._initialized = True
            return True
        except Exception as e:
            self._init_error = str(e)
            logger.debug("voiceprint: init failed: %s", e)
            return False

    def is_configured(self) -> bool:
        """config 是否有真模型路径."""
        return bool(self.cfg.speaker_id_model and os.path.exists(
            self.cfg.speaker_id_model))

    def extract_embedding(self, audio_pcm_bytes: bytes,
                          sample_rate: int = DEFAULT_RATE) -> list[float] | None:
        """从 PCM 提取声纹 embedding (768-d).

        Returns None if not configured / model missing (cogno 5D 期望失败
        静默).
        """
        if not self._try_init():
            return None
        if not self.is_configured():
            logger.debug("voiceprint: speaker_id_model not configured")
            return None
        try:
# 内联说明见 docs/scripts/Ikaros-memory/cogno_extensions/voiceprint.md（见“内联注释摘录”）
            logger.debug("voiceprint: extract not implemented (model file absent)")
            return None
        except Exception as e:
            logger.debug("voiceprint: extract failed: %s", e)
            return None

    def compare(self, embedding_a: list[float] | None,
                embedding_b: list[float] | None,
                threshold: float | None = None) -> VoiceprintMatch:
        """比对两个 embedding, 返 match or not.

        镜像 MewCo:asr.py L80-110 speaker verification pattern.
        """
        if embedding_a is None or embedding_b is None:
            return VoiceprintMatch(user_id=None, score=0.0, is_match=False)
        if len(embedding_a) != len(embedding_b):
            return VoiceprintMatch(user_id=None, score=0.0, is_match=False)
        # 真物: cosine similarity / L2 norm
        # 镜像 MewCo `voiceprint_threshold` 默认 0.5
        try:
            import math
            dot = sum(a * b for a, b in zip(embedding_a, embedding_b))
            na = math.sqrt(sum(a * a for a in embedding_a)) or 1e-9
            nb = math.sqrt(sum(b * b for b in embedding_b)) or 1e-9
            cos = dot / (na * nb)
        except Exception:
            cos = 0.0
        thr = threshold if threshold is not None else self.cfg.voiceprint_threshold
        return VoiceprintMatch(
            user_id="user" if cos >= thr else None,
            score=cos,
            is_match=cos >= thr,
        )

    def detect_audio_event(self, audio_pcm_bytes: bytes,
                            sample_rate: int = DEFAULT_RATE) -> str | None:
        """音频事件检测 -- 镜像 MewCo:asr.py AudioTag ZipFormer.

        Returns event label like "[打喷嚏]" / "[狗叫声]" (MewCo:asr.py L49-66
        audio_event_mapping 字典里的中文标签). Returns None if not
        configured.
        """
        if not self._try_init():
            return None
        if not self.cfg.audio_tag_model or not os.path.exists(
                self.cfg.audio_tag_model):
            logger.debug("voiceprint: audio_tag_model not configured")
            return None
        try:
            # 真物 onnx 模型跑 audio tagging; 我们**不假装**
            logger.debug("voiceprint: detect not implemented (model file absent)")
            return None
        except Exception as e:
            logger.debug("voiceprint: detect failed: %s", e)
            return None

    def stats(self) -> dict:
        return {
            "configured": self.is_configured(),
            "voiceprint_enabled": self.cfg.voiceprint_enabled,
            "audio_event_enabled": self.cfg.audio_event_enabled,
            "voiceprint_threshold": self.cfg.voiceprint_threshold,
            "audio_event_threshold": self.cfg.audio_event_threshold,
            "init_error": self._init_error,
            "sherpa_onnx_version": _safe_version(),
        }


def _safe_version() -> str:
    try:
        import sherpa_onnx  # noqa
        return getattr(sherpa_onnx, "__version__", "unknown")
    except Exception:
        return "not-imported"
