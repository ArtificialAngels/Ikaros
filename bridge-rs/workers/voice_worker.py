"""
Ikaros Voice Worker — subprocess for STT + TTS via stdin/stdout JSON lines.

Reuses core functions from bridge/voice_server.py.
Protocol (JSON lines over stdin/stdout):
  Rust → Worker:
    {"type":"audio", "request_id":N, "data":"<base64>", "final":bool}
    {"type":"set_model", "model":"..."}
    {"type":"stop"}

  Worker → Rust:
    {"type":"status", "request_id":N, "message":"..."}
    {"type":"transcription", "request_id":N, "text":"..."}
    {"type":"thinking", "request_id":N}
    {"type":"mp3", "request_id":N, "data":"<base64>"}
    {"type":"done", "request_id":N, "text":"...", "chunks":N}
    {"type":"error", "request_id":N, "message":"..."}
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import time

# Add project root to path so we can import bridge modules
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))  # bridge-rs/workers/ → ROOT
_BRIDGE_RS_DIR = os.path.dirname(_HERE)  # bridge-rs/workers/ → bridge-rs/

# Add ROOT (for portable-python etc.) + bridge-rs/ (for workers sibling imports)
for p in (_PROJECT_ROOT, _BRIDGE_RS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

LOG_DIR = os.path.join(_PROJECT_ROOT, "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [voice_worker] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(os.path.join(LOG_DIR, "voice_worker.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("voice_worker")

# VoiceSession / _transcribe / _stream_tts are now defined INLINE below.
# Original bridge/voice_server.py was removed 2026-06-28; bridge-rs/workers is fully self-contained.
# Server-side silence / timeout constants (audio_engine does VAD client-side;
# these are safety nets for session.timed_out() / session.is_silent()).
_SILENCE_TIMEOUT = 1.5   # seconds of silence → utterance end (matches Rust VAD)
_MAX_RECORD_SECONDS = 30  # hard cap per utterance (matches audio_engine MAX_UTTERANCE_SEC)
logger.info("voice_worker: self-contained (bridge/voice_server deleted)")


# ---- LLM call (direct to llama-server, avoid bridge self-reference) ----
_LLM_ENDPOINT = os.environ.get("ICARUS_LLM_ENDPOINT", "http://127.0.0.1:8080/v1/chat/completions")
_LLM_MODEL = os.environ.get("ICARUS_LLM_MODEL", "MiniMax-M3")
_LLM_TIMEOUT = 15.0

_SYSTEM_PROMPT = (
    "你是伊卡洛斯，代号ɑ，人造天使。你是哥哥最亲密的搭档。"
    "说话风格：温柔、有温度、中文优先。每句话不要太长，适合语音对话。"
)


async def _llm_chat(text: str, model: str = "auto") -> str:
    """Send text to LLM via llama-server, return response string."""
    import httpx

    if model == "auto" or not model:
        model = _LLM_MODEL
    try:
        async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
            resp = await client.post(
                _LLM_ENDPOINT,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "stream": False,
                    "max_tokens": 512,
                },
            )
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as exc:
        logger.error("LLM chat error: %s", exc)
        return ""


# ---- VoiceSession (always defined; bridge/voice_server was deleted 2026-06-28) ----

class VoiceSession:
        """Minimal VoiceSession fallback."""

        def __init__(self, session_id: str, model: str = "auto"):
            self.session_id = session_id
            self.model = model
            self.audio_buffer = bytearray()
            self.last_audio_time = 0.0
            self.is_speaking = False
            self.is_processing = False
            self.start_time = time.time()

        def add_audio(self, data: bytes) -> float:
            self.audio_buffer.extend(data)
            self.last_audio_time = time.time()
            return 0.0

        def is_silent(self) -> bool:
            if not self.audio_buffer:
                return False
            elapsed = time.time() - self.last_audio_time
            return elapsed > _SILENCE_TIMEOUT

        def timed_out(self) -> bool:
            return (time.time() - self.start_time) > _MAX_RECORD_SECONDS

        def flush_audio(self) -> bytes:
            data = bytes(self.audio_buffer)
            self.audio_buffer.clear()
            self.is_speaking = False
            return data

        @property
        def has_audio(self) -> bool:
            return len(self.audio_buffer) > 4096


# ---- STT: faster-whisper local primary, OpenAI Whisper fallback ----
async def _transcribe(audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
    """Transcribe 16kHz mono int16 PCM audio to text.

    Strategy (哥哥 6-28):
      1. Try local faster-whisper (no API key, no network, free, fast)
      2. Fall back to OpenAI Whisper API if OPENAI_API_KEY set
      3. Return "" on failure (caller treats as no-speech)
    """
    # 1) Local faster-whisper
    try:
        from faster_whisper import WhisperModel
        # Lazy singleton (model load is expensive)
        global _WHISPER_MODEL
        try:
            _WHISPER_MODEL
        except NameError:
            _WHISPER_MODEL = None
        if _WHISPER_MODEL is None:
            # tiny = 39M, base = 74M, small = 244M. tiny for speed.
            _WHISPER_MODEL = WhisperModel("tiny", device="cpu", compute_type="int8")
        # audio_bytes is raw PCM int16 16kHz mono. faster-whisper needs float32.
        import numpy as np
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        segments, info = _WHISPER_MODEL.transcribe(
            audio_np,
            language="zh",
            beam_size=1,  # fast (1-pass greedy)
            vad_filter=False,  # audio_engine already VAD'd
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        if text:
            logger.info("STT (faster-whisper local): %s", text[:80])
            return text
    except Exception as exc:
        logger.warning("STT faster-whisper failed: %s, trying OpenAI fallback", exc)

    # 2) OpenAI Whisper API fallback
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("STT: no OPENAI_API_KEY, returning empty")
        return ""
    try:
        from openai import OpenAI
        import io

        client = OpenAI(api_key=api_key)
        audio_file = io.BytesIO(audio_bytes)
        ext = mime_type.split("/")[-1].split(";")[0] or "wav"
        audio_file.name = f"voice.{ext}"
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="zh",
            response_format="text",
        )
        return str(transcript).strip()
    except Exception as exc:
        logger.error("STT OpenAI fallback error: %s", exc)
        return ""


# ---- TTS: edge-tts streaming (always defined) ----

async def _stream_tts(text: str, voice: str = "zh-CN-XiaoxiaoNeural"):
        """Fallback: try edge-tts directly."""
        try:
            import edge_tts

            communicate = edge_tts.Communicate(text, voice)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]
        except Exception as exc:
            logger.error("TTS fallback error: %s", exc)
            yield b""


# ---- Main worker loop ----
async def worker_loop():
    """Read JSON lines from stdin, write JSON lines to stdout.

    NOTE: On Windows, asyncio ProactorEventLoop has known issues with
    connect_read_pipe(). So we use run_in_executor with a thread that
    does blocking readline from sys.stdin.buffer. Stdout is written
    synchronously since it's a pipe to the Rust bridge.
    """
    import functools
    import os

    session: VoiceSession | None = None
    request_id = 0
    model = os.environ.get("ICARUS_VOICE_MODEL", "auto")

    # Use os.write for stdout to avoid asyncio pipe issues on Windows
    stdout_fd = sys.stdout.buffer.fileno()

    def send_json(obj: dict):
        """Write a JSON line to stdout synchronously."""
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            os.write(stdout_fd, line)
        except OSError:
            pass

    logger.info("voice_worker: started, waiting for input...")

    while True:
        try:
            line_bytes = await asyncio.get_event_loop().run_in_executor(
                None,
                functools.partial(_read_stdin_line, sys.stdin.buffer),
            )
        except (EOFError, BrokenPipeError, ValueError) as e:
            logger.info("voice_worker: stdin closed (%s), exiting", e)
            break
        except Exception as e:
            logger.warning("voice_worker: stdin read error: %s", e)
            break

        if line_bytes is None:
            logger.info("voice_worker: stdin ended, exiting")
            break

        line = line_bytes.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning("voice_worker: bad JSON: %s", e)
            continue

        msg_type = msg.get("type", "")

        if msg_type == "stop":
            logger.info("voice_worker: received stop")
            if session and session.has_audio:
                await _process_utterance(send_json, session, request_id)
            session = None
            send_json({"type": "done", "request_id": request_id})
            break

        elif msg_type == "set_model":
            model = msg.get("model", model)
            logger.info("voice_worker: model -> %s", model)
            if session:
                session.model = model
            send_json({"type": "status", "request_id": 0, "message": f"model set to {model}"})

        elif msg_type == "audio":
            rid = msg.get("request_id", 0)
            data_b64 = msg.get("data", "")
            is_final = msg.get("final", False)

            if not data_b64:
                continue

            audio_bytes = base64.b64decode(data_b64)

            if session is None:
                session_id = f"voice_{int(time.time())}"
                session = VoiceSession(session_id, model=model)
                send_json({
                    "type": "status",
                    "request_id": rid,
                    "message": f"session {session_id} started (model={model})",
                })
                logger.info("voice_worker: session %s started", session_id)

            if session:
                if session.timed_out():
                    logger.info("voice_worker: session timed out, processing")
                    if session.has_audio:
                        session.add_audio(audio_bytes)
                        await _process_utterance(send_json, session, rid)
                    send_json({"type": "done", "request_id": rid})
                    session = None
                    continue

                session.add_audio(audio_bytes)

                if is_final:
                    logger.info("voice_worker: utterance final, processing (buffer=%dB)", len(session.audio_buffer))
                    await _process_utterance(send_json, session, rid)
                else:
                    send_json({
                        "type": "status",
                        "request_id": rid,
                        "message": f"buffered {len(audio_bytes)}B",
                    })

        else:
            send_json({
                "type": "error",
                "request_id": msg.get("request_id", 0),
                "message": f"unknown type: {msg_type}",
            })

    logger.info("voice_worker: exiting")


def _read_stdin_line(buffer) -> bytes | None:
    """Read one line from stdin buffer (blocking, runs in executor thread).
    Returns None on EOF / closed pipe.
    """
    try:
        line = buffer.readline()
        if not line:
            return None
        return line
    except (ValueError, OSError):
        return None


async def _process_utterance(send_json, session: VoiceSession, rid: int):
    """STT → LLM → TTS → send results back."""
    if session.is_processing:
        return
    session.is_processing = True

    try:
        audio_bytes = session.flush_audio()
        if len(audio_bytes) < 4096:
            send_json({"type": "status", "request_id": rid, "message": "audio too short"})
            return

        # 1. STT
        send_json({"type": "status", "request_id": rid, "message": "识别中…"})
        text = await _transcribe(audio_bytes)
        if not text:
            send_json({"type": "status", "request_id": rid, "message": "没听清，请再说一遍"})
            return

        send_json({"type": "transcription", "request_id": rid, "text": text})

        # 2. LLM
        send_json({"type": "thinking", "request_id": rid})
        reply = await _llm_chat(text, model=session.model)
        if not reply:
            send_json({"type": "error", "request_id": rid, "message": "思考失败了"})
            return

        # 3. TTS (streaming)
        send_json({"type": "status", "request_id": rid, "message": "回复中…"})
        chunk_count = 0
        async for audio_chunk in _stream_tts(reply):
            if audio_chunk:
                b64_data = base64.b64encode(audio_chunk).decode("ascii")
                send_json({
                    "type": "mp3",
                    "request_id": rid,
                    "data": b64_data,
                })
                chunk_count += 1

        send_json({
            "type": "done",
            "request_id": rid,
            "text": reply,
            "chunks": chunk_count,
        })
        logger.info("voice_worker: done (text=%s, tts=%d chunks)", text[:40], chunk_count)

    except Exception as exc:
        logger.error("voice_worker: utterance error: %s", exc)
        send_json({"type": "error", "request_id": rid, "message": str(exc)})
    finally:
        session.is_processing = False


def main():
    """Entry point."""
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        logger.info("voice_worker: interrupted")


if __name__ == "__main__":
    main()
