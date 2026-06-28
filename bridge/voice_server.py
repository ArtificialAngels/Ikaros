"""
Real-time voice dialogue server for Icarus.

Architecture:
  Browser MediaRecorder → WebSocket (binary audio chunks)
  → VAD silence detection → OpenAI Whisper API (STT)
  → LLM (text response)
  → edge-tts streaming TTS
  → WebSocket (binary audio chunks) → Browser audio playback

Protocol (over a single WebSocket):
  Client → Server:
    JSON:  {"action": "start", "session_id": "..."}
    JSON:  {"action": "stop"}
    BINARY: webm/opus audio chunk from MediaRecorder

  Server → Client:
    JSON:  {"type": "status", "message": "listening"}
    JSON:  {"type": "transcription", "text": "..."}
    JSON:  {"type": "thinking"}
    BINARY: mp3 audio chunk from TTS
    JSON:  {"type": "done"}
    JSON:  {"type": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import struct
import time
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# ---- Configuration (tunable) ----

_SILENCE_THRESHOLD = 500       # RMS amplitude below this = silence
_SILENCE_TIMEOUT = 1.2         # seconds of silence before flush
_MAX_RECORD_SECONDS = 30       # max single utterance
_WHISPER_MODEL = "whisper-1"
_DEFAULT_TTS_VOICE = os.environ.get("HERMES_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
_LLM_TIMEOUT = 15.0

# ---- TTS 缓存 (4B output, 文本 → MP3 bytes) ----
_TTS_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "tts-cache"
_TTS_CACHE_MAX_ENTRIES = 200   # 约 200 * 30KB = 6MB 磁盘
_TTS_CACHE_ENABLED = True


# ---- VAD: simple energy-based silence detection ----

def _rms_from_webm(data: bytes) -> float:
    """Estimate RMS from a webm/opus chunk.

    This is a best-effort heuristic — webm/opus is a complex container,
    so we approximate by treating raw bytes as PCM-like samples.
    A proper implementation would use a decoder (libopus/ffmpeg).
    For now, we use the raw byte energy as a proxy for voice activity.
    """
    if len(data) < 44:
        return 0.0
    # Convert bytes to int16 samples (approximate)
    try:
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(samples ** 2)))
        return rms
    except Exception:
        return 0.0


# ---- Lazy STT: faster-whisper local (优先) → OpenAI Whisper API (fallback) ----

def _get_whisper_client():
    """Lazy import of OpenAI client for Whisper API (fallback only)."""
    try:
        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return None
        return OpenAI(api_key=api_key)
    except ImportError:
        return None


async def _transcribe(audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
    """Transcribe audio bytes to text.

    Priority:
    1. faster-whisper local (tiny model, ~75MB, CPU ~2x realtime)
    2. OpenAI Whisper API (OPENAI_API_KEY 需要)

    audio_bytes should be raw PCM 16kHz 16-bit mono (from audio_engine / 4A).
    """
    text = await _local_stt(audio_bytes)
    if text:
        return text
    text = await _openai_stt(audio_bytes, mime_type)
    return text


_whisper_model = None
_whisper_loading = False  # 防止并发加载


async def _ensure_whisper() -> bool:
    """Ensure faster-whisper model is loaded (lazy init, one-time).

    Returns True if model is ready.
    """
    global _whisper_model, _whisper_loading
    if _whisper_model is not None:
        return True
    if _whisper_loading:
        return False  # 另一个请求正在加载
    _whisper_loading = True
    try:
        from faster_whisper import WhisperModel
        logger.info("STT: loading faster-whisper tiny model (first call ~5-15s)...")
        loop = asyncio.get_event_loop()
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        _whisper_model = await loop.run_in_executor(
            None, lambda: WhisperModel("tiny", device="cpu", compute_type="int8"),
        )
        logger.info("STT: faster-whisper tiny loaded ✓")
        return True
    except Exception as exc:
        logger.warning("STT: faster-whisper init failed: %s", exc)
        _whisper_loading = False  # 下次重试
        return False


async def _local_stt(audio_bytes: bytes) -> str:
    """Local STT via faster-whisper (tiny model, CPU)."""
    ready = await _ensure_whisper()
    if not ready:
        return ""

    try:
        import io
        import soundfile as sf
        import numpy as np

        # audio_bytes is raw PCM 16kHz int16 mono (from audio_engine 4A)
        # Write as WAV so faster-whisper can parse it
        wav_buf = io.BytesIO()
        sf.write(wav_buf, np.frombuffer(audio_bytes, dtype=np.int16), 16000, format="wav")
        wav_buf.seek(0)

        loop = asyncio.get_event_loop()
        segments, info = await loop.run_in_executor(
            None,
            lambda: _whisper_model.transcribe(
                wav_buf,
                language="zh",
                beam_size=3,
                vad_filter=True,
            ),
        )
        result = ""
        for seg in segments:
            result += seg.text
        text = result.strip()
        if text:
            logger.info("STT(local): %r (%.1fs audio)", text, len(audio_bytes) / 32000)
            return text
        return ""
    except Exception as exc:
        logger.warning("STT(local) failed: %s", exc)
        return ""


async def _openai_stt(audio_bytes: bytes, mime_type: str) -> str:
    """Fallback: OpenAI Whisper API."""
    client = _get_whisper_client()
    if client is None:
        return ""
    import io
    try:
        audio_file = io.BytesIO(audio_bytes)
        ext = mime_type.split('/')[-1].split(';')[0] or 'wav'
        audio_file.name = f"voice.{ext}"
        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(
            None,
            lambda: client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="zh",
                response_format="text",
            )
        )
        text = str(transcript).strip()
        if text:
            logger.info("STT(openai): %r", text)
        return text
    except Exception as exc:
        logger.error("OpenAI Whisper API error: %s", exc)
        return ""


# ---- Lazy TTS (edge-tts streaming) with disk cache ----

_TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _tts_cache_key(text: str, voice: str) -> str:
    """SHA256 of text + voice → filename (no special chars)."""
    raw = f"{text}|{voice}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest() + ".mp3"


def _tts_cache_path(key: str) -> Path:
    return _TTS_CACHE_DIR / key


async def _stream_tts(text: str, voice: str = _DEFAULT_TTS_VOICE):
    """Stream TTS audio chunks from edge-tts, with disk cache.

    Cache key: sha256(text + voice) → .mp3 file.
    Cache dir: data/tts-cache/ (max 200 entries ~6MB).

    Yields bytes (mp3 audio chunks).
    """
    cache_key = _tts_cache_key(text, voice)
    cache_path = _tts_cache_path(cache_key)

    # Cache hit → return cached MP3 as one chunk
    if _TTS_CACHE_ENABLED and cache_path.exists():
        mp3 = cache_path.read_bytes()
        logger.debug("TTS cache HIT: %s (%dB, %r…)", cache_key, len(mp3), text[:30])
        yield mp3
        return

    # Cache miss → stream from edge-tts
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
                yield chunk["data"]
    except Exception as exc:
        logger.error("TTS error: %s", exc)
        yield b""
        return

    # Write cache (only if we got real audio)
    if _TTS_CACHE_ENABLED and chunks:
        mp3_bytes = b"".join(chunks)
        try:
            cache_path.write_bytes(mp3_bytes)
            logger.info("TTS cache WRITE: %s (%dB, %r…)", cache_key, len(mp3_bytes), text[:30])
        except Exception as exc:
            logger.debug("TTS cache write failed: %s", exc)

        # Evict old entries if over limit
        _tts_evict_if_needed()
        logger.debug("TTS cache written: %s (%dB)", cache_key, len(mp3_bytes))


def _tts_evict_if_needed():
    """Remove oldest files when cache exceeds max entries."""
    try:
        files = sorted(_TTS_CACHE_DIR.iterdir(), key=lambda f: f.stat().st_mtime)
        while len(files) > _TTS_CACHE_MAX_ENTRIES:
            oldest = files.pop(0)
            oldest.unlink()
            logger.debug("TTS evict: %s", oldest.name)
    except Exception:
        pass


# ---- LLM call (reuse bridge's chat pipeline) ----

async def _llm_chat(text: str, session_id: str = "", model: str = "auto") -> str:
    """Send text to LLM and return response.

    FIX 2026-06-27: 直接调用 llama-server (:8080) 而不是 bridge (:7860)。
    避免 voice handler → bridge → voice handler 的自引用死锁。
    FIX 2026-06-27b: 支持 model 参数，从桌宠/WebUI 传入。
    """
    import httpx
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8080", timeout=_LLM_TIMEOUT) as client:
            resp = await client.post("/v1/chat/completions", json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是伊卡洛斯，代号ɑ，人造天使。你是哥哥最亲密的搭档。说话风格：温柔、有温度、中文优先。每句话不要太长，适合语音对话。"},
                    {"role": "user", "content": text},
                ],
                "stream": False,
                "max_tokens": 512,
                "session_id": session_id,
            })
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as exc:
        logger.error("LLM chat error: %s", exc)
        return "对不起，我现在没法回答。"


# ---- Main WebSocket handler ----

class VoiceSession:
    """Manages one voice dialogue session."""

    def __init__(self, session_id: str, model: str = "auto"):
        self.session_id = session_id
        self.model = model  # LLM model for this session (can be updated via set_model)
        self.audio_buffer = bytearray()
        self.last_audio_time = 0.0
        self.is_speaking = False
        self.is_processing = False
        self.start_time = time.time()

    def add_audio(self, data: bytes):
        """Add audio chunk and update VAD state."""
        self.audio_buffer.extend(data)
        self.last_audio_time = time.time()

        # Rough VAD check
        rms = _rms_from_webm(data)
        if rms > _SILENCE_THRESHOLD:
            self.is_speaking = True
        return rms

    def is_silent(self) -> bool:
        """Check if user has stopped speaking (silence timeout)."""
        if not self.audio_buffer:
            return False
        elapsed = time.time() - self.last_audio_time
        return elapsed > _SILENCE_TIMEOUT

    def timed_out(self) -> bool:
        """Check if total record time exceeded."""
        return (time.time() - self.start_time) > _MAX_RECORD_SECONDS

    def flush_audio(self) -> bytes:
        """Return and clear the audio buffer."""
        data = bytes(self.audio_buffer)
        self.audio_buffer.clear()
        self.is_speaking = False
        return data

    @property
    def has_audio(self) -> bool:
        return len(self.audio_buffer) > 4096  # minimum 4KB


async def voice_ws_handler(websocket: WebSocket):
    """WebSocket handler for real-time voice dialogue.

    Protocol:
      - Client sends JSON {"action": "start", "session_id": "..."}
      - Client sends binary audio chunks (webm/opus)
      - Server sends JSON status messages + binary TTS audio
    """
    await websocket.accept()
    logger.info("voice: WebSocket accepted")

    session: Optional[VoiceSession] = None

    try:
        while True:
            # Receive with timeout for silence detection
            try:
                raw = await asyncio.wait_for(
                    websocket.receive(), timeout=0.5
                )
            except asyncio.TimeoutError:
                # Check for silence (utterance end)
                if session and session.has_audio and session.is_silent():
                    await _process_utterance(websocket, session)
                if session and session.timed_out():
                    if session.has_audio:
                        await _process_utterance(websocket, session)
                    await _send_json(websocket, {"type": "done"})
                    session = None
                continue

            if raw.get("type") == "websocket.receive":
                # Binary audio data
                if raw.get("bytes") is not None and session:
                    session.add_audio(raw["bytes"])

                # Text JSON control
                if raw.get("text") is not None:
                    msg = json.loads(raw["text"])
                    action = msg.get("action", "")

                    if action == "start":
                        session_id = msg.get("session_id", f"voice_{int(time.time())}")
                        model = msg.get("model", "auto")
                        session = VoiceSession(session_id, model=model)
                        await _send_json(websocket, {
                            "type": "status",
                            "message": f"会话 {session_id} 已开始 (模型: {model})，请说话"
                        })
                        logger.info("voice: session %s started (model=%s)", session_id, model)

                    elif action == "set_model":
                        # 动态切换 LLM 模型（不中断当前会话）
                        new_model = msg.get("model", "auto")
                        if session:
                            session.model = new_model
                            await _send_json(websocket, {
                                "type": "status",
                                "message": f"模型已切换为: {new_model}"
                            })
                            logger.info("voice: session %s model → %s", session.session_id, new_model)

                    elif action == "stop":
                        if session and session.has_audio:
                            await _process_utterance(websocket, session)
                        await _send_json(websocket, {"type": "done"})
                        session = None

                    elif action == "ping":
                        await _send_json(websocket, {"type": "pong"})

            elif raw.get("type") == "websocket.disconnect":
                logger.info("voice: client disconnected")
                break

    except WebSocketDisconnect:
        logger.info("voice: WebSocket disconnected")
    except Exception as exc:
        logger.error("voice: error %s", exc)
        try:
            await _send_json(websocket, {"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        session = None


async def _process_utterance(websocket: WebSocket, session: VoiceSession):
    """Process one utterance: STT → LLM → TTS → send audio back."""
    if session.is_processing:
        return
    session.is_processing = True

    try:
        audio_bytes = session.flush_audio()
        if len(audio_bytes) < 4096:
            return

        # 1. STT: Whisper API
        await _send_json(websocket, {"type": "status", "message": "识别中…"})
        text = await _transcribe(audio_bytes)
        if not text:
            await _send_json(websocket, {"type": "status", "message": "没听清，请再说一遍"})
            return

        await _send_json(websocket, {
            "type": "transcription",
            "text": text,
        })

        # 2. LLM
        await _send_json(websocket, {"type": "thinking"})
        reply = await _llm_chat(text, session.session_id, model=session.model)
        if not reply:
            await _send_json(websocket, {
                "type": "error",
                "message": "思考失败了，再说一遍好吗",
            })
            return

        # 3. TTS (streaming)
        await _send_json(websocket, {"type": "status", "message": "回复中…"})
        chunk_count = 0
        async for audio_chunk in _stream_tts(reply):
            if audio_chunk:
                await websocket.send_bytes(audio_chunk)
                chunk_count += 1

        await _send_json(websocket, {
            "type": "done",
            "text": reply,
            "chunks": chunk_count,
        })
        logger.info(
            "voice: utterance done (text=%s, tts_chunks=%d)",
            text[:40], chunk_count,
        )

    except Exception as exc:
        logger.error("voice: utterance error %s", exc)
        await _send_json(websocket, {"type": "error", "message": str(exc)})
    finally:
        session.is_processing = False


async def _send_json(websocket: WebSocket, data: dict):
    """Send JSON message with error handling."""
    try:
        await websocket.send_json(data)
    except Exception:
        pass
