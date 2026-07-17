# __init__.py

> 源文件：`Ikaros-memory/cogno_extensions/__init__.py`

cogno_extensions -- Ikaros cogno 5D 元能力扩展.

包含:
- vlm_provider.py: Vision Language Model router (抽 MewCo-AI:vlm.py 222 行)
- voiceprint.py: 声纹识别 (抽 MewCo-AI:asr.py 220 行, sherpa_onnx 模型)
- vlm_extractor.py: Live2DPet vlm-extractor.js 5s 周期截屏 + 3 级 mipmap

设计: 抽模式, 适配 Ikaros 配置 + cogno_5d 第 6 维.
不直接复制源文件 (避免污染 self-config / 双亡 import 链).
