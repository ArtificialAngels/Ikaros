"""
Neuro Client — 桌宠 ↔ Neuro 桥接

桌面宠物通过 HTTP 短轮询 Neuro bridge 端点:
- /v1/neuro/status   : PATIENCE 计时、history_len、AI 状态
- /v1/neuro/patience : 调整沉默阈值
- /v1/neuro/patience/trigger : 手动触发 PATIENCE (让 AI 主动说话)
- /v1/neuro/memories : 浏览记忆
- /v1/neuro/reset    : 重置说话标志 (用于卡死恢复)

设计: 独立线程 1Hz 轮询, 把状态推到 Qt SignalBridge
Neuro 是 Neuro 主进程全局状态, 桌宠是观察者+触发器, 互不耦合.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional, Callable
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

log = logging.getLogger("ikaros.neuro")

# Neuro bridge base URL
NEURO_BASE = "http://127.0.0.1:7860"


class NeuroClient:
    """桌宠 → Neuro 集成. 1Hz 轮询 status, 把状态变化 emit 给桌宠."""

    POLL_INTERVAL = 1.0  # 秒

    def __init__(self, on_status_change: Optional[Callable[[dict], None]] = None):
        self.on_status_change = on_status_change
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_status: dict = {}
        self._patience_seconds: float = 30.0
        self._last_patience_warning: float = 0.0  # 防重复

    # ─── HTTP helpers ───

    def _http_get(self, path: str, timeout: float = 2.0) -> Optional[dict]:
        try:
            url = f"{NEURO_BASE}{path}"
            req = urlrequest.Request(url, method="GET")
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, json.JSONDecodeError, OSError) as e:
            log.debug("neuro GET %s failed: %s", path, e)
            return None

    def _http_post(self, path: str, body: dict | None = None, timeout: float = 4.0) -> Optional[dict]:
        try:
            url = f"{NEURO_BASE}{path}"
            data = json.dumps(body or {}).encode("utf-8")
            req = urlrequest.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, json.JSONDecodeError, OSError) as e:
            log.warning("neuro POST %s failed: %s", path, e)
            return None

    # ─── Public API ───

    def set_patience(self, seconds: float) -> bool:
        """调整 PATIENCE 阈值 (5-600s)."""
        seconds = max(5.0, min(600.0, float(seconds)))
        result = self._http_post("/v1/neuro/patience", {"seconds": seconds})
        if result and "patience" in result:
            self._patience_seconds = result["patience"]
            log.info("neuro: patience set to %.1fs", self._patience_seconds)
            return True
        return False

    def trigger_patience(self) -> bool:
        """手动触发 PATIENCE - 让 AI 主动说话."""
        result = self._http_post("/v1/neuro/patience/trigger")
        if result and result.get("triggered"):
            log.info("neuro: PATIENCE triggered manually")
            return True
        return False

    def reset_signals(self) -> bool:
        """重置说话标志 (卡死恢复)."""
        result = self._http_post("/v1/neuro/reset")
        return bool(result and result.get("reset"))

    def get_memories(self, limit: int = 10) -> list[dict]:
        """浏览记忆 (只返回最近 N 条)."""
        result = self._http_get(f"/v1/neuro/memories?limit={limit}")
        if result:
            return result.get("memories", [])
        return []

    def add_memory(self, document: str, importance: int = 5) -> bool:
        """手动注入一条记忆."""
        result = self._http_post(
            "/v1/neuro/memory/add",
            {"document": document, "metadata": {"type": "manual", "importance": importance}},
        )
        return bool(result and result.get("id"))

    # ─── Polling loop ───

    def start(self):
        """启动 1Hz 轮询线程."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="NeuroPoll")
        self._thread.start()
        log.info("neuro client started (1Hz poll)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _poll_loop(self):
        while self._running:
            try:
                status = self._http_get("/v1/neuro/status", timeout=1.5)
                if status:
                    # 关键状态变化才回调, 减少 Qt signal 风暴
                    if status != self._last_status:
                        if self.on_status_change:
                            try:
                                self.on_status_change(status)
                            except Exception as e:
                                log.debug("neuro callback error: %s", e)
                        self._last_status = status
                    self._patience_seconds = status.get("patience", 30.0)
            except Exception as e:
                log.debug("neuro poll error: %s", e)
            time.sleep(self.POLL_INTERVAL)

    # ─── Derived state helpers ───

    @property
    def last_status(self) -> dict:
        return self._last_status.copy()

    @property
    def patience(self) -> float:
        return self._patience_seconds

    @property
    def time_since_last(self) -> float:
        return self._last_status.get("time_since_last_message", 0.0)

    @property
    def patience_progress(self) -> float:
        """0..1, 越接近 1 越接近 PATIENCE 触发. 用于桌宠表情."""
        if self._patience_seconds <= 0:
            return 0.0
        return min(1.0, self.time_since_last / self._patience_seconds)

    @property
    def ai_state(self) -> str:
        """推导 AI 状态 (用于桌宠 character 切换)."""
        s = self._last_status
        if s.get("AI_thinking"):
            return "thinking"
        if s.get("AI_speaking"):
            return "speaking"
        if s.get("human_speaking"):
            return "listening"
        if self.patience_progress > 0.7:
            return "bored"  # 接近 PATIENCE
        return "idle"

    @property
    def history_len(self) -> int:
        return self._last_status.get("history_len", 0)