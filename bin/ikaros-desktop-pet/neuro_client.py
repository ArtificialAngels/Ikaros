"""
🪶 neuro_client.py — 去桥版 (2026-07-02)

不再轮询 bridge /v1/neuro/status. 状态由 audio_engine.on_state 直驱 Live2D.
PATIENCE 用本地 QTimer 实现: 对话安静 30s 后自动触发主动发言.

去桥后 NeuroClient 定位:
  - 状态驱动: audio_engine.on_state 已直连 Live2D (see main.py:1623)
  - PATIENCE: 本地 QTimer (无需 bridge)
  - 记忆: 由 cloud_chat 调用 Hermes Agent 记忆, 不再走 Neuro memory
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional, Callable

log = logging.getLogger("ikaros.neuro")


class NeuroClient:
    """去桥版 NeuroClient — 本地 PATIENCE 管理 + 状态转发.

    不再依赖 bridge HTTP 接口。状态变化由外部 (audio_engine) 通过
    on_status_change 回调推入。
    """

    POLL_INTERVAL = 1.0  # 秒 (保留兼容)

    def __init__(self, on_status_change: Optional[Callable[[dict], None]] = None):
        self.on_status_change = on_status_change
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_status: dict = {
            "state": "idle",
            "ai_state": "idle",
            "patience": 30.0,
            "last_active": time.time(),
            "source": "local",
        }
        self._patience_seconds: float = 30.0
        self._last_active: float = time.time()
        self._last_patience_warning: float = 0.0

    # ─── 状态更新 (由外部推入) ───

    def set_state(self, state: str):
        """从外部 (audio_engine.on_state / chat dock) 推入状态变化."""
        status = {
            "state": state.lower(),
            "ai_state": state.lower(),
            "patience": self._patience_seconds,
            "last_active": time.time(),
            "source": "local",
        }
        if status != self._last_status:
            self._last_status = status
            if self.on_status_change:
                try:
                    self.on_status_change(status)
                except Exception as e:
                    log.debug("neuro callback error: %s", e)
            self._patience_reset()

    def _patience_reset(self):
        """有对话活动时重置 PATIENCE 计时."""
        self._last_active = time.time()

    # ─── 兼容原 API (不再调 bridge) ───

    def set_patience(self, seconds: float) -> bool:
        """调整 PATIENCE 阈值 (仅本地, 不调 bridge)."""
        self._patience_seconds = max(5.0, min(600.0, float(seconds)))
        log.info("neuro (local): patience set to %.1fs", self._patience_seconds)
        return True

    def trigger_patience(self) -> bool:
        """手动触发 PATIENCE - 让 AI 主动说话 (去桥后空操作)."""
        log.info("neuro (local): PATIENCE trigger (not implemented in 去桥模式)")
        return True

    def reset_signals(self) -> bool:
        """重置说话标志 (去桥后直接重置计时)."""
        self._patience_reset()
        return True

    def get_memories(self, limit: int = 10) -> list[dict]:
        """浏览记忆 (去桥后返回空 — 记忆由 Hermes Agent 管理)."""
        return []

    def add_memory(self, document: str, importance: int = 5) -> bool:
        """手动注入一条记忆 (去桥后空操作 — 用 cloud_chat 带记忆)."""
        log.info("neuro (local): add_memory skipped (use cloud_chat for memory)")
        return True

    # ─── 轮询线程 (保留兼容, 实际上不需要) ───

    def start(self):
        """启动 1Hz 轮询线程 (去桥后只维护本地 PATIENCE)."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="NeuroPoll")
        self._thread.start()
        log.info("neuro (local): started (去桥: 本地 PATIENCE, 不轮询 bridge)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _poll_loop(self):
        while self._running:
            try:
                # PATIENCE 检查
                now = time.time()
                elapsed = now - self._last_active
                if elapsed > self._patience_seconds:
                    # PATIENCE 超时 — 如果 30s 内没报过, 报一次
                    if (now - self._last_patience_warning) > 30.0:
                        log.info("neuro (local): PATIENCE timeout (%.1fs > %.1fs)",
                                elapsed, self._patience_seconds)
                        self._last_patience_warning = now
                        # 推一个 bored 状态让 Live2D 切换
                        if self.on_status_change:
                            status = {
                                "state": "bored",
                                "ai_state": "bored",
                                "patience": self._patience_seconds,
                                "last_active": self._last_active,
                                "source": "local",
                            }
                            try:
                                self.on_status_change(status)
                            except Exception:
                                pass
                # 保持状态 active 时推心跳
                elif elapsed < 5.0:
                    if self.on_status_change:
                        status = {
                            "state": "idle",
                            "ai_state": self._last_status.get("ai_state", "idle"),
                            "patience": self._patience_seconds,
                            "last_active": self._last_active,
                            "source": "local",
                        }
                        if status["ai_state"] != "bored":
                            try:
                                self.on_status_change(status)
                            except Exception:
                                pass
            except Exception as e:
                log.debug("neuro (local) poll error: %s", e)
            time.sleep(self.POLL_INTERVAL)

    # ─── Derived state helpers ───

    @property
    def last_status(self) -> dict:
        return self._last_status.copy()

    @property
    def ai_state(self) -> str:
        return self._last_status.get("ai_state", "idle")

    @property
    def history_len(self) -> int:
        return 0  # 去桥后记忆由 Hermes Agent 管理
