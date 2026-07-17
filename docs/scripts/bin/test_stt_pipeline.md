# test_stt_pipeline.py — 端到端验证 STT 链路

> 源文件：`bin/test_stt_pipeline.py`
> 作用：不依赖桌宠 UI，直接连 `:7870` 验证后端音频处理能力。

## 说明（模块 docstring 原文）

端到端验证 STT 链路：edge-tts 合成中文 → 16k mono PCM → ws 二进制帧 → vosk 识别 → LLM/TTS。
不依赖桌宠 UI，直接连 `:7870` 验证后端音频处理能力。

## 流程

1. `edge-tts` 合成中文（voice=`zh-CN-XiaoxiaoNeural`）
2. 解码 mp3 → 16k mono Int16 PCM（pydub）
3. 连 `ws://127.0.0.1:7870/v1/voice/ws`，发 `start`
4. 分块（4096B）发 PCM 二进制帧
5. 发 `end_utterance`（断句）
6. 收取后端响应（partial / transcription / done / thinking / 二进制 TTS 音频）

连后端前 `sys.path.insert(0, r"E:/Ikaros/bin")`（测试脚本，固定盘符路径）。
