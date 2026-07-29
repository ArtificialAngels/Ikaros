# 详细说明见 docs/scripts/bin/audio_preprocessor.md

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger("ikaros.audio_prep")

# 目标 RMS 级别 (int16 量程百分比, 0~1)
_TARGET_RMS = 0.15
# 噪声门阈值 (RMS 低于此值 → 归零)
_NOISE_GATE_RMS = 0.005
# 噪声门攻击/释放 时间常数 (秒)
_GATE_ATTACK_SEC = 0.01
_GATE_RELEASE_SEC = 0.3
# 软限幅阈值 (int16 量程百分比)
_LIMITER_THRESHOLD = 0.9


class AudioPreprocessor:
    """音频预处理: 去噪 + 归一化 + 限幅."""

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._gate_env: float = 0.0  # 噪声门包络 (RMS 平滑)

    def reset(self) -> None:
        """每句语音结束后重置状态."""
        self._gate_env = 0.0

    def process(self, pcm_bytes: bytes) -> bytes:
        """处理一段 16k mono Int16 PCM, 返回干净的 PCM bytes。

        总延迟 <1ms (纯 numpy 运算)。
        """
        if not pcm_bytes:
            return pcm_bytes

        # 1) 转 float32 [-1, 1]
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if len(audio) == 0:
            return pcm_bytes

        # 2) DC 偏移去除
        audio -= np.mean(audio)

        # 3) RMS 归一化
        rms = np.sqrt(np.mean(audio ** 2) + 1e-10)
        if rms > 1e-6:
            audio = audio * (_TARGET_RMS / max(rms, _TARGET_RMS * 0.5))

        # 4) 噪声门 (平滑 RMS 包络)
        frame_rms = np.sqrt(np.mean(audio ** 2) + 1e-10)
        if frame_rms > _NOISE_GATE_RMS:
            # 语音段: 快速打开
            alpha = min(1.0, len(audio) / self.sample_rate / _GATE_ATTACK_SEC)
            self._gate_env += alpha * (1.0 - self._gate_env)
        else:
            # 静音段: 缓慢关闭
            alpha = min(1.0, len(audio) / self.sample_rate / _GATE_RELEASE_SEC)
            self._gate_env *= (1.0 - alpha)
        audio *= self._gate_env

        # 5) 软限幅
        over = np.abs(audio) - _LIMITER_THRESHOLD
        audio = np.where(
            over > 0,
            np.sign(audio) * (_LIMITER_THRESHOLD + np.tanh(over)),
            audio,
        )

        # 6) 转回 int16
        audio = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
        return audio.tobytes()
