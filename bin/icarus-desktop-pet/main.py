"""
🪶 Icarus Desktop Pet — PyQt6 Edition
Always-on-top transparent window, SVG chibi Ikaros, system tray, voice + context.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, pyqtSignal, QObject
from PyQt6.QtGui import QAction, QIcon, QPainter, QPixmap
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QMenu, QSystemTrayIcon, QWidget, QVBoxLayout,
)

# Paths
HERE = Path(__file__).parent
CHARACTER_SVG = HERE / "character.svg"
HERMES_ROOT = HERE.parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [icarus] %(message)s")
log = logging.getLogger("icarus")


# ─── Communication bridge (thread-safe) ───

class SignalBridge(QObject):
    state_changed = pyqtSignal(str)
    bubble_shown = pyqtSignal(str, int)
    context_changed = pyqtSignal(str)


# ─── Pet Window ───

class PetWindow(QMainWindow):
    WIDTH, HEIGHT = 260, 320

    def __init__(self, bridge: SignalBridge):
        super().__init__()
        self.bridge = bridge
        self._drag_pos = QPoint()
        self._is_dragging = False
        self._current_state = "idle"

        # Window setup
        self.setWindowTitle("🪶")
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Central widget
        central = QWidget()
        central.setStyleSheet("background: transparent;")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # SVG widget
        self.svg = QSvgWidget(str(CHARACTER_SVG))
        self.svg.setFixedSize(200, 280)
        self.svg.setStyleSheet("background: transparent;")
        layout.addWidget(self.svg, 0, Qt.AlignmentFlag.AlignCenter)

        # Position: center of screen (first run) or last position
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                (geo.width() - self.WIDTH) // 2,
                (geo.height() - self.HEIGHT) // 2,
            )

    # ─── Drag support ───
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._is_dragging = True

    def mouseMoveEvent(self, event):
        if self._is_dragging:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._is_dragging = False

    def mouseDoubleClickEvent(self, event):
        # Toggle between compact/normal
        log.info("pet: double-click (placeholder)")

    def set_state(self, state: str):
        self._current_state = state.lower()
        self.svg.renderer().setViewBox(self._svg_viewbox_for_state(state.lower()))
        self.svg.update()

    def _svg_viewbox_for_state(self, state: str) -> QRect:
        # Different viewbox regions for different states (if spritesheet)
        return QRect(0, 0, 200, 280)


# ─── System Tray ───

class PetTray:
    def __init__(self, window: PetWindow, bridge: SignalBridge):
        self.window = window
        self.bridge = bridge

        # Create a simple icon (colored dot)
        icon = QPixmap(32, 32)
        icon.fill(Qt.GlobalColor.transparent)
        p = QPainter(icon)
        p.setBrush(Qt.GlobalColor.darkCyan)
        p.drawEllipse(4, 4, 24, 24)
        p.end()

        self.tray = QSystemTrayIcon(QIcon(icon), parent=window)
        self.tray.setToolTip("🪶 伊卡洛斯")

        # Menu
        menu = QMenu()
        menu.addAction("🪶 显示/隐藏", self._toggle_visible)
        menu.addAction("💤 休眠", self._sleep)
        menu.addSeparator()
        menu.addAction("❌ 退出", self._quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activate)
        self.tray.show()

    def _toggle_visible(self):
        self.window.setVisible(not self.window.isVisible())

    def _sleep(self):
        self.window.hide()

    def _quit(self):
        QApplication.quit()

    def _on_activate(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_visible()


# ─── Context Engine (threaded) ───

class ContextThread(threading.Thread):
    def __init__(self, bridge: SignalBridge):
        super().__init__(daemon=True)
        self.bridge = bridge
        self._running = True

    def run(self):
        try:
            import win32gui, win32process
        except ImportError:
            log.warning("context: win32api not available")
            return

        last_tag = None
        while self._running:
            try:
                hwnd = win32gui.GetForegroundWindow()
                if hwnd:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    import psutil
                    try:
                        proc = psutil.Process(pid)
                        name = proc.name()
                    except Exception:
                        name = "?"

                    tag = self._classify(name)
                    if tag != last_tag:
                        last_tag = tag
                        self.bridge.context_changed.emit(tag)
            except Exception:
                pass
            time.sleep(1.5)

    def _classify(self, name: str) -> str:
        n = name.lower()
        if any(g in n for g in ['game', 'steam', 'diablo', 'wow', 'league']):
            return "Game"
        if n in ['code.exe', 'cursor.exe', 'pycharm64.exe', 'vscode.exe']:
            return "Coding"
        if n in ['chrome.exe', 'msedge.exe', 'firefox.exe']:
            return "Browser"
        if n in ['excel.exe', 'winword.exe', 'powerpnt.exe', 'wps.exe']:
            return "Office"
        return "Other"

    def stop(self):
        self._running = False


# ─── Audio Engine (threaded) ───

class AudioThread(threading.Thread):
    def __init__(self, bridge: SignalBridge):
        super().__init__(daemon=True)
        self.bridge = bridge
        self._running = True

    def run(self):
        import asyncio
        asyncio.run(self._ws_loop())

    async def _ws_loop(self):
        import asyncio
        try:
            import websockets
        except ImportError:
            log.warning("audio: websockets not available")
            return

        # Bypass proxy
        for k in list(os.environ.keys()):
            if 'proxy' in k.lower():
                os.environ.pop(k, None)

        uri = "ws://127.0.0.1:7860/v1/voice/ws"
        while self._running:
            try:
                async with websockets.connect(uri, proxy=None) as ws:
                    await ws.send(json.dumps({"action": "start", "session_id": "icarus_desktop"}))
                    self.bridge.state_changed.emit("LISTENING")
                    self.bridge.bubble_shown.emit("🎤 我在听~", 2000)

                    while self._running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=0.3)
                            if isinstance(msg, bytes):
                                pass  # TTS audio — would play
                            else:
                                data = json.loads(msg)
                                t = data.get("type", "")
                                if t == "transcription":
                                    self.bridge.bubble_shown.emit(data.get("text", ""), 3000)
                                elif t == "thinking":
                                    self.bridge.state_changed.emit("THINKING")
                                elif t == "status":
                                    self.bridge.bubble_shown.emit(data.get("message", ""), 2000)
                                elif t == "done":
                                    self.bridge.bubble_shown.emit(data.get("text", "嗯~"), 5000)
                                    self.bridge.state_changed.emit("SPEAKING")
                                    await asyncio.sleep(2)
                                    self.bridge.state_changed.emit("LISTENING")
                                    self.bridge.bubble_shown.emit("🎤 继续~", 2000)
                        except asyncio.TimeoutError:
                            continue
            except Exception as exc:
                log.warning("audio: WS error %s, retry in 3s", exc)
                await asyncio.sleep(3)

    def stop(self):
        self._running = False


# ─── Main App ───

class IcarusApp:
    def __init__(self):
        self.bridge = SignalBridge()
        self.window = PetWindow(self.bridge)
        self.tray = PetTray(self.window, self.bridge)
        self.audio = AudioThread(self.bridge)
        self.context = ContextThread(self.bridge)

        # Connect signals
        self.bridge.state_changed.connect(self._on_state)
        self.bridge.bubble_shown.connect(self._on_bubble)
        self.bridge.context_changed.connect(self._on_context)

    def _on_state(self, state: str):
        log.info("state → %s", state)
        self.window.set_state(state)

    def _on_bubble(self, text: str, duration: int):
        self.window.setWindowTitle(f"🪶 {text[:20]}")

    def _on_context(self, tag: str):
        log.info("context → %s", tag)
        if tag == "Game":
            self.window.setWindowTitle("🪶 👀 哥哥在打游戏")
        elif tag == "Coding":
            self.window.setWindowTitle("🪶 💻 哥哥写代码")
        elif tag == "Office":
            self.window.setWindowTitle("🪶 📝 哥哥在工作")
        else:
            self.window.setWindowTitle("🪶")

    def run(self):
        self.window.show()
        self.audio.start()
        self.context.start()
        log.info("🪶 Icarus Desktop Pet running")
        return QApplication.exec()

    def cleanup(self):
        self.audio.stop()
        self.context.stop()


def register_autostart():
    """Register desktop pet for Windows autostart."""
    try:
        import winreg
        python = sys.executable
        cmd = f'"{python}" "{HERE / "main.py"}"'
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "IcarusDesktopPet", 0,
                              winreg.REG_SZ, cmd)
        log.info("autostart: registered")
    except Exception as exc:
        log.warning("autostart: %s", exc)


def main():
    # Kill proxy env
    for k in list(os.environ.keys()):
        if 'proxy' in k.lower():
            os.environ.pop(k, None)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    register_autostart()

    pet = IcarusApp()
    rc = pet.run()
    pet.cleanup()
    sys.exit(rc)


if __name__ == "__main__":
    main()
