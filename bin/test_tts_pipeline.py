#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_tts_pipeline.py — 验证 voice-ws 的 TTS 链路 (Hermes 内置 TTS)。

连 ws://127.0.0.1:7870/v1/voice/ws, 发 {action:text} 触发 LLM+TTS,
捕获回传的二进制音频帧并落盘, 校验格式/大小。

用法: portable-python -u bin/test_tts_pipeline.py
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, r"E:/Ikaros/bin")
import websockets  # noqa: E402

URI = "ws://127.0.0.1:7870/v1/voice/ws"
TEST_TEXT = "你好，伊卡洛斯，请给我讲一个关于星星的小故事。"
OUT = r"E:/Ikaros/data/models/_tts_e2e.mp3"


def _sniff(data: bytes) -> str:
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data[:4] == b"RIFF":
        return "wav"
    if data[:4] == b"OggS":
        return "ogg"
    return "unknown"


async def main():
    got_json = []
    audio = b""
    async with websockets.connect(URI) as ws:
        await ws.send(json.dumps({"action": "start", "session_id": "e2e_tts"}))
        await ws.send(json.dumps({"action": "text", "text": TEST_TEXT}))
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=40)
                if isinstance(msg, bytes):
                    audio += msg
                    print(f"[audio] binary frame {len(msg)} bytes (total {len(audio)})")
                    if len(audio) > 2000:  # 收到足够音频即收尾
                        break
                else:
                    try:
                        m = json.loads(msg)
                        got_json.append(m.get("type"))
                        print(f"[json] {m.get('type')}: "
                              f"{str(m.get('text', m.get('message','')))[:40]}")
                    except Exception:
                        print(f"[raw] {msg[:60]!r}")
        except asyncio.TimeoutError:
            print("[timeout] 40s 内未收齐音频")

    print("\n=== RESULT ===")
    print("json types seen:", got_json)
    print("audio bytes:", len(audio), "format:", _sniff(audio))
    if audio:
        with open(OUT, "wb") as f:
            f.write(audio)
        print("audio saved ->", OUT)
        print("TTS PIPELINE:", "OK" if len(audio) > 500 else "FAIL (too small)")
    else:
        print("TTS PIPELINE: FAIL (no audio frame)")


if __name__ == "__main__":
    asyncio.run(main())
