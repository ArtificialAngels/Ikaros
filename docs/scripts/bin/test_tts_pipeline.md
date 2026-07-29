# test_tts_pipeline.py — 验证 voice-ws 的 TTS 链路

> 源文件：`bin/test_tts_pipeline.py`
> 作用：验证 Hermes 内置 TTS 链路，落盘校验音频格式/大小。

## 说明（模块 docstring 原文）

`test_tts_pipeline.py` — 验证 voice-ws 的 TTS 链路 (Hermes 内置 TTS)。
连 `ws://127.0.0.1:7870/v1/voice/ws`，发 `{action:text}` 触发 LLM+TTS，
捕获回传的二进制音频帧并落盘，校验格式/大小。

用法：`portable-python -u bin/test_tts_pipeline.py`

## 关键常量

- `URI = ws://127.0.0.1:7870/v1/voice/ws`
- `TEST_TEXT = "你好，伊卡洛斯，请给我讲一个关于星星的小故事。"`
- `OUT = E:/Ikaros/data/models/_tts_e2e.mp3`

连后端前 `sys.path.insert(0, r"E:/Ikaros/bin")`（测试脚本，固定盘符路径）。
收到 >2000 字节音频即收尾，落盘后按大小判定 `OK` / `FAIL`。
