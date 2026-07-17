# bin/qwen_realtime.py — Qwen Realtime API 客户端

## 用途（原模块 docstring）
基于 OpenAI Realtime 协议，通过 WebSocket 连接阿里云 DashScope。内置 ASR + LLM + 可选 native TTS，端到端延迟目标 <500ms。

## 用法
```
from bin.qwen_realtime import QwenRealtimeClient
client = QwenRealtimeClient(api_key="...")
await client.stream_audio(pcm_bytes); await client.commit_audio()
async for event in client.receive():
    if event["type"] == "text": print(event["text"])
    elif event["type"] == "audio": play(event["audio_bytes"])
```

## 环境变量
- `DASHSCOPE_API_KEY` — 阿里云 API key（必须）
- `IKAROS_REALTIME_MODEL` — 模型名（默认 `qwen3-omni-flash-realtime-2025-09-04`）

## 配置常量
- `QWEN_WS_URL = wss://dashscope.aliyuncs.com/api-ws/v1/realtime`
- `QWEN_DEFAULT_MODEL = qwen3-omni-flash-realtime-2025-09-04`
