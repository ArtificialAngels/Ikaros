"""
Icarus Neuro Tray — Standalone system-tray indicator for Neuro state.

不嵌入 webui. 独立的 PyQt6 system tray icon, 显示 Neuro 状态:
- 托盘图标根据 AI 状态变色 (idle/bored/listening/thinking/speaking)
- 右键菜单: 触发 PATIENCE / 调阈值 / 重置 / 看记忆
- PATIENCE > 85% 时弹 Windows 气泡通知
- 双击托盘图标 = 触发 PATIENCE (让 AI 主动说话)

启动: 双击 start_neuro_tray.bat
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Callable

from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap, QFont
from PyQt6.QtWidgets import (
    QApplication, QMenu, QSystemTrayIcon, QMessageBox, QInputDialog,
)

HERE = Path(__file__).parent
NEURO_BASE = "http://127.0.0.1:7860"
POLL_INTERVAL = 1.0  # 秒
PATIENCE_ALERT_THRESHOLD = 0.85  # 超过 85% 弹通知

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [neuro-tray] %(message)s",
    stream=sys.stderr,
    force=True,
)
log = logging.getLogger("neuro-tray")


# ─── HTTP helper (similar to neuro_client.py but standalone) ───

def http_get(path: str, timeout: float = 2.0) -> Optional[dict]:
    from urllib import request as urlrequest
    from urllib.error import URLError, HTTPError
    try:
        req = urlrequest.Request(f"{NEURO_BASE}{path}", method="GET")
        with urlrequest.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log.debug("GET %s: %s", path, e)
        return None


def http_post(path: str, body: Optional[dict] = None, timeout: float = 4.0) -> Optional[dict]:
    from urllib import request as urlrequest
    from urllib.error import URLError, HTTPError
    try:
        data = json.dumps(body or {}).encode("utf-8")
        req = urlrequest.Request(
            f"{NEURO_BASE}{path}", data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlrequest.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log.warning("POST %s: %s", path, e)
        return None


# ─── Neuro state polling (background thread) ───

class NeuroPoller(QObject):
    """1Hz poll → emit Qt signal on state change."""
    status_updated = pyqtSignal(dict)
    patience_alert = pyqtSignal(float)  # 新一轮 PATIENCE 接近触发

    def __init__(self):
        super().__init__()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_status: dict = {}
        self._last_alert_ts: float = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="NeuroPoller")
        self._thread.start()
        log.info("NeuroPoller started (1Hz)")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            status = http_get("/v1/neuro/status", timeout=1.5)
            if status:
                self.status_updated.emit(status)
                # PATIENCE 接近触发 → 弹通知 (防抖: 60s 内最多一次)
                t = status.get("time_since_last_message", 0.0)
                p = status.get("patience", 30.0)
                if p > 0 and (t / p) >= PATIENCE_ALERT_THRESHOLD:
                    now = time.time()
                    if now - self._last_alert_ts > 60:
                        self._last_alert_ts = now
                        self.patience_alert.emit(t)
                self._last_status = status
            else:
                # bridge 不可达 — 发空状态让 tray 显示离线
                self.status_updated.emit({"_offline": True})
            time.sleep(POLL_INTERVAL)


# ─── Tray icon renderer ───

COLORS = {
    "offline":   QColor("#64748b"),  # 灰
    "idle":      QColor("#4facfe"),  # 蓝
    "listening": QColor("#a78bfa"),  # 紫
    "thinking":  QColor("#fbbf24"),  # 黄
    "speaking":  QColor("#43e97b"),  # 绿
    "bored":     QColor("#f5576c"),  # 红 (PATIENCE 接近)
}


def render_icon(state: str, size: int = 64) -> QIcon:
    """绘制 ɑ 字符 + 颜色背景."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    color = COLORS.get(state, COLORS["offline"])
    p.setBrush(color)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, size - 8, size - 8)

    # ɑ character
    p.setPen(QColor("white"))
    font = QFont("Segoe UI", int(size * 0.5), QFont.Weight.Bold)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "ɑ")
    p.end()

    return QIcon(pix)


def derive_state(status: dict) -> str:
    """从 /v1/neuro/status 推导 AI state."""
    if status.get("_offline"):
        return "offline"
    if status.get("AI_thinking"):
        return "thinking"
    if status.get("AI_speaking"):
        return "speaking"
    if status.get("human_speaking"):
        return "listening"
    t = status.get("time_since_last_message", 0.0)
    p = status.get("patience", 30.0)
    if p > 0 and (t / p) > 0.7:
        return "bored"
    return "idle"


# ─── Main app ───

class NeuroTrayApp:
    def __init__(self):
        self.tray: Optional[QSystemTrayIcon] = None
        self.poller = NeuroPoller()
        self._current_state = "offline"
        self._patience_seconds = 30.0
        self._last_status: dict = {}
        self._last_alert_ts: float = 0.0

        # Create tray
        self.tray = QSystemTrayIcon()
        self.tray.setIcon(render_icon("offline"))
        self.tray.setToolTip("Neuro · 离线")
        self.tray.activated.connect(self._on_tray_activated)
        self._build_menu()

        # Connect poller signals (queued connection for thread safety)
        self.poller.status_updated.connect(self._on_status, Qt.ConnectionType.QueuedConnection)
        self.poller.patience_alert.connect(self._on_patience_alert, Qt.ConnectionType.QueuedConnection)

    def _build_menu(self):
        menu = QMenu()

        # Status (non-clickable info)
        self._status_action = QAction("⚪ 离线", menu)
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)

        menu.addSeparator()

        # Trigger PATIENCE (let her speak)
        act_trigger = QAction("💬 让伊卡洛斯主动说话", menu)
        act_trigger.triggered.connect(self._trigger_patience)
        menu.addAction(act_trigger)

        # PATIENCE threshold submenu
        patience_menu = menu.addMenu("⏱️ PATIENCE 阈值")
        self._patience_group = QActionGroup(patience_menu)
        self._patience_group.setExclusive(True)
        for label, sec in [("15s (敏感)", 15), ("30s (默认)", 30), ("60s (慢热)", 60), ("120s (极慢)", 120)]:
            a = QAction(label, patience_menu)
            a.setCheckable(True)
            a.setData(sec)
            a.triggered.connect(lambda checked, s=sec: self._set_patience(s))
            self._patience_group.addAction(a)
            patience_menu.addAction(a)

        menu.addSeparator()

        # Reset
        act_reset = QAction("🔄 重置说话标志", menu)
        act_reset.triggered.connect(self._reset_signals)
        menu.addAction(act_reset)

        # View memories
        act_mem = QAction("🧠 看记忆…", menu)
        act_mem.triggered.connect(self._show_memories)
        menu.addAction(act_mem)

        # Add memory
        act_add = QAction("📝 加一条记忆…", menu)
        act_add.triggered.connect(self._add_memory)
        menu.addAction(act_add)

        menu.addSeparator()

        # Show bridge health
        act_health = QAction("🔗 查看 Neuro 状态…", menu)
        act_health.triggered.connect(self._show_status)
        menu.addAction(act_health)

        menu.addSeparator()

        # Quit
        act_quit = QAction("❌ 退出", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)

    # ─── Slot handlers ───

    def _on_status(self, status: dict):
        """Poller pushed new status."""
        self._last_status = status
        state = derive_state(status)
        if state != self._current_state:
            self._current_state = state
            self.tray.setIcon(render_icon(state))

        # Update tooltip
        if status.get("_offline"):
            tooltip = "Neuro · 离线 (bridge :7860 不可达)"
            self._status_action.setText("⚪ 离线")
        else:
            t = status.get("time_since_last_message", 0.0)
            p = status.get("patience", 30.0)
            h = status.get("history_len", 0)
            tooltip = f"Neuro · {state}\nPATIENCE {t:.1f}/{p:.0f}s\n记忆 {h} 条"
            self._status_action.setText(
                f"{'🟢' if state != 'offline' else '⚪'} {state.upper()} · {t:.1f}s / {p:.0f}s"
            )
        self.tray.setToolTip(tooltip)

        # Update patience menu checkmarks
        self._patience_seconds = status.get("patience", 30.0)
        for act in self._patience_group.actions():
            v = act.data()
            act.setChecked(v is not None and abs(v - self._patience_seconds) < 1)

    def _on_patience_alert(self, t: float):
        """PATIENCE 接近触发 → 气泡通知."""
        if self.tray and self.tray.supportsMessages():
            self.tray.showMessage(
                "伊卡洛斯想说话了",
                f"已沉默 {t:.0f} 秒, 哥哥要不要让她说点什么?\n(右键菜单 → 💬 触发)",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )

    def _on_tray_activated(self, reason):
        """双击 = 触发 PATIENCE."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._trigger_patience()

    # ─── Action handlers ───

    def _trigger_patience(self):
        result = http_post("/v1/neuro/patience/trigger")
        if result and result.get("triggered"):
            log.info("PATIENCE triggered")
            if self.tray and self.tray.supportsMessages():
                self.tray.showMessage(
                    "伊卡洛斯", "💬 主动说话触发成功",
                    QSystemTrayIcon.MessageIcon.Information, 2000,
                )
        else:
            QMessageBox.warning(None, "Neuro", "触发失败 — Neuro bridge 不可达")

    def _set_patience(self, seconds: float):
        result = http_post("/v1/neuro/patience", {"seconds": float(seconds)})
        if result and "patience" in result:
            log.info("PATIENCE → %.0fs", result["patience"])
            self.tray.setToolTip(f"Neuro · PATIENCE {result['patience']:.0f}s")

    def _reset_signals(self):
        result = http_post("/v1/neuro/reset")
        if result and result.get("reset"):
            log.info("Neuro reset")

    def _show_memories(self):
        result = http_get("/v1/neuro/memories?limit=20")
        if not result:
            QMessageBox.warning(None, "Neuro", "Neuro bridge 不可达")
            return
        mems = result.get("memories", [])
        if not mems:
            QMessageBox.information(None, "Neuro 记忆", "(空)")
            return
        text = "\n".join(
            f"[{m.get('metadata', {}).get('type', '?')}] {m.get('document', '')}"
            for m in reversed(mems)
        )
        QMessageBox.information(None, f"Neuro · {len(mems)} 条记忆", text)

    def _add_memory(self):
        text, ok = QInputDialog.getText(None, "Neuro · 加记忆", "伊卡洛斯要记住的:")
        if ok and text.strip():
            result = http_post("/v1/neuro/memory/add", {
                "document": text.strip(),
                "metadata": {"type": "manual", "importance": 7},
            })
            if result and result.get("id"):
                log.info("memory added: %s", result["id"])

    def _show_status(self):
        s = self._last_status
        if not s:
            QMessageBox.information(None, "Neuro", "尚未收到状态")
            return
        text = "\n".join(f"{k}: {v}" for k, v in s.items())
        QMessageBox.information(None, "Neuro 状态", text)

    def _quit(self):
        log.info("quit requested")
        self.poller.stop()
        QApplication.quit()

    # ─── Lifecycle ───

    def run(self):
        self.poller.start()
        self.tray.show()
        log.info("Neuro tray running")
        return QApplication.exec()


def main():
    # Kill proxy env (socks5 makes PyQt6 websockets crash)
    for k in list(os.environ.keys()):
        if "proxy" in k.lower():
            os.environ.pop(k, None)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 关闭窗口不退出

    tray = NeuroTrayApp()
    return tray.run()


if __name__ == "__main__":
    sys.exit(main())