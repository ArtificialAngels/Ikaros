#!/usr/bin/env python3
"""
Ikaros 监控事件日志 — 为 monitor_agent.py 提供结构化事件数据。

所有 WS 事件（STT、LLM 回答、AI 状态）通过此模块写入
data/logs/ikaros-monitor.jsonl，monitor_agent 通过 tail 实时读取。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
MONITOR_LOG = _ROOT / "data" / "logs" / "ikaros-monitor.jsonl"

_lock = threading.Lock()
_log = logging.getLogger("ikaros.monitor")


def log_event(event_type: str, text: str = "", **extra) -> None:
    """写一条结构化事件到 JSONL 文件。

    Args:
        event_type: 事件类型 (stt, llm_reply, state, status, neuro_state)
        text: 事件内容/文本
        extra: 额外字段 (如 state=thinking)
    """
    try:
        MONITOR_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "type": event_type,
            "text": text,
            **extra,
        }
        with _lock:
            with open(MONITOR_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        _log.debug("log_event FAILED: %s", exc)


def log_stt(text: str) -> None:
    """语音识别结果."""
    log_event("stt", text)


def log_llm_reply(text: str) -> None:
    """LLM 回答内容."""
    log_event("llm_reply", text)


def log_state(state: str) -> None:
    """AI 状态变化 (listening/thinking/speaking/idle)."""
    log_event("state", state, state=state)


def log_status(msg: str) -> None:
    """状态消息."""
    log_event("status", msg)


def log_error(msg: str) -> None:
    """错误消息."""
    log_event("error", msg)


def log_module_status(module: str, status: str) -> None:
    """模块启停状态 (stt/tts/voice_ws)."""
    log_event("module_status", status, module=module)


def log_heartbeat(module: str) -> None:
    """模块心跳 (stt/tts)."""
    log_event("heartbeat", module, module=module)


# ── 预定义 heartbeat 间隔 (由 audio_engine.py 使用) ──
# _capture loop: CHUNK=1024 @ 16kHz ⇒ 64ms/次, 300次≈19s
HEARTBEAT_CAPTURE_INTERVAL = 300
# TTS worker idle loop: timeout=1s, 15次≈15s
HEARTBEAT_TTS_INTERVAL = 15
