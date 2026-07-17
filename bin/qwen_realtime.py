# 详细说明见 docs/scripts/bin/qwen_realtime.md

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import AsyncIterator, Optional

logger = logging.getLogger("ikaros.realtime")

# Qwen Realtime API 配置
QWEN_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
QWEN_DEFAULT_MODEL = "qwen3-omni-flash-realtime-2025-09-04"


class QwenRealtimeClient:
    """Qwen Realtime API 客户端 — 一体式 STT+LLM.

    通过单个 WebSocket 发送 PCM 音频, 接收文本/音频回复。
    延迟: 首 token <500ms, 端到端 <1s (理论值)。
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        voice: str = "Cherry",
        instructions: str = "",
        input_audio_transcription: bool = True,
    ):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.model = model or os.environ.get("IKAROS_REALTIME_MODEL", QWEN_DEFAULT_MODEL)
        self.voice = voice
        self.instructions = instructions
        self.transcription = input_audio_transcription
        self._ws: "websockets.WebSocketClientProtocol | None" = None  # type: ignore
        self._receive_task: asyncio.Task | None = None
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._connected = False

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def connect(self) -> bool:
        """建立 WebSocket 连接并初始化会话."""
        if not self.api_key:
            logger.warning("QwenRealtime: DASHSCOPE_API_KEY not set")
            return False
        try:
            import websockets
            url = f"{QWEN_WS_URL}?model={self.model}"
            self._ws = await websockets.connect(
                url,
                additional_headers={"Authorization": f"Bearer {self.api_key}"},
                ping_interval=20,
                ping_timeout=10,
                max_size=2 ** 24,  # 16MB
            )
            # 发送 session.update
            session_config = {
                "type": "session.update",
                "session": {
                    "modalities": ["text"],
                    "instructions": self.instructions,
                    "voice": self.voice,
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_transcription": (
                        {"model": "gummy-realtime-v1"} if self.transcription else None
                    ),
                    "turn_detection": {"type": "server_vad"},
                    "temperature": 0.7,
                    "max_response_output_tokens": 512,
                },
            }
            # 去除 None 值
            session_config["session"] = {
                k: v for k, v in session_config["session"].items()
                if v is not None
            }
            await self._ws.send(json.dumps(session_config))
            self._connected = True
            # 启动接收循环
            self._receive_task = asyncio.ensure_future(self._recv_loop())
            logger.info("QwenRealtime: connected (model=%s)", self.model)
            return True
        except Exception as e:
            logger.warning("QwenRealtime: connect failed: %s", e)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        self._connected = False
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def stream_audio(self, pcm_bytes: bytes) -> None:
        """发送音频帧 (16k mono Int16 PCM)."""
        if not self._connected or not self._ws:
            return
        import base64
        encoded = base64.b64encode(pcm_bytes).decode("ascii")
        await self._ws.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": encoded,
        }))

    async def commit_audio(self) -> None:
        """通知服务端音频输入完毕."""
        if not self._connected or not self._ws:
            return
        await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        await self._ws.send(json.dumps({"type": "response.create"}))

    async def cancel_response(self) -> None:
        """取消当前正在生成的回复 (barge-in)."""
        if not self._connected or not self._ws:
            return
        await self._ws.send(json.dumps({"type": "response.cancel"}))

    async def receive(self) -> AsyncIterator[dict]:
        """异步生成器: yield 每个事件 (text/audio/done/error)."""
        while self._connected:
            try:
                event = await asyncio.wait_for(
                    self._response_queue.get(), timeout=30
                )
                yield event
                if event.get("type") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                yield {"type": "error", "message": "timeout"}
                break

    async def _recv_loop(self) -> None:
        """后台接收循环."""
        import websockets
        while self._connected and self._ws:
            try:
                raw = await self._ws.recv()
                event = json.loads(raw)
                ev_type = event.get("type", "")
                if ev_type == "session.updated":
                    logger.debug("QwenRealtime: session updated")
                elif ev_type == "input_audio_buffer.speech_started":
                    await self._response_queue.put({"type": "speech_started"})
                elif ev_type == "input_audio_buffer.speech_stopped":
                    await self._response_queue.put({"type": "speech_stopped"})
                elif ev_type == "conversation.item.input_audio_transcription.completed":
                    self._response_queue.put_nowait({
                        "type": "transcription",
                        "text": event.get("transcript", ""),
                    })
                elif ev_type == "response.text.delta":
                    self._response_queue.put_nowait({
                        "type": "text_delta",
                        "text": event.get("delta", ""),
                    })
                elif ev_type == "response.audio.delta":
                    import base64
                    audio_b64 = event.get("delta", "")
                    if audio_b64:
                        self._response_queue.put_nowait({
                            "type": "audio_delta",
                            "audio": base64.b64decode(audio_b64),
                        })
                elif ev_type == "response.done":
                    self._response_queue.put_nowait({"type": "done"})
                elif ev_type == "error":
                    err = event.get("error", {})
                    logger.warning("QwenRealtime error: %s", err.get("message", ""))
                    self._response_queue.put_nowait({
                        "type": "error",
                        "message": err.get("message", "unknown"),
                    })
                else:
                    logger.debug("QwenRealtime: unhandled event %s", ev_type)
            except websockets.ConnectionClosed:
                logger.info("QwenRealtime: connection closed")
                break
            except Exception as e:
                logger.debug("QwenRealtime: recv error: %s", e)
                break
        self._connected = False
