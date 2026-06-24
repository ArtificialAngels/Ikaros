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
    QActionGroup, QApplication, QMainWindow, QMenu, QSystemTrayIcon, QWidget, QVBoxLayout,
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
    def __init__(self, window: PetWindow, bridge: SignalBridge, audio_engine):
        self.window = window
        self.bridge = bridge
        self.audio = audio_engine

        # Icon
        icon = QPixmap(64, 64)
        icon.fill(Qt.GlobalColor.transparent)
        p = QPainter(icon)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Feather icon
        p.setBrush(Qt.GlobalColor.darkCyan)
        p.drawEllipse(8, 8, 48, 48)
        p.setPen(Qt.GlobalColor.white)
        p.setFont(self._font())
        p.drawText(icon.rect(), Qt.AlignmentFlag.AlignCenter, "ɑ")
        p.end()

        self.tray = QSystemTrayIcon(QIcon(icon), parent=window)
        self.tray.setToolTip("🪶 伊卡洛斯 · 待机中")

        self._build_menu()

    def _font(self):
        from PyQt6.QtGui import QFont
        f = QFont("Segoe UI", 28, QFont.Weight.Bold)
        return f

    def _build_menu(self):
        menu = QMenu()

        menu.addAction("🪶 显示/隐藏", self._toggle_visible)
        menu.addSeparator()

        # Mode group
        self._mode_group = QActionGroup(menu)
        self._mode_group.setExclusive(True)

        self._continuous_action = QAction(
            "🎤 连续对话模式", checkable=True, checked=True
        )
        self._wake_action = QAction(
            "🔑 唤醒词模式", checkable=True
        )

        self._continuous_action.triggered.connect(lambda: self._set_mode("continuous"))
        self._wake_action.triggered.connect(lambda: self._set_mode("wake"))

        self._mode_group.addAction(self._continuous_action)
        self._mode_group.addAction(self._wake_action)

        menu.addAction(self._continuous_action)
        menu.addAction(self._wake_action)
        menu.addSeparator()

        # Wake word submenu (only active in wake mode)
        self._wake_menu = QMenu("🔑 唤醒词")
        self._wake_menu.setEnabled(False)
        for w in self.audio.wake_words:
            action = self._wake_menu.addAction(f"{w}")
            action.setCheckable(True)
            action.setChecked(True)
        self._wake_menu.addSeparator()
        self._wake_menu.addAction("➕ 添加唤醒词…")
        menu.addMenu(self._wake_menu)

        # Sensitivity
        sens_menu = menu.addMenu("🎚️ 麦克风灵敏度")
        for label, val in [("高 (安静环境)", 200), ("中 (默认)", 400), ("低 (嘈杂)", 800)]:
            a = sens_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(val == self.audio.threshold)
            a.triggered.connect(lambda checked, v=val: self._set_threshold(v))

        menu.addSeparator()
        menu.addAction("💤 隐藏", self._sleep)
        menu.addAction("❌ 退出", self._quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activate)
        self.tray.show()

    def _set_mode(self, mode: str):
        if mode == "continuous":
            self.audio.continuous_mode = True
            self.audio.wake_word_enabled = False
            self._wake_menu.setEnabled(False)
            self.tray.setToolTip("🪶 伊卡洛斯 · 连续对话")
        else:
            self.audio.continuous_mode = False
            self.audio.wake_word_enabled = True
            self._wake_menu.setEnabled(True)
            self.tray.setToolTip("🪶 伊卡洛斯 · 唤醒模式")

    def _set_threshold(self, val: int):
        self.audio.threshold = val

    def _toggle_visible(self):
        self.window.setVisible(not self.window.isVisible())

    def _sleep(self):
        self.window.hide()

    def _quit(self):
        QApplication.quit()

    def _on_activate(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_visible()

    def update_status(self, text: str):
        self.tray.setToolTip(f"🪶 伊卡洛斯 · {text}")


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


# ─── Audio Engine — imports the module-level AudioEngine ───

# The real AudioEngine is in audio_engine.py
# Imported lazily to avoid dependency issues


# ─── Main App ───

class IcarusApp:
    def __init__(self):
        self.bridge = SignalBridge()
        self.window = PetWindow(self.bridge)
        self.audio = None
        self.tray = None

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
        log.info("🪶 Icarus Desktop Pet running")

        # Start AudioEngine (lazy import so pyaudio doesn't block startup)
        from audio_engine import AudioEngine
        self.audio = AudioEngine()
        self.audio.on_state = self._on_state
        self.audio.on_bubble = self._on_bubble
        self.audio.start()

        # Create tray with audio reference
        self.tray = PetTray(self.window, self.bridge, self.audio)

        # Start context engine
        self._context = ContextThread(self.bridge)
        self._context.start()

        self.window.show()
        return QApplication.exec()

    def cleanup(self):
        if self.audio:
            self.audio.stop()
        if hasattr(self, '_context'):
            self._context.stop()


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
