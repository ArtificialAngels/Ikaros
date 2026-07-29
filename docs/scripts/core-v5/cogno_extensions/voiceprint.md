# voiceprint.py

> 源文件：`Ikaros-memory/cogno_extensions/voiceprint.py`

voiceprint.py -- 声纹识别 wrapper (抽 MewCo-AI:asr.py).

源: MewCo-AI/mewco_ai_assistant_comm/asr.py (204 行, 用 sherpa_onnx
+ 3DSpeaker + AudioTag ZipFormer 三件套). MewCo 自身依赖 GUI
Tkinter + config.json global vars -- 不直接复用. 这里抽**核心**
声纹 + 音频事件检测两 mode, 用 Ikaros 自己 config dict 装.

设计原则 (v3 0.95 不重复发明):
- 不复制 config global vars (data/db/config.json) -- 用 init(...)
  显式接 config
- 不复制 pyaudio 全双工 stream (MewCo 是 CHANNELS=1, RATE=16000,
  CHUNK=1024) -- 只暴露 extract_embedding(audio_bytes) 接口
  让 audio_engine.py 调 (audio_engine 已经有 PCM 流)
- 模型路径按 v3 0.84 [IL2] 原则"不擅自下模型", 留 None 让调用
  者传 (cloud_chat.py 里看)
- 失败静默: cogno_5d 第 6 维失败 -> [未知-vp-not-configured]

## 内联注释摘录

            # 真物 extractor 镜像 MewCo:asr.py L23-25:
            #   extractor = sherpa_onnx.SpeakerEmbeddingExtractor(...)
            #   embedding = extractor.compute(audio_np)
            # 我们**不假装**能算 embedding (没模型文件)
            # 返 None -> cogno 第 6 维 [未知-vp-not-configured]

