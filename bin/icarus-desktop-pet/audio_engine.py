"""
Icarus Audio Engine — pyaudio capture → WebSocket bridge.

Captures microphone audio in a background thread, performs simple
energy-based VAD, and sends audio chunks to the bridge's voice
WebSocket endpoint for STT → LLM → TTS processing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import threading
import time
from typing import Optional

import pyaudio

logger = logging.getLogger("icarus.audio")

# ---- Config ----
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1024
SILENCE_THRESHOLD = 300  # RMS below this = silence
SILENCE_TIMEOUT = 1.2    # seconds of silence before flush
WS_URL = "ws://127.0.0.1:7860/v1/voice/ws"


class AudioEngine:
    """Microphone capture and VAD engine.

    Captures audio in a thread, detects speech/silence, and sends
    audio chunks through a WebSocket to the bridge.
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._p = pyaudio.PyAudio()
        self._stream: Optional[pyaudio.Stream] = None
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._audio_buffer = bytearray()
        self._last_audio_time = 0.0
        self._is_speaking = False
        self._on_state_change = None  # callback(state)
        self._on_transcription = None  # callback(text)
        self._device_index: Optional[int] = None

    @property
    def device_count(self) -> int:
        """Number of available audio input devices."""
        return self._p.get_device_count()

    def list_devices(self) -> list[dict]:
        """List all audio input devices."""
        devices = []
        for i in range(self._p.get_device_count()):
            info = self._p.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                devices.append({
                    "index": i,
                    "name": info["name"],
                    "channels": info["maxInputChannels"],
                    "sample_rate": int(info["defaultSampleRate"]),
                })
        return devices

    def set_device(self, index: int):
        """Select a specific microphone by device index."""
        self._device_index = index

    def set_callbacks(self, on_state=None, on_transcription=None):
        self._on_state_change = on_state
        self._on_transcription = on_transcription

    def _rms(self, data: bytes) -> float:
        """Compute RMS amplitude of raw 16-bit PCM data."""
        if len(data) < 2:
            return 0.0
        count = len(data) // 2
        try:
            fmt = f"<{count}h"
            samples = struct.unpack(fmt, data[:count * 2])
            return (sum(s * s for s in samples) / count) ** 0.5
        except Exception:
            return 0.0

    def _run_ws_client(self):
        """Run the WebSocket client in its own asyncio event loop."""
        async def _client():
            import websockets
            async with websockets.connect(WS_URL) as ws:
                self._ws = ws
                await ws.send(json.dumps({"action": "start"}))
                logger.info("audio engine: WebSocket connected")

                while self._running:
                    try:
                        # Receive messages from bridge (transcription, audio)
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.1)
                        if isinstance(msg, bytes):
                            # TTS audio chunk — forward to callback
                            if self._on_transcription:
                                self._on_transcription({"type": "audio", "data": msg})
                        else:
                            data = json.loads(msg)
                            if data.get("type") == "transcription":
                                if self._on_transcription:
                                    self._on_transcription(data)
                            elif data.get("type") == "status":
                                if data.get("message") == "思考中…":
                                    self._emit_state("thinking")
                            elif data.get("type") == "done":
                                self._emit_state("idle")
                                if self._on_transcription:
                                    self._on_transcription({"type": "done"})
                    except asyncio.TimeoutError:
                        continue
                    except websockets.exceptions.ConnectionClosed:
                        logger.warning("audio engine: WS disconnected")
                        break

                self._ws = None

        try:
            asyncio.run(_client())
        except Exception as exc:
            logger.error("audio engine: WS client error: %s", exc)

    def _emit_state(self, state: str):
        if self._on_state_change:
            self._on_state_change(state)

    def _capture_thread(self):
        """Audio capture + VAD loop."""
        try:
            kwargs = {
                "format": FORMAT,
                "channels": CHANNELS,
                "rate": RATE,
                "input": True,
                "frames_per_buffer": CHUNK,
                "stream_callback": None,
            }
            if self._device_index is not None:
                kwargs["input_device_index"] = self._device_index

            self._stream = self._p.open(**kwargs)
            logger.info("audio engine: capture started")

            buffered_silence = 0.0
            min_audio_chunks = 5  # minimum chunks before sending

            while self._running and self._stream.is_active():
                try:
                    data = self._stream.read(CHUNK, exception_on_overflow=False)
                except Exception:
                    break

                rms = self._rms(data)
                now = time.time()

                if rms > SILENCE_THRESHOLD:
                    # Voice detected
                    self._audio_buffer.extend(data)
                    self._last_audio_time = now
                    if not self._is_speaking:
                        self._is_speaking = True
                        self._emit_state("listening")
                    buffered_silence = 0.0
                else:
                    if self._is_speaking:
                        buffered_silence += CHUNK / RATE
                        self._audio_buffer.extend(data)

                        if buffered_silence > SILENCE_TIMEOUT:
                            # Silence timeout — flush audio
                            self._flush_audio()
                    else:
                        # Not speaking, not buffering — keep minimal buffer for VAD
                        if len(self._audio_buffer) > 0:
                            # Keep last 0.5s of silence for context
                            max_buf = int(RATE * 0.5 * 2)  # 0.5s of 16-bit mono
                            if len(self._audio_buffer) > max_buf:
                                self._audio_buffer = self._audio_buffer[-max_buf:]

            self._flush_audio()

        except Exception as exc:
            logger.error("audio engine: capture error: %s", exc)
        finally:
            if self._stream:
                try:
                    self._stream.stop_stream()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def _flush_audio(self):
        """Send accumulated audio through WebSocket."""
        if len(self._audio_buffer) < 4096:
            self._audio_buffer.clear()
            self._is_speaking = False
            return

        data = bytes(self._audio_buffer)
        self._audio_buffer.clear()
        self._is_speaking = False

        if self._ws and data:
            try:
                # Send audio via the running WS client
                # We use a synchronized queue approach
                import queue
                self._audio_queue.put(data)
            except Exception:
                pass

    def _ws_send_loop(self):
        """Background thread that sends queued audio through WebSocket."""
        while self._running:
            try:
                data = self._audio_queue.get(timeout=0.5)
                if self._ws:
                    try:
                        import asyncio
                        asyncio.run_coroutine_threadsafe(
                            self._ws.send(data), self._loop
                        )
                    except Exception:
                        pass
            except Exception:
                pass

    def start(self):
        """Start audio capture and WebSocket client."""
        if self._running:
            return
        self._running = True
        self._audio_queue = __import__('queue').Queue()

        # Start WebSocket client in a thread
        self._ws_thread = threading.Thread(target=self._run_ws_client, daemon=True)
        self._ws_thread.start()

        # Start capture thread
        self._capture_thread_obj = threading.Thread(target=self._capture_thread, daemon=True)
        self._capture_thread_obj.start()

        # Wait a moment for WS to connect
        time.sleep(1)

    def stop(self):
        """Stop audio capture."""
        self._running = False
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass

    def close(self):
        self.stop()
        if self._p:
            self._p.terminate()
