# 详细说明见 docs/scripts/bin/test_stt_pipeline.md
import asyncio
import io
import json
import sys

sys.path.insert(0, r"E:/Ikaros/bin")

import edge_tts
from pydub import AudioSegment
import websockets

WS_URL = "ws://127.0.0.1:7870/v1/voice/ws"
SYNTH_TEXT = "你好伊卡洛斯，今天天气怎么样"


async def main():
    print(f"[1] edge-tts 合成: {SYNTH_TEXT!r}")
    buf = io.BytesIO()
    comm = edge_tts.Communicate(SYNTH_TEXT, voice="zh-CN-XiaoxiaoNeural")
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    buf.seek(0)
    size = buf.getbuffer().nbytes
    print(f"    mp3 bytes = {size}")

    print("[2] 解码 mp3 -> 16k mono Int16 PCM")
    seg = AudioSegment.from_file(buf, format="mp3")
    seg = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    pcm = seg.raw_data
    print(f"    pcm bytes = {len(pcm)} (~{len(pcm)//32000:.1f}s @16k)")

    print(f"[3] 连 {WS_URL}")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({"action": "start", "session_id": "stt_test"}))
        try:
            rep = await asyncio.wait_for(ws.recv(), 3)
            if isinstance(rep, bytes):
                print("    start reply: [binary]")
            else:
                print(f"    start reply: {rep[:200]}")
        except asyncio.TimeoutError:
            print("    start reply: (timeout)")

        print("[4] 分块发 PCM 二进制帧")
        chunk = 4096
        n = 0
        for i in range(0, len(pcm), chunk):
            await ws.send(pcm[i : i + chunk])
            n += 1
        print(f"    发送 {n} 帧")

        print("[5] 发 end_utterance (断句)")
        await ws.send(json.dumps({"action": "end_utterance"}))

        print("[6] 收后端响应 ===")
        for _ in range(25):
            try:
                msg = await asyncio.wait_for(ws.recv(), 20)
            except asyncio.TimeoutError:
                print("    (timeout waiting more)")
                break
            if isinstance(msg, bytes):
                print(f"    [binary TTS audio {len(msg)} bytes]")
                continue
            try:
                d = json.loads(msg)
            except Exception:
                print(f"    [non-json] {msg[:120]}")
                continue
            t = d.get("type")
            if t == "partial":
                print(f"    [partial] {d.get('text')}")
            elif t == "transcription":
                print(f"    [transcription] {d.get('text')}")
            elif t == "done":
                print(f"    [done] {d.get('text')}")
            elif t == "stt_status":
                print(f"    [stt_status] {d}")
            elif t == "thinking":
                print("    [thinking...]")
            else:
                print(f"    [{t}] {d.get('text', d.get('message', ''))}")


if __name__ == "__main__":
    asyncio.run(main())
