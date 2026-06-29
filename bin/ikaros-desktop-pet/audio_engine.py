"""
Ikaros Audio Engine — persistent microphone capture + TTS playback.

Architecture:
  sounddevice capture thread (16000Hz, 16-bit mono)
  → energy-based VAD (RMS threshold)
  → silence detection → flush raw PCM binary to bridge WebSocket
  → bridge SenseVoice STT → text correction → LLM → TTS
  → bridge WS pushes TTS MP3 chunks → pydub decode → sounddevice OutputStream

Config options (set via system tray):
  - Continuous mode: always-on, auto detects speech
  - Wake-word mode: listen for "伊卡洛斯" before processing
  - Sensitivity: VAD threshold adjustment
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

# B: TTS 答缓存 (哥哥 6-29 拍板)
try:
    from tts_cache import get_cache as _tts_cache
except ImportError:
    _tts_cache = None  # tts_cache.py 缺失时降级到原行为

from _monitor_events import log_stt, log_llm_reply, log_state, log_status, log_error
from _monitor_events import log_module_status, log_heartbeat, log_event
from _monitor_events import HEARTBEAT_CAPTURE_INTERVAL, HEARTBEAT_TTS_INTERVAL

log = logging.getLogger("ikaros.audio")

# Audio config — 16kHz mono int16, 与 Whisper / edge-tts 默认一致
CHANNELS = 1
RATE = 16000
CHUNK = 1024
DTYPE = "int16"  # sounddevice 用 numpy dtype, 替代 pyaudio.paInt16

# VAD defaults
DEFAULT_THRESHOLD = 100
SILENCE_TIMEOUT = 1.2        # seconds of silence = utterance end
MIN_AUDIO_MS = 500            # minimum audio to send (ms)
MAX_UTTERANCE_SEC = 30

# AGC (Automatic Gain Control) — 环境噪音自适应增益
AGC_TARGET_RMS = 800          # 目标 RMS (int16 范围 0~32768)
AGC_MAX_GAIN = 8.0            # 最大增益倍数
AGC_MIN_GAIN = 1.0            # 最小增益倍数 (安静环境不放大)
AGC_NOISE_ATTACK = 0.05       # 噪音底上升速度 (快适应)
AGC_NOISE_DECAY = 0.005       # 噪音底下降速度 (慢适应)
AGC_GAIN_ATTACK = 0.1         # 增益上升速度
AGC_GAIN_DECAY = 0.05         # 增益下降速度
AGC_MIN_NOISE = 20            # 最低噪音底 (避免除零)
AGC_MAX_NOISE = 2000          # 最高噪音底 (超过不再压制)

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

        # AGC state — 环境噪音自适应增益
        self._agc_noise_floor = 100.0    # 当前噪音底估计 (RMS)
        self._agc_gain = 1.0             # 当前增益倍数
        self._agc_enabled = True         # AGC 开关 (可从 tray 控制)

        # 4B: TTS playback state — bridge pushes MP3 chunks via WS
        self._tts_chunks: list[bytes] = []   # accumulate MP3 chunks
        self._tts_lock = threading.Lock()
        self._out_stream: Optional[sd.OutputStream] = None
        self._out_device_index: Optional[int] = None  # for OutputStream device

        # A: TTS 打断 + 优先级 (哥哥 6-29 拍板)
        self._tts_playing: bool = False
        self._tts_queue: asyncio.Queue = None  # 初始化在 aio_loop 启动后
        self._tts_interrupted: bool = False
        self._tts_play_lock = threading.Lock()  # 跟 _tts_lock 区分
        self._user_spoke_recently: float = 0.0  # 用户最近说话时间戳 (unix)

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
                coro = self._ws.send(json.dumps({"type": "set_model", "model": model}))
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

    # ─── AGC (环境噪音自适应增益) ───

    def _agc_update(self, rms: float, is_speaking: bool) -> float:
        """根据环境噪音更新增益, 返回当前增益值.

        静音期间: 追踪噪音底 RMS (指数移动平均)
        说话期间: 噪音底冻结, 增益保持不变
        """
        if not self._agc_enabled:
            return 1.0

        if not is_speaking:
            # 静音期: 更新噪音底估计
            if rms < self._agc_noise_floor:
                # 噪音下降 — 慢适应 (避免突然安静时增益飙升)
                self._agc_noise_floor += AGC_NOISE_DECAY * (rms - self._agc_noise_floor)
            else:
                # 噪音上升 — 快适应 (快速跟踪环境变吵)
                self._agc_noise_floor += AGC_NOISE_ATTACK * (rms - self._agc_noise_floor)

            # 钳位噪音底
            self._agc_noise_floor = max(AGC_MIN_NOISE, min(AGC_MAX_NOISE, self._agc_noise_floor))

            # 计算目标增益
            target_gain = AGC_TARGET_RMS / max(self._agc_noise_floor, AGC_MIN_NOISE)
            target_gain = max(AGC_MIN_GAIN, min(AGC_MAX_GAIN, target_gain))

            # 平滑增益变化
            if target_gain < self._agc_gain:
                self._agc_gain += AGC_GAIN_DECAY * (target_gain - self._agc_gain)
            else:
                self._agc_gain += AGC_GAIN_ATTACK * (target_gain - self._agc_gain)

        return self._agc_gain

    def _apply_gain(self, data: bytes, gain: float) -> bytes:
        """对 int16 PCM 数据应用增益."""
        if abs(gain - 1.0) < 0.01:
            return data  # 增益接近 1, 跳过转换
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        samples *= gain
        # 软限幅 (避免削波失真)
        np.clip(samples, -32768, 32767, out=samples)
        return samples.astype(np.int16).tobytes()

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
                    self._ws_connected = True
                    log_module_status("voice_ws", "connected")
                    # Send start with current LLM model
                    await ws.send(json.dumps({"type": "start", "model": self._llm_model}))
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
                                    text = data.get("text", "?")
                                    log_stt(text)
                                    self._emit_bubble(text, 3000)
                                elif t == "thinking":
                                    log_state("thinking")
                                    self._emit_state("THINKING")
                                elif t == "status":
                                    msg = data.get("message", "")
                                    if msg:
                                        log_status(msg)
                                    self._emit_bubble(msg, 2000)
                                elif t == "done":
                                    reply_text = data.get("text", "嗯~")
                                    model_name = data.get("model", "")
                                    log_llm_reply(reply_text)
                                    if model_name:
                                        log_event("model_info", model_name)
                                    self._emit_bubble(reply_text, 5000)
                                    self._emit_state("SPEAKING")
                                    # 4B: done = bridge TTS stream finished → play accumulated MP3
                                    chunks_count = 0
                                    with self._tts_lock:
                                        chunks_count = len(self._tts_chunks)
                                    if chunks_count > 0:
                                        # A: 不再 create_task, 改入队列 + 单 worker 顺序播放
                                        # 排队的 mp3 bytes 走 _tts_play_worker (单线程顺序)
                                        with self._tts_lock:
                                            mp3_bytes = b"".join(self._tts_chunks)
                                            self._tts_chunks.clear()
                                        # 排队 (maxsize=3 防内存爆, 满了丢最旧)
                                        if self._tts_queue is not None:
                                            try:
                                                self._tts_queue.put_nowait(mp3_bytes)
                                            except asyncio.QueueFull:
                                                # 丢最旧, 入新
                                                try:
                                                    self._tts_queue.get_nowait()
                                                except Exception:
                                                    pass
                                                try:
                                                    self._tts_queue.put_nowait(mp3_bytes)
                                                except Exception:
                                                    pass
                                                log.warning("TTS queue full, dropped oldest")
                                        else:
                                            # 兜底: aio_loop 还没启, 同步播
                                            self._tts_playing = True
                                            self.play_mp3_bytes(mp3_bytes)
                                            self._tts_playing = False
                                    else:
                                        log.warning("done 但 _tts_chunks 为空, 没 TTS 音频可播")
                                    await asyncio.sleep(0.5)
                                    # Re-send "start" to re-enable audio session on Rust bridge.
                                    # Without this, is_audio_session=false on server and new audio
                                    # arriving before the next explicit "start" would be dropped.
                                    if self._ws and self._running:
                                        try:
                                            await self._ws.send(json.dumps({"type": "start", "model": self._llm_model}))
                                        except Exception:
                                            pass
                                    self._emit_state("LISTENING")
                                elif t == "error":
                                    err_msg = data.get('message', '?')
                                    log_error(err_msg)
                                    self._emit_bubble(f"⚠️ {err_msg}", 4000)
                        except asyncio.TimeoutError:
                            continue
                    # Inner loop exit (stop() or connection closed)
                    if self._ws_connected:
                        self._ws_connected = False
                        log_module_status("voice_ws", "disconnected")
            except Exception as exc:
                log.warning("WS: %s, retry 3s", exc)
                if self._ws_connected:
                    self._ws_connected = False
                    log_module_status("voice_ws", "disconnected")
                self._ws = None
                await asyncio.sleep(3)

    def _emit_state(self, s: str):
        if self.on_state:
            self.on_state(s)

    def _emit_bubble(self, t: str, d: int = 3000):
        if self.on_bubble:
            self.on_bubble(t, d)

    # ─── TTS playback ───

    async def _play_tts_async(self):
        """Decode accumulated MP3 chunks → 16kHz mono int16 → sounddevice OutputStream.

        在 aio_loop 异步任务里跑 — 不阻塞 WS receive.
        完成后清空 _tts_chunks 准备下一轮.
        """
        try:
            # 原子地取出所有 chunks，避免竞态
            with self._tts_lock:
                mp3_bytes = b"".join(self._tts_chunks)
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
            # Debug: save raw MP3 for inspection
            try:
                debug_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data', 'logs', 'tts-debug-last.mp3')
                os.makedirs(os.path.dirname(debug_path), exist_ok=True)
                with open(debug_path, 'wb') as _f:
                    _f.write(mp3_bytes)
                log.info("TTS MP3 saved to %s (%d bytes)", debug_path, len(mp3_bytes))
            except Exception as _e:
                log.warning("TTS debug save failed: %s", _e)

            # 解码 (FFmpeg backend, 需要 ffmpeg.exe 在 PATH 或 pydub 找得到)
            audio = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
            log.info(
                "TTS decoded: %dHz %dch %d-bit, %.2fs, %dB MP3",
                audio.frame_rate, audio.channels, audio.sample_width * 8,
                len(audio) / 1000.0, len(mp3_bytes),
            )
            # 重采样到 16kHz mono int16 (跟录音同)
            audio = audio.set_frame_rate(RATE).set_channels(1).set_sample_width(2)

            import numpy as _np
            samples = _np.frombuffer(audio.raw_data, dtype=_np.int16)
            duration_sec = len(samples) / RATE
            log.info(
                "TTS playing: %d samples (%.2fs @%dHz)",
                len(samples), duration_sec, RATE,
            )

            # Debug: save decoded PCM for inspection
            try:
                pcm_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data', 'logs', 'tts-debug-last.pcm')
                with open(pcm_path, 'wb') as _f:
                    _f.write(samples.tobytes())
                log.info("TTS PCM saved to %s (%d samples)", pcm_path, len(samples))
            except Exception as _e:
                log.warning("TTS PCM save failed: %s", _e)

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

                    # STT heartbeat (每 ~300次 ≈ 19s)
                    self._capture_hb_count += 1
                    if self._capture_hb_count >= HEARTBEAT_CAPTURE_INTERVAL:
                        self._capture_hb_count = 0
                        log_heartbeat("stt")
                        if self._agc_enabled:
                            log.debug("AGC: noise=%.0f gain=%.2f", self._agc_noise_floor, self._agc_gain)
                except Exception as exc:
                    log.warning("audio: stream.read failed: %s", exc)
                    break

                # data 已经是 bytes (raw int16), 直接喂 VAD
                raw_rms = self._rms(data)
                now = time.time()

                # AGC: 根据环境噪音更新增益
                gain = self._agc_update(raw_rms, self._speaking)
                # 应用增益后的数据用于 VAD 和缓冲
                if abs(gain - 1.0) > 0.01:
                    processed = self._apply_gain(data, gain)
                else:
                    processed = data
                rms = self._rms(processed) if abs(gain - 1.0) > 0.01 else raw_rms

                # C: TTS 串扰抑制 (哥哥 6-29 拍板)
                # TTS 播放时 VAD 阈值 +50%, 扬声器出声不误触发 mic
                effective_threshold = self.threshold
                if self._tts_playing:
                    effective_threshold = int(self.threshold * 1.5)

                if rms > effective_threshold:
                    # Voice detected
                    if not self._speaking:
                        # 语音活动开始 — 通知监控面板
                        log_event("voice_activity", "detected")
                    self._buffer.extend(processed)
                    self._last_audio_ts = now
                    # A: 标记用户最近说话, TTS worker 用来打断旧答
                    self._user_spoke_recently = now
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
                        self._buffer.extend(processed)  # keep trailing silence
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
        """Send accumulated audio buffer as binary PCM to bridge (SenseVoice STT)."""
        if len(self._buffer) < int(RATE * MIN_AUDIO_MS / 1000 * 2):
            self._buffer.clear()
            self._speaking = False
            return
        audio = bytes(self._buffer)
        self._buffer.clear()
        self._speaking = False

        # 发送 PCM binary 到桥端 (bridge SenseVoice 做 STT + 纠错 + LLM)
        if self._ws:
            try:
                coro = self._ws.send(audio)  # bytes → binary WS frame
                asyncio.run_coroutine_threadsafe(coro, self._aio_loop)
                duration = len(audio) / (RATE * 2)
                log.debug("audio sent: %d bytes (%.1fs)", len(audio), duration)
            except Exception as exc:
                log.warning("audio WS send failed: %s", exc)
        else:
            log.info("offline: %d bytes audio dropped", len(audio))

        self._emit_state("LISTENING")
    def start(self):
        if self._running:
            return
        self._running = True
        self._capture_hb_count = 0  # STT heartbeat counter
        self._ws_connected = False
        log_module_status("stt", "running")
        log_module_status("tts", "running")
        self._ws_thread = threading.Thread(target=self._run_ws, daemon=True)
        self._ws_thread.start()
        time.sleep(0.5)  # Let WS connect first
        self._capture_thread = threading.Thread(target=self._capture, daemon=True)
        self._capture_thread.start()
        log.info("audio: engine started (bridge-side SenseVoice STT)")

    def _run_ws(self):
        self._aio_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._aio_loop)
        # A: 初始化 TTS 队列 + 启动单 worker (哥哥 6-29 拍板)
        self._tts_queue = asyncio.Queue(maxsize=3)
        # 注意: create_task 必须 await, 这里用 ensure_future 同步提交
        asyncio.ensure_future(self._tts_play_worker(), loop=self._aio_loop)
        try:
            self._aio_loop.run_until_complete(self._ws_loop())
        finally:
            self._aio_loop.close()

    async def _tts_play_worker(self):
        """A: TTS 单 worker 顺序播放 — 排队 + 打断检查.

        设计:
        - 顺序消费 _tts_queue (FIFO)
        - 每段 mp3 播前检查 _user_spoke_recently (3s 内说话就打断当前)
        - 播完继续下一个, 不阻塞 WS loop
        - 任何一段失败 (pydub 解码 etc) 跳过不中断 worker
        """
        log.info("[TTS worker] started")
        _tts_hb = 0
        while self._running:
            try:
                mp3_bytes = await asyncio.wait_for(
                    self._tts_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                # TTS heartbeat (每 ~15次超时 ≈ 15s idle)
                _tts_hb += 1
                if _tts_hb >= HEARTBEAT_TTS_INTERVAL:
                    _tts_hb = 0
                    log_heartbeat("tts")
                continue
            except Exception as exc:
                log.warning("[TTS worker] queue get failed: %s", exc)
                continue

            if not mp3_bytes:
                continue

            # 打断检查: 用户 3s 内说话, 跳过这答 (让位给新对话)
            now = time.time()
            if (now - self._user_spoke_recently) < 3.0:
                log.info("[TTS worker] user spoke recently, skip this reply")
                continue

            self._tts_playing = True
            try:
                # B: 优先查缓存 (同 text+voice 直接拿 mp3)
                # 注意: 缓存 key 是 mp3 内容 hash, 这里 mp3 已生成, 直接播
                await asyncio.get_event_loop().run_in_executor(
                    None, self.play_mp3_bytes, mp3_bytes,
                )
            except Exception as exc:
                log.error("[TTS worker] play failed: %s", exc)
            finally:
                self._tts_playing = False
        log.info("[TTS worker] stopped")

    def stop(self):
        if self._running:
            log_module_status("stt", "stopped")
            log_module_status("tts", "stopped")
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