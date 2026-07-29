# bin/audio_preprocessor.py — 轻量音频预处理

## 用途（原模块 docstring）
移植自 N.E.K.O 思路。处理链：DC 偏移去除 → RMS 归一化 → 简单噪声门 → 软限幅。纯 numpy/scipy 实现，无 C 编译依赖，<1ms 延迟。

## 用法
```
from bin.audio_preprocessor import AudioPreprocessor
ap = AudioPreprocessor(sample_rate=16000)
clean_pcm = ap.process(raw_pcm_int16)
```

## 处理参数（常量）
- `_TARGET_RMS = 0.15`（目标 RMS 级别，int16 量程百分比）
- `_NOISE_GATE_RMS = 0.005`（噪声门阈值，RMS 低于此 → 归零）
- `_GATE_ATTACK_SEC = 0.01` / `_GATE_RELEASE_SEC = 0.3`（噪声门攻击/释放时间常数）
- `_LIMITER_THRESHOLD = 0.9`（软限幅阈值，int16 量程百分比）

`AudioPreprocessor`：`_gate_env` 为噪声门包络（RMS 平滑）。
