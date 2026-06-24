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
import io
import json
import logging
import os
import struct
import time
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


# ---- Lazy STT (OpenAI Whisper API) ----

def _get_whisper_client():
    """Lazy import of OpenAI client for Whisper API."""
    try:
        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return None
        return OpenAI(api_key=api_key)
    except ImportError:
        return None


async def _transcribe(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """Send audio to OpenAI Whisper API and return text."""
    client = _get_whisper_client()
    if client is None:
        return ""

    try:
        # Write audio to a temporary file-like object
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = f"voice.{mime_type.split('/')[-1].split(';')[0] or 'mp3'}"

        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(
            None,
            lambda: client.audio.transcriptions.create(
                model=_WHISPER_MODEL,
                file=audio_file,
                language="zh",
                response_format="text",
            )
        )
        return str(transcript).strip()
    except Exception as exc:
        logger.error("Whisper API error: %s", exc)
        return ""


# ---- Lazy TTS (edge-tts streaming) ----

async def _stream_tts(text: str, voice: str = _DEFAULT_TTS_VOICE):
    """Stream TTS audio chunks from edge-tts.

    Yields bytes (mp3 audio chunks).
    """
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]
    except Exception as exc:
        logger.error("TTS error: %s", exc)
        yield b""


# ---- LLM call (reuse bridge's chat pipeline) ----

async def _llm_chat(text: str, session_id: str = "") -> str:
    """Send text to LLM and return response.

    Uses the bridge's internal chat pipeline if available, otherwise
    falls back to a direct API call.
    """
    # Try direct HTTP call to our own bridge
    import httpx
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:7860", timeout=_LLM_TIMEOUT) as client:
            resp = await client.post("/v1/chat/completions", json={
                "model": "auto",
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

    def __init__(self, session_id: str):
        self.session_id = session_id
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
                        session = VoiceSession(session_id)
                        await _send_json(websocket, {
                            "type": "status",
                            "message": f"会话 {session_id} 已开始，请说话"
                        })
                        logger.info("voice: session %s started", session_id)

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
        reply = await _llm_chat(text, session.session_id)
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
