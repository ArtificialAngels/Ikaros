"""
Ikaros Audio Engine — persistent microphone capture + TTS playback.

Architecture (inspired by DeskMate's ListenEvent.py):
  sounddevice capture thread (16000Hz, 16-bit mono)         [4A.3: pyaudio → sounddevice]
  → energy-based VAD (RMS threshold)
  → silence detection → flush to bridge WebSocket
  → optional wake-word gating
  → bridge WS pushes TTS MP3 chunks → pydub decode → sounddevice OutputStream  [4B]

Config options (set via system tray):
  - Continuous mode: always-on, auto detects speech
  - Wake-word mode: listen for "伊卡洛斯" before processing
  - Sensitivity: VAD threshold adjustment

哥哥 2026-06-27 Phase 4B: 接 voice_server TTS MP3 流到扬声器
  - bridge voice_server 用 edge-tts 输出 MP3 chunk via websocket.send_bytes()
  - 多个 chunks 累加成完整 MP3 (用 MP3 frame header sync 0xFFE/0xFFF)
  - pydub 解码 → numpy int16 array → sounddevice.OutputStream 播放
  - 16kHz mono int16 跟录音同 (降采样如果原始是 24kHz/48kHz)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import threading
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd  # 哥哥 6-27: pyaudio 弃用, sounddevice 替

log = logging.getLogger("icarus.audio")

# Audio config — 16kHz mono int16, 与 Whisper / edge-tts 默认一致
CHANNELS = 1
RATE = 16000
CHUNK = 1024
DTYPE = "int16"  # sounddevice 用 numpy dtype, 替代 pyaudio.paInt16

# VAD defaults
DEFAULT_THRESHOLD = 400
SILENCE_TIMEOUT = 1.2        # seconds of silence = utterance end
MIN_AUDIO_MS = 500            # minimum audio to send (ms)
MAX_UTTERANCE_SEC = 30

# Websocket
WS_URL = "ws://127.0.0.1:7860/v1/voice/ws"

class AudioEngine:
    """Persistent microphone capture with VAD and wake-word."""

    def __init__(self):
        self._running = False
        self._stream: Optional[sd.RawInputStream] = None
        self._ws = None
        self._buffer = bytearray()
        self._speaking = False
        self._last_audio_ts = 0.0
        self._utterance_start = 0.0

        # 4B: TTS playback state — bridge pushes MP3 chunks via WS
        self._tts_chunks: list[bytes] = []   # accumulate MP3 chunks
        self._tts_lock = threading.Lock()
        self._out_stream: Optional[sd.OutputStream] = None
        self._out_device_index: Optional[int] = None  # for OutputStream device

        # Config (mutable from tray)
        self.continuous_mode = True
        self.wake_word_enabled = False
        self.wake_words = ["伊卡洛斯"]
        self.threshold = DEFAULT_THRESHOLD
        self.device_index: Optional[int] = None

        # LLM model for voice (synced from PetWindow model selection)
        self._llm_model: str = "auto"

        # Callbacks
        self.on_state: Optional[Callable[[str], None]] = None
        self.on_bubble: Optional[Callable[[str, int], None]] = None

        # Threads
        self._capture_thread: Optional[threading.Thread] = None
        self._ws_thread: Optional[threading.Thread] = None

    def set_model(self, model: str):
        """Set LLM model for voice. Sends update to voice_server if connected."""
        self._llm_model = model
        # If WS is connected, send set_model action to update server-side
        if self._ws:
            try:
                coro = self._ws.send(json.dumps({"action": "set_model", "model": model}))
                asyncio.run_coroutine_threadsafe(coro, self._aio_loop)
            except Exception:
                pass
        log.info("voice model → %s", model)

    # ─── Device management ───

    def list_devices(self) -> list[dict]:
        devices = []
        try:
            all_devs = sd.query_devices()
        except Exception as exc:
            log.warning("list_devices: %s", exc)
            return devices
        for i, info in enumerate(all_devs):
            if info["max_input_channels"] > 0:
                devices.append({
                    "index": i,
                    "name": info["name"],
                    "channels": info["max_input_channels"],
                })
        return devices

    def set_device(self, index: int):
        self.device_index = index

    # ─── VAD (能量阈值, RMS) ───
    # 与原 pyaudio 版本完全相同 — 输入是 bytes (int16 little-endian), 转 RMS

    def _rms(self, data: bytes) -> float:
        if len(data) < 2:
            return 0.0
        count = len(data) // 2
        try:
            # numpy 比 struct.unpack 快 10x, 但保持兼容行为
            arr = np.frombuffer(data[:count * 2], dtype=np.int16)
            if len(arr) == 0:
                return 0.0
            return float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
        except Exception:
            # 回退到 struct (与原版完全一致)
            try:
                fmt = f"<{count}h"
                samples = struct.unpack(fmt, data[:count * 2])
                return (sum(s * s for s in samples) / count) ** 0.5
            except Exception:
                return 0.0

    # ─── WebSocket client ───

    async def _ws_loop(self):
        import websockets
        # 清掉代理 env var, 避免 httpx/websockets 走系统代理失败
        for k in list(os.environ.keys()):
            if 'proxy' in k.lower():
                os.environ.pop(k, None)
        uri = WS_URL
        while self._running:
            try:
                async with websockets.connect(uri, proxy=None) as ws:
                    self._ws = ws
                    # Send start with current LLM model
                    await ws.send(json.dumps({"action": "start", "model": self._llm_model}))
                    self._emit_state("LISTENING")
                    self._emit_bubble("🎤 我在听~", 2000)

                    while self._running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=0.3)
                            if isinstance(msg, bytes):
                                # 4B: TTS MP3 chunk from edge-tts → accumulate
                                with self._tts_lock:
                                    self._tts_chunks.append(msg)
                                log.debug("TTS chunk: %dB (total %d chunks)", len(msg), len(self._tts_chunks))
                            else:
                                data = json.loads(msg)
                                t = data.get("type", "")
                                if t == "transcription":
                                    self._emit_bubble(data.get("text", "?"), 3000)
                                elif t == "thinking":
                                    self._emit_state("THINKING")
                                elif t == "status":
                                    self._emit_bubble(data.get("message", ""), 2000)
                                elif t == "done":
                                    self._emit_bubble(data.get("text", "嗯~"), 5000)
                                    self._emit_state("SPEAKING")
                                    # 4B: done = bridge TTS stream finished → play accumulated MP3
                                    chunks_count = 0
                                    with self._tts_lock:
                                        chunks_count = len(self._tts_chunks)
                                    if chunks_count > 0:
                                        # 异步播放 — 不阻塞 WS loop (audio 可能 5-30s)
                                        self._aio_loop.create_task(self._play_tts_async())
                                    else:
                                        log.warning("done 但 _tts_chunks 为空, 没 TTS 音频可播")
                                    await asyncio.sleep(0.5)
                                    self._emit_state("LISTENING")
                                elif t == "error":
                                    self._emit_bubble(f"⚠️ {data.get('message', '?')}", 4000)
                        except asyncio.TimeoutError:
                            continue
            except Exception as exc:
                log.warning("WS: %s, retry 3s", exc)
                await asyncio.sleep(3)

    def _emit_state(self, s: str):
        if self.on_state:
            self.on_state(s)

    def _emit_bubble(self, t: str, d: int = 3000):
        if self.on_bubble:
            self.on_bubble(t, d)

    # ─── TTS playback (4B) ───

    async def _play_tts_async(self):
        """Decode accumulated MP3 chunks → 16kHz mono int16 → sounddevice OutputStream.

        在 aio_loop 异步任务里跑 — 不阻塞 WS receive.
        完成后清空 _tts_chunks 准备下一轮.
        """
        try:
            mp3_bytes = b"".join(self._tts_chunks)
            with self._tts_lock:
                self._tts_chunks.clear()
            if not mp3_bytes:
                return
            await asyncio.get_event_loop().run_in_executor(
                None, self.play_mp3_bytes, mp3_bytes,
            )
        except Exception as exc:
            log.error("TTS playback failed: %s", exc)

    def play_mp3_bytes(self, mp3_bytes: bytes):
        """同步播放 MP3 bytes — 16kHz mono int16 → sounddevice.OutputStream.

        pydub 解码 MP3 (需要 ffmpeg/libav), 输出 numpy int16 array.
        sounddevice.OutputStream 流式 write, 不要一次性 load 全部 (避免 30s 阻塞).

        Args:
            mp3_bytes: 完整 MP3 文件 bytes (edge-tts 输出的多 chunk 合并)
        """
        from pydub import AudioSegment
        import io

        try:
            # 解码 (FFmpeg backend, 需要 ffmpeg.exe 在 PATH 或 pydub 找得到)
            audio = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
            # 重采样到 16kHz mono int16 (跟录音同)
            audio = audio.set_frame_rate(RATE).set_channels(1).set_sample_width(2)

            import numpy as _np
            samples = _np.frombuffer(audio.raw_data, dtype=_np.int16)
            duration_sec = len(samples) / RATE
            log.info(
                "TTS playing: %d samples (%.2fs @%dHz), %dB MP3",
                len(samples), duration_sec, RATE, len(mp3_bytes),
            )

            # 选 output device (默认)
            out_kwargs = dict(samplerate=RATE, channels=1, dtype="int16")
            if self._out_device_index is not None:
                out_kwargs["device"] = self._out_device_index

            # 流式播放 — 一次 0.5s chunk, 让回调 (如果以后有) 能 trigger
            with sd.OutputStream(**out_kwargs) as stream:
                chunk_samples = RATE // 2  # 0.5s
                for i in range(0, len(samples), chunk_samples):
                    if not self._running:
                        break
                    buf = samples[i:i + chunk_samples]
                    stream.write(buf)
        except FileNotFoundError as exc:
            log.error("FFmpeg not found on PATH — pydub can't decode MP3: %s", exc)
            log.error("哥哥: 把 runtime/ffmpeg/ 加进 PATH 或装 ffmpeg.exe")
        except Exception as exc:
            log.error("TTS playback error: %s", exc)

    # ─── Capture thread ───
    # 哥哥 6-27: sounddevice.RawInputStream (blocking mode) 替代 pyaudio.Stream
    # RawInputStream 返回原始 int16 bytes, 跳过 numpy 转换 — 性能更好

    def _capture(self):
        log.info("audio: capture started (sounddevice RawInputStream)")
        try:
            stream_kwargs = dict(
                samplerate=RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=CHUNK,
            )
            if self.device_index is not None:
                stream_kwargs["device"] = self.device_index
            self._stream = sd.RawInputStream(**stream_kwargs)
            self._stream.start()
        except Exception as exc:
            log.error("audio: open stream failed: %s", exc)
            return

        silence_chunks = 0
        speech_chunks = 0

        try:
            while self._running and self._stream.active:
                try:
                    # RawInputStream.read 返 (bytes, overflowed: bool)
                    data, overflowed = self._stream.read(CHUNK)
                except Exception as exc:
                    log.warning("audio: stream.read failed: %s", exc)
                    break

                # data 已经是 bytes (raw int16), 直接喂 VAD
                rms = self._rms(data)
                now = time.time()

                if rms > self.threshold:
                    # Voice detected
                    self._buffer.extend(data)
                    self._last_audio_ts = now
                    if not self._speaking:
                        self._speaking = True
                        self._utterance_start = now
                        speech_chunks = 0
                        if self.continuous_mode:
                            self._emit_state("LISTENING")
                    speech_chunks += 1
                    silence_chunks = 0
                else:
                    if self._speaking:
                        self._buffer.extend(data)  # keep trailing silence
                        silence_chunks += 1
                        # Check silence timeout
                        dur = (now - self._last_audio_ts)
                        total = (now - self._utterance_start)

                        if dur > SILENCE_TIMEOUT or total > MAX_UTTERANCE_SEC:
                            self._flush()
                    else:
                        # Keep a rolling 0.5s buffer for VAD context
                        max_pre = int(RATE * 0.5 * 2)
                        if len(self._buffer) > max_pre:
                            self._buffer = self._buffer[-max_pre:]
        finally:
            self._flush()
            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def _flush(self):
        if len(self._buffer) < int(RATE * MIN_AUDIO_MS / 1000 * 2):
            self._buffer.clear()
            self._speaking = False
            return
        audio = bytes(self._buffer)
        self._buffer.clear()
        self._speaking = False

        # Check wake word if enabled
        # (In a real implementation, this would use a lightweight ASR)
        # For now: send all speech through, wake word handled by LLM
        # 静音5秒后—听歌/睡觉等不会被误触发
        if self._ws:
            try:
                coro = self._ws.send(audio)
                asyncio.run_coroutine_threadsafe(coro, self._aio_loop)
            except Exception:
                # 如果没有 aio_loop (还未启动 _run_ws), 简单忽略
                pass

        self._emit_state("LISTENING")

    # ─── Lifecycle ───

    def start(self):
        if self._running:
            return
        self._running = True
        self._ws_thread = threading.Thread(target=self._run_ws, daemon=True)
        self._ws_thread.start()
        time.sleep(0.5)  # Let WS connect first
        self._capture_thread = threading.Thread(target=self._capture, daemon=True)
        self._capture_thread.start()
        log.info("audio: engine started")

    def _run_ws(self):
        self._aio_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._aio_loop)
        try:
            self._aio_loop.run_until_complete(self._ws_loop())
        finally:
            self._aio_loop.close()

    def stop(self):
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def close(self):
        self.stop()
        # sounddevice 不需要显式 terminate (它用 PortAudio, OS 管 lifecycle)