"""
Ikaros Audio Engine — persistent microphone capture + TTS playback.

Architecture (去桥 v2, 2026-07-02):
  sounddevice capture thread (16000Hz, 16-bit mono)
  → energy-based VAD (RMS threshold + AGC)
  → silence detection → flush PCM
  → LOCAL faster-whisper tiny-int8 STT (in pet process)
  → cloud_chat() 直调 cloud LLM (带 soul + cogno 5D 注入)
  → edge-tts 本地 TTS → pydub decode → sounddevice OutputStream

不再依赖 bridge WS 连接。STT / LLM / TTS 全在本地进程完成。

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

# Local STT (faster-whisper tiny int8 — runs in pet process, bypasses bridge STT)
LOCAL_STT_MODEL = "tiny"  # ~75MB, fast CPU inference
LOCAL_STT_DEVICE = "cpu"
LOCAL_STT_COMPUTE = "int8"  # int8 quantization for speed
LOCAL_STT_LANG = "zh"          # Chinese primary, auto-detect fallback
LOCAL_STT_BEAM_SIZE = 3

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

# ─── 去桥后不再需要 WebSocket 连接 ───
# WS_URL 已移除，语音管线全部本地完成

class AudioEngine:
    """Persistent microphone capture with VAD and wake-word."""

    def __init__(self):
        self._running = False
        self._stream: Optional[sd.RawInputStream] = None
        self._buffer = bytearray()
        self._speaking = False
        self._last_audio_ts = 0.0
        self._utterance_start = 0.0

        # AGC state — 环境噪音自适应增益
        self._agc_noise_floor = 100.0    # 当前噪音底估计 (RMS)
        self._agc_gain = 1.0             # 当前增益倍数
        self._agc_enabled = True         # AGC 开关 (可从 tray 控制)

        # Local STT (faster-whisper lazy init)
        self._stt_model = None           # faster_whisper.WhisperModel instance
        self._stt_model_lock = threading.Lock()
        self._stt_available: Optional[bool] = None  # None=untested, True/False

        # 4B: TTS playback state — edge-tts 本地生成 MP3, 排队播放
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
        self._async_thread: Optional[threading.Thread] = None

    def set_model(self, model: str):
        """Set LLM model for voice. 去桥后只更新本地模型配置."""
        self._llm_model = model
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

    # ─── 语音管线: STT → cloud_chat → TTS (去桥后, 全本地) ───

    async def _process_and_reply(self, text: str):
        """在 async loop 中: cloud_chat → edge-tts → TTS 队列.

        此方法从 _flush() 通过 asyncio.run_coroutine_threadsafe 调用,
        运行在 _async_loop 线程中, 不阻塞 capture 线程.
        """
        if not text or not text.strip():
            return

        log.info("Voice STT: %s", text[:80])
        log_stt(text)
        self._emit_bubble(text, 3000)
        self._emit_state("THINKING")

        try:
            # 调 cloud LLM (带 soul + cogno 5D)
            from cloud_chat import cloud_chat
            reply = await cloud_chat(text, max_tokens=400)

            if not reply or not reply.strip():
                log.warning("cloud_chat returned empty reply")
                self._emit_state("LISTENING")
                return

            log_llm_reply(reply)
            self._emit_bubble(reply, 5000)
            self._emit_state("SPEAKING")

            # 本地 edge-tts TTS (用 main.py 的同款)
            from cloud_chat import _get_api_key, _load_env
            tts_text = reply
            # strip markdown emphasis (同 main.py _strip_markdown_emphasis)
            import re as _re
            tts_text = _re.sub(r'\*\*([^*]+)\*\*', r'\1', tts_text)
            tts_text = _re.sub(r'\*([^*]+)\*', r'\1', tts_text)
            tts_text = _re.sub(r'__([^_]+)__', r'\1', tts_text)

            try:
                import edge_tts
                communicate = edge_tts.Communicate(tts_text, voice="zh-CN-XiaoxiaoNeural")
                mp3_chunks = []
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        mp3_chunks.append(chunk["data"])
                mp3_bytes = b"".join(mp3_chunks)
            except Exception as exc:
                log.warning("TTS failed: %s — skipping audio", exc)
                mp3_bytes = b""

            if mp3_bytes and len(mp3_bytes) > 100:
                # 排队播放 (同原 TTS worker)
                if self._tts_queue is not None:
                    try:
                        self._tts_queue.put_nowait(mp3_bytes)
                    except asyncio.QueueFull:
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
                    self._tts_playing = True
                    self.play_mp3_bytes(mp3_bytes)
                    self._tts_playing = False

            # 回复后重新进入听状态
            await asyncio.sleep(0.5)
            self._emit_state("LISTENING")

        except Exception as exc:
            log.error("voice pipeline failed: %s", exc)
            self._emit_bubble(f"⚠️ {exc}", 4000)
            self._emit_state("LISTENING")

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

    def _ensure_stt_model(self):
        """Lazily load faster-whisper tiny-int8 model. Returns model or None."""
        if self._stt_available is True:
            return self._stt_model
        if self._stt_available is False:
            return None
        # First call — try to load
        with self._stt_model_lock:
            if self._stt_available is not None:
                return self._stt_model if self._stt_available else None
            try:
                from faster_whisper import WhisperModel
                log.info("Loading local STT: faster-whisper %s on %s", LOCAL_STT_MODEL, LOCAL_STT_DEVICE)
                self._stt_model = WhisperModel(
                    LOCAL_STT_MODEL,
                    device=LOCAL_STT_DEVICE,
                    compute_type=LOCAL_STT_COMPUTE,
                )
                self._stt_available = True
                log.info("Local STT loaded OK")
                return self._stt_model
            except Exception as exc:
                self._stt_available = False
                log.warning("Local STT unavailable (will fallback to bridge STT): %s", exc)
                return None

    def _do_local_stt_and_reply(self, pcm_bytes: bytes):
        """Run faster-whisper on PCM bytes → 文本 → 送入 async 管线做 LLM+TTS.

        Flow: PCM → faster-whisper → text → asyncio.run_coroutine_threadsafe(
            _process_and_reply(text), loop)
        """
        model = self._ensure_stt_model()
        if model is None:
            log.warning("Local STT unavailable, voice input dropped")
            return

        try:
            # faster-whisper expects numpy float32 array in [-1, 1]
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            segments, info = model.transcribe(
                samples,
                language=LOCAL_STT_LANG,
                beam_size=LOCAL_STT_BEAM_SIZE,
                vad_filter=True,
            )
            text = "".join(seg.text for seg in segments).strip()
            if not text:
                log.info("Local STT: empty result, skipping")
                return

            log.info("Local STT: %s (lang=%s, prob=%.2f)", text[:80], info.language, info.language_probability)

            # 送入 async loop 做 cloud_chat + TTS
            if self._aio_loop and self._aio_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._process_and_reply(text), self._aio_loop
                )
            else:
                log.warning("Async loop not running, voice reply dropped: %s", text[:40])

        except Exception as exc:
            log.warning("Local STT failed, voice input dropped: %s", exc)


    def _flush(self):
        """将捕获的音频做本地 STT → cloud_chat → TTS (去桥后, 不再走 WS).

        本地 faster-whisper STT → 文本 → async loop 中 cloud_chat + edge-tts.
        """
        if len(self._buffer) < int(RATE * MIN_AUDIO_MS / 1000 * 2):
            self._buffer.clear()
            self._speaking = False
            return
        audio = bytes(self._buffer)
        self._buffer.clear()
        self._speaking = False

        # 全本地: 本地 STT → cloud_chat → 本地 TTS
        self._do_local_stt_and_reply(audio)

        self._emit_state("LISTENING")

    def start(self):
        if self._running:
            return
        self._running = True
        self._capture_hb_count = 0  # STT heartbeat counter
        log_module_status("stt", "running")
        log_module_status("tts", "running")
        # 去桥: 不再启动 WS 线程, 改启动 async loop 线程做 LLM+TTS
        self._async_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._async_thread.start()
        time.sleep(0.5)  # 等 async loop 就绪
        self._capture_thread = threading.Thread(target=self._capture, daemon=True)
        self._capture_thread.start()
        log.info("audio: engine started (去桥: 本地 STT → cloud_chat → 本地 TTS)")

    def _run_async_loop(self):
        self._aio_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._aio_loop)
        # 初始化 TTS 队列 + 启动单 worker (哥哥 6-29 拍板)
        self._tts_queue = asyncio.Queue(maxsize=3)
        asyncio.ensure_future(self._tts_play_worker(), loop=self._aio_loop)
        try:
            # 去桥: 不再连 WS, loop 保持运行等待 _process_and_reply
            self._aio_loop.run_forever()
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