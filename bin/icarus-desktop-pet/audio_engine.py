"""
Icarus Audio Engine — persistent microphone capture with VAD + wake word.

Architecture (inspired by DeskMate's ListenEvent.py):
  sounddevice capture thread (16000Hz, 16-bit mono)         [4A.3: pyaudio → sounddevice]
  → energy-based VAD (RMS threshold)
  → silence detection → flush to bridge WebSocket
  → optional wake-word gating

Config options (set via system tray):
  - Continuous mode: always-on, auto detects speech
  - Wake-word mode: listen for "伊卡洛斯" before processing
  - Sensitivity: VAD threshold adjustment

哥哥 2026-06-27 Phase 4A: pyaudio crashes on this system (Quest 注释 line 595).
sounddevice 用 PortAudio 但 binding 是 ctypes (不是 PyAudio 的 CFFI), 在 Win11 上更稳。
API 差异:
  - pyaudio.open(format=paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1024)
    + stream.read(CHUNK, exception_on_overflow=False)  → 阻塞, 返 bytes
  - sounddevice.RawInputStream(samplerate=16000, channels=1, dtype='int16',
                                blocksize=1024, callback=None)  ← callback=None 走 blocking mode
    + stream.read(1024)  → 返 numpy array, .tobytes() 转 bytes
VAD / WS / wake-word 流程不变, 只换底层 IO.
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

        # Config (mutable from tray)
        self.continuous_mode = True
        self.wake_word_enabled = False
        self.wake_words = ["伊卡洛斯"]
        self.threshold = DEFAULT_THRESHOLD
        self.device_index: Optional[int] = None

        # Callbacks
        self.on_state: Optional[Callable[[str], None]] = None
        self.on_bubble: Optional[Callable[[str, int], None]] = None

        # Threads
        self._capture_thread: Optional[threading.Thread] = None
        self._ws_thread: Optional[threading.Thread] = None

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
                    await ws.send(json.dumps({"action": "start"}))
                    self._emit_state("LISTENING")
                    self._emit_bubble("🎤 我在听~", 2000)

                    while self._running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=0.3)
                            if isinstance(msg, bytes):
                                # TTS audio — 4B 阶段接 pydub/sounddevice 播放
                                pass
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
        if self._ws:
            try:
                # Send via the existing websocket
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(
                    asyncio.create_task,
                    self._ws.send(audio),
                )
            except RuntimeError:
                # No running loop — create a fire-and-forget task
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._ws.send(audio),
                        self._aio_loop,
                    )
                except Exception:
                    pass
            except Exception:
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