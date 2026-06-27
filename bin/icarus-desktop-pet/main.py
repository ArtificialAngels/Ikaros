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

from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, pyqtSignal, pyqtSlot, QObject, QUrl, QUrlQuery, QEvent
from PyQt6.QtGui import QAction, QActionGroup, QIcon, QPainter, QPixmap, QColor, QCursor
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage


class _Live2DPage(QWebEnginePage):
    """QWebEnginePage subclass that forwards JS console messages to Python log."""
    _log = logging.getLogger("icarus.live2d")

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        # Log ALL messages (including errors) so we can diagnose rendering issues
        if "error" in str(level).lower() or "⚠" in message or "✗" in message:
            self._log.error("[JS:%s] %s (line %d, %s)", level, message, lineNumber, sourceID)
        else:
            self._log.info("[JS:%s] %s (line %d)", level, message, lineNumber)
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QMenu, QSystemTrayIcon, QWidget, QVBoxLayout,
    QHBoxLayout, QLineEdit, QTextEdit, QPushButton, QLabel,
)

# 4C: HTTP bridge to Hermes bridge /v1/chat/completions
try:
    import httpx  # 异步 HTTP 客户端 (bridge_intent_router + chat 都会用)
except ImportError:
    httpx = None  # fallback: 用 urllib (同步)

# Paths
HERE = Path(__file__).parent
CHARACTER_SVG = HERE / "character.svg"
CHARACTER_PNG = HERE / "character.png"
LIVE2D_HTML = HERE / "live2d" / "index.html"

# Set to True to use Live2D (WebEngine), False for PNG/SVG rendering
USE_LIVE2D = True
# Live2D model key — matches MODELS array in index.html
# Options: haru, hiyori, senko, shizuku48, shizuku, xisitina
L2D_MODEL_KEY = "haru"
# Available models (maps key -> index in JS MODELS[])
L2D_AVAILABLE_MODELS = {
    "haru": 0, "hiyori": 1, "senko": 2,
    "shizuku48": 3, "shizuku": 4, "xisitina": 5,
}
L2D_EXPR_BY_STATE = {
    "idle": "idle", "listening": "relax",
    "thinking": "serious", "speaking": "happy", "bored": "sleep",
}
HERMES_ROOT = HERE.parent.parent

# 便携 Python 不自动加 cwd 到 sys.path (python312._pth 机制)
# 手动加, 让 from audio_engine import AudioEngine 等本地 import 能工作
sys.path.insert(0, str(HERE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [icarus] %(message)s")
log = logging.getLogger("icarus")

# 4C: IntentRouter (Layer 1 规则 — task/chat/ambiguous)
# 必须在 HERE + log 都定义之后, 否则 except 分支用 log 会 NameError
try:
    import sys as _sys
    _sys.path.insert(0, str(HERE.parent.parent))  # 让 bridge/ 可导入
    from bridge.intent_router import IntentRouter as _IntentRouter
except Exception as _exc:
    log.warning("IntentRouter import failed: %s — chat 会 fall back 到 LLM 隐式", _exc)
    _IntentRouter = None

# 4C: edge-tts (TTS) — ChatDockWindow 用它生成回复 MP3
try:
    import edge_tts  # Microsoft Edge neural TTS (offline API)
except ImportError:
    log.warning("edge-tts not installed — 4C chat 会用 fallback TTS (no playback)")
    edge_tts = None


# ─── Communication bridge (thread-safe) ───

class SignalBridge(QObject):
    state_changed = pyqtSignal(str)
    bubble_shown = pyqtSignal(str, int)
    context_changed = pyqtSignal(str)
    # Neuro signals
    neuro_state_changed = pyqtSignal(str)   # "idle" / "listening" / "thinking" / "speaking" / "bored"
    neuro_patience_changed = pyqtSignal(float)  # 当前 PATIENCE 值
    neuro_memory_added = pyqtSignal(str)     # 新记忆文本 (反射触发)


# ─── Drag Bridge (QWebChannel for JS → Python drag communication) ───

class _DragBridge(QObject):
    """QWebChannel bridge for JS → Python window drag communication.

    JS detects mouse events on the Live2D canvas and calls these methods
    to drag the window. This approach doesn't block wl-live2d's features.
    """
    DRAG_THRESHOLD = 5

    def __init__(self, pet_window: 'PetWindow'):
        super().__init__()
        self._pet_window = pet_window
        self._drag_start_pos = None  # global position at drag start
        self._window_start_pos = None  # window position at drag start
        self._is_dragging = False

    @pyqtSlot(int, int)  # onDragStart(globalX, globalY)
    def onDragStart(self, global_x: int, global_y: int):
        """JS calls this when mouse press detected."""
        self._drag_start_pos = QPoint(global_x, global_y)
        self._window_start_pos = self._pet_window.pos()
        self._is_dragging = False

    @pyqtSlot(int, int)  # onDragMove(globalX, globalY)
    def onDragMove(self, global_x: int, global_y: int):
        """JS calls this on mouse move."""
        if self._drag_start_pos is None:
            return
        current_pos = QPoint(global_x, global_y)
        delta = (current_pos - self._drag_start_pos).manhattanLength()
        if delta < self.DRAG_THRESHOLD:
            return  # Not a drag yet
        self._is_dragging = True
        # Move window by the delta from start
        offset = current_pos - self._drag_start_pos
        new_pos = self._window_start_pos + offset
        self._pet_window.move(new_pos)

    @pyqtSlot()  # onDragEnd()
    def onDragEnd(self):
        """JS calls this on mouse release."""
        self._drag_start_pos = None
        self._window_start_pos = None
        self._is_dragging = False

    @pyqtSlot(result=bool)  # isDragging()
    def isDragging(self) -> bool:
        """JS calls this to check if we're currently dragging."""
        return self._is_dragging

    @pyqtSlot(int, int)  # onContextMenu(screenX, screenY)
    def onContextMenu(self, screen_x: int, screen_y: int):
        """JS calls this on right-click. Show custom Qt context menu."""
        self._pet_window._show_context_menu(QPoint(screen_x, screen_y))


# ─── Pet Window ───

class PetWindow(QMainWindow):
    WIDTH, HEIGHT = 500, 500  # bigger for Live2D

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

        # Central widget
        central = QWidget()
        central.setStyleSheet("background: transparent;")
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # Character widget (Live2D WebEngine, or PNG fallback)
        from PyQt6.QtWidgets import QLabel

        if USE_LIVE2D and LIVE2D_HTML.exists():
            # Drag handle at top (30px)
            self._drag_handle = QWidget(central)
            self._drag_handle.setFixedSize(self.WIDTH, 30)
            self._drag_handle.setStyleSheet("background: transparent;")
            self._drag_handle.setCursor(Qt.CursorShape.OpenHandCursor)
            layout.addWidget(self._drag_handle, 0, Qt.AlignmentFlag.AlignTop)

            # ── Live2D view container ──
            self._live2d_container = QWidget(central)
            self._live2d_container.setFixedSize(self.WIDTH, self.HEIGHT - 40)
            self._live2d_container.setStyleSheet("background: transparent;")
            container_layout = QVBoxLayout(self._live2d_container)
            container_layout.setContentsMargins(0, 0, 0, 0)

            self._live2d_view = QWebEngineView(self._live2d_container)
            _page = _Live2DPage(self._live2d_view)
            self._live2d_view.setPage(_page)
            _ws = _page.settings()
            _ws.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
            _ws.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            _ws.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            self._live2d_view.setFixedSize(self.WIDTH, self.HEIGHT - 40)
            self._live2d_view.setStyleSheet("background: transparent; border: none;")
            # Disable built-in context menu (we handle it via JS → QWebChannel)
            self._live2d_view.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
            # FIX L2D-001: set page background to transparent (Qt side)
            # This is REQUIRED for the QWebEngineView to render with alpha channel.
            # Without this, the page has an opaque white/black background.
            _page.setBackgroundColor(QColor(0, 0, 0, 0))  # fully transparent
            self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
            _page.loadFinished.connect(self._on_live2d_loaded)
            _url = QUrl.fromLocalFile(str(LIVE2D_HTML))
            _query = QUrlQuery()
            _query.addQueryItem("model", L2D_MODEL_KEY)
            _url.setQuery(_query)
            _page.setUrl(_url)
            container_layout.addWidget(self._live2d_view)

            # ── QWebChannel for JS ↔ Python drag communication ──
            # JS detects mouse events on the canvas and calls Python via
            # QWebChannel to drag the window. This doesn't block wl-live2d's
            # built-in features (tips, menus, hit test).
            self._drag_bridge = _DragBridge(self)
            self._web_channel = QWebChannel()
            self._web_channel.registerObject("dragBridge", self._drag_bridge)
            _page.setWebChannel(self._web_channel)

            layout.addWidget(self._live2d_container, 0, Qt.AlignmentFlag.AlignCenter)
            self._character_label = None
        elif CHARACTER_PNG.exists():
            char_label = QLabel(central)
            char_label.setStyleSheet("background: transparent;")
            char_label.setFixedSize(self.WIDTH - 20, self.HEIGHT - 60)
            char_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap(str(CHARACTER_PNG))
            scaled = pixmap.scaled(
                char_label.width(),
                char_label.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            char_label.setPixmap(scaled)
            self._character_label = char_label
        else:
            # Fallback to SVG
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

        # Install event filter for drag handle
        if hasattr(self, '_drag_handle'):
            self._drag_handle.installEventFilter(self)

    # ─── Drag support (drag handle only; Live2D area uses _DragOverlay) ───
    def eventFilter(self, obj, event):
        """Handle mouse events on drag handle for window dragging."""
        if hasattr(self, '_drag_handle') and obj == self._drag_handle:
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    self._is_dragging = True
                    return True
            elif event.type() == QEvent.Type.MouseMove:
                if self._is_dragging:
                    self.move(event.globalPosition().toPoint() - self._drag_pos)
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._is_dragging = False
                return True
        return super().eventFilter(obj, event)

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
        if hasattr(self, 'svg') and self.svg:
            self.svg.renderer().setViewBox(self._svg_viewbox_for_state(state.lower()))
            self.svg.update()
        elif hasattr(self, '_live2d_view') and self._live2d_view:
            # Send state to Live2D via JS (使用新的 setState API)
            try:
                expr = L2D_EXPR_BY_STATE.get(state.lower(), "idle")
                self._live2d_view.page().runJavaScript(
                    f"window.setState && window.setState('{expr}')"
                )
            except Exception:
                pass

    def switch_live2d_model(self, key_or_index):
        """Switch Live2D model by key or index."""
        if not hasattr(self, '_live2d_view') or not self._live2d_view:
            return
        # Resolve key to index if needed
        if isinstance(key_or_index, str):
            idx = L2D_AVAILABLE_MODELS.get(key_or_index, 0)
        else:
            idx = int(key_or_index)
        try:
            self._live2d_view.page().runJavaScript(
                f"window.switchModel && window.switchModel({idx})"
            )
        except Exception as e:
            log.warning("switch_live2d_model: %s", e)

    def notify_live2d_tip(self, text: str):
        """Show a tip message on the Live2D model."""
        if not hasattr(self, '_live2d_view') or not self._live2d_view:
            return
        # Escape single quotes for JS
        safe = text.replace("\\", "\\\\").replace("'", "\\'")
        try:
            self._live2d_view.page().runJavaScript(
                f"window.notifyTip && window.notifyTip('{safe}')"
            )
        except Exception as e:
            log.warning("notify_live2d_tip: %s", e)

    def show_bubble(self, text: str, duration: int = 4000):
        """Show Neuro speech bubble above the model."""
        if not hasattr(self, '_live2d_view') or not self._live2d_view:
            return
        safe = text.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        try:
            self._live2d_view.page().runJavaScript(
                f"window.showBubble && window.showBubble('{safe}', {duration})"
            )
        except Exception as e:
            log.warning("show_bubble: %s", e)

    def show_neuro_state(self, state: str):
        """Update Neuro state indicator (emoji + text at bottom)."""
        if not hasattr(self, '_live2d_view') or not self._live2d_view:
            return
        try:
            self._live2d_view.page().runJavaScript(
                f"window.showNeuroState && window.showNeuroState('{state}')"
            )
        except Exception as e:
            log.warning("show_neuro_state: %s", e)

    def _svg_viewbox_for_state(self, state: str) -> QRect:
        # Different viewbox regions for different states (if spritesheet)
        return QRect(0, 0, 200, 280)

    def _on_live2d_loaded(self, ok: bool):
        """Check Live2D status after page load."""
        log.info("[live2d] loadFinished: ok=%s", ok)
        if ok and hasattr(self, '_live2d_view'):
            # Check model status after 3s delay
            QTimer.singleShot(3000, self._check_live2d_status)

    def _check_live2d_status(self):
        """Query JS for Live2D model status."""
        if not hasattr(self, '_live2d_view'):
            return
        page = self._live2d_view.page()
        # Get model name
        page.runJavaScript(
            "window.getLive2D ? JSON.stringify(window.getLive2D()) : 'no getLive2D'",
            lambda r: log.info("[live2d] model info: %s", r)
        )
        # Get model list
        page.runJavaScript(
            "window.getModelList ? window.getModelList().join(', ') : 'no list'",
            lambda r: log.info("[live2d] available: %s", r)
        )
        # Get current model index
        page.runJavaScript(
            "window.getCurrentModelIndex ? window.getCurrentModelIndex() : -1",
            lambda r: log.info("[live2d] current index: %s", r)
        )

    def _show_context_menu(self, global_pos: QPoint):
        """Show custom right-click context menu (called from JS via QWebChannel)."""
        menu = QMenu(self)
        menu.setStyleSheet("")  # empty = follow system theme

        # ── Model info ──
        def _add_model_info():
            info_action = menu.addAction("📍 当前模型: 加载中...")
            info_action.setEnabled(False)
            if hasattr(self, '_live2d_view') and self._live2d_view:
                self._live2d_view.page().runJavaScript(
                    "window.getCurrentModelName ? window.getCurrentModelName() : '?'",
                    lambda name: info_action.setText(f"📍 当前模型: {name}")
                )
        _add_model_info()
        menu.addSeparator()

        # ── Model switching ──
        switch_menu = menu.addMenu("🔄 切换模型")
        switch_menu.addAction("⏮ 上一个", self._ctx_prev_model)
        switch_menu.addAction("⏭ 下一个", self._ctx_next_model)
        switch_menu.addSeparator()

        # Add individual model entries
        if hasattr(self, '_live2d_view') and self._live2d_view:
            self._live2d_view.page().runJavaScript(
                "window.getAllModelNames ? JSON.stringify(window.getAllModelNames()) : '[]'",
                lambda names_json: self._populate_model_switch_menu(switch_menu, names_json)
            )

        # ── Switch costume (texture) ──
        menu.addAction("👗 切换服装", self._ctx_next_texture)

        # ── Scale adjustment ──
        scale_menu = menu.addMenu("📏 模型比例")
        for label, multiplier in [("50%", 0.5), ("75%", 0.75), ("100% (默认)", 1.0),
                                   ("125%", 1.25), ("150%", 1.5), ("200%", 2.0)]:
            action = scale_menu.addAction(label)
            action.triggered.connect(lambda checked, m=multiplier: self._ctx_set_scale(m))

        menu.addSeparator()

        # ── Capture screenshot ──
        menu.addAction("📸 保存图片", self._ctx_capture_model)

        # ── Toggle hit frames ──
        menu.addAction("🔲 帧检测", self._ctx_toggle_hitframes)

        # ── Random model ──
        menu.addAction("🔀 随机模型", self._ctx_random_model)

        menu.addSeparator()
        menu.addAction("💤 隐藏", lambda: self.hide())
        menu.addAction("❌ 退出", lambda: QApplication.quit())

        # Show menu at cursor position
        menu.popup(QCursor.pos())

    def _populate_model_switch_menu(self, switch_menu: QMenu, names_json: str):
        """Populate model switch submenu with individual model names."""
        import json
        try:
            names = json.loads(names_json)
        except Exception:
            names = []
        if not names:
            return
        switch_menu.addSeparator()
        for idx, name in enumerate(names):
            action = switch_menu.addAction(f"  {idx + 1}. {name}")
            action.triggered.connect(
                lambda checked, i=idx: self._ctx_switch_to(i)
            )

    def _ctx_next_model(self):
        if hasattr(self, '_live2d_view') and self._live2d_view:
            self._live2d_view.page().runJavaScript("window.nextModel && window.nextModel()")

    def _ctx_prev_model(self):
        if hasattr(self, '_live2d_view') and self._live2d_view:
            self._live2d_view.page().runJavaScript("window.prevModel && window.prevModel()")

    def _ctx_switch_to(self, idx: int):
        if hasattr(self, '_live2d_view') and self._live2d_view:
            self._live2d_view.page().runJavaScript(
                f"window.switchToModel && window.switchToModel({idx})"
            )

    def _ctx_random_model(self):
        if hasattr(self, '_live2d_view') and self._live2d_view:
            import random
            self._live2d_view.page().runJavaScript(
                "window.getModelCount ? window.getModelCount() : 0",
                lambda count: self._live2d_view.page().runJavaScript(
                    f"window.switchToModel && window.switchToModel({random.randint(0, max(0, int(count) - 1))})"
                ) if count else None
            )

    def _ctx_set_scale(self, multiplier: float):
        if hasattr(self, '_live2d_view') and self._live2d_view:
            self._live2d_view.page().runJavaScript(
                f"window.setModelScale && window.setModelScale({multiplier})"
            )

    def _ctx_next_texture(self):
        """Switch to next costume/texture for current model."""
        if hasattr(self, '_live2d_view') and self._live2d_view:
            self._live2d_view.page().runJavaScript(
                "window.nextTexture && window.nextTexture()"
            )

    def _ctx_capture_model(self):
        """Capture current model as PNG and save to file."""
        if not hasattr(self, '_live2d_view') or not self._live2d_view:
            return
        self._live2d_view.page().runJavaScript(
            "window.captureModel && window.captureModel()",
            lambda data_url: self._save_capture(data_url) if data_url else None
        )

    def _save_capture(self, data_url: str):
        """Save base64 PNG data URL to file."""
        import base64
        from PyQt6.QtWidgets import QFileDialog
        # Parse data URL: data:image/png;base64,xxxx
        try:
            header, encoded = data_url.split(",", 1)
            img_data = base64.b64decode(encoded)
        except Exception as e:
            log.warning("capture decode failed: %s", e)
            return
        # Ask user where to save
        path, _ = QFileDialog.getSaveFileName(
            self, "保存图片", "live2d_capture.png", "PNG (*.png)"
        )
        if path:
            try:
                with open(path, "wb") as f:
                    f.write(img_data)
                log.info("capture saved: %s", path)
            except Exception as e:
                log.warning("capture save failed: %s", e)

    def _ctx_toggle_hitframes(self):
        """Toggle hit area frame display."""
        if hasattr(self, '_live2d_view') and self._live2d_view:
            self._live2d_view.page().runJavaScript(
                "window.toggleHitFrames && window.toggleHitFrames()",
                lambda on: log.info("hit frames: %s", "ON" if on else "OFF")
            )


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

        # ─── Neuro menu (桌宠 → Neuro 控制) ───
        neuro_menu = menu.addMenu("🧠 Neuro")
        neuro_menu.addAction("💬 让伊卡洛斯主动说话", self._trigger_patience)
        neuro_menu.addSeparator()

        patience_menu = neuro_menu.addMenu("⏱️ PATIENCE 阈值")
        for label, sec in [("15s (敏感)", 15), ("30s (默认)", 30), ("60s (慢热)", 60), ("120s (极慢)", 120)]:
            a = patience_menu.addAction(label)
            a.triggered.connect(lambda checked, s=sec: self._set_patience(s))

        neuro_menu.addAction("🔄 重置说话标志", self._reset_signals)
        neuro_menu.addAction("🧠 看记忆…", self._show_memories)
        neuro_menu.addSeparator()
        neuro_menu.addAction("📝 加一条记忆…", self._add_memory_prompt)

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

    # ─── Neuro control handlers ───

    def _get_neuro(self):
        """Get the IcarusApp's NeuroClient via window's app instance."""
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app and hasattr(app, '_icarus_pet'):
            return app._icarus_pet.neuro
        return None

    def _trigger_patience(self):
        """桌宠菜单: 手动触发 PATIENCE (让 AI 主动说话)"""
        neuro = self._get_neuro()
        if neuro and neuro.trigger_patience():
            self.update_status("Neuro triggered")
        else:
            self.update_status("Neuro unavailable")

    def _set_patience(self, seconds: float):
        """桌宠菜单: 调整 PATIENCE 阈值"""
        neuro = self._get_neuro()
        if neuro and neuro.set_patience(seconds):
            self.update_status(f"PATIENCE → {seconds:.0f}s")
        else:
            self.update_status("PATIENCE failed")

    def _reset_signals(self):
        """桌宠菜单: 重置说话标志"""
        neuro = self._get_neuro()
        if neuro and neuro.reset_signals():
            self.update_status("Neuro reset")

    def _show_memories(self):
        """桌宠菜单: 弹窗显示最近 10 条记忆"""
        from PyQt6.QtWidgets import QMessageBox
        neuro = self._get_neuro()
        if not neuro:
            QMessageBox.warning(self.window, "Neuro", "Neuro client not available")
            return
        mems = neuro.get_memories(limit=10)
        if not mems:
            QMessageBox.information(self.window, "Neuro", "(no memories)")
            return
        text = "\n".join(
            f"[{m['metadata'].get('type', '?')}] {m['document']}"
            for m in mems
        )
        QMessageBox.information(self.window, f"Neuro · {len(mems)} 条记忆", text)

    def _add_memory_prompt(self):
        """桌宠菜单: 弹输入框加记忆"""
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self.window, "Neuro · 加记忆",
            "伊卡洛斯要记住的:"
        )
        if ok and text.strip():
            neuro = self._get_neuro()
            if neuro and neuro.add_memory(text.strip()):
                self.update_status("记忆已加")

    def _toggle_visible(self):
        self.window.setVisible(not self.window.isVisible())

    def _sleep(self):
        self.window.hide()

def _quit(self):
    QApplication.quit()


# ─── Chat Dock (4C: 文本聊天入口) ───

BRIDGE_CHAT_URL = "http://127.0.0.1:7860/v1/chat/completions"
BRIDGE_CHAT_TIMEOUT_S = 30.0


async def bridge_chat(text: str, *, profile: str = "default", model: str = "MiniMax-M3") -> str:
    """4C: 调 Hermes bridge /v1/chat/completions — cloud auto-flip 已实现.

    Args:
        text: 用户输入
        profile: Hermes profile (default)
        model: 默认 MiniMax-M3 (bridge 已注册 minimax-cn, .env 有 key)

    Returns:
        assistant 回复文本 (string)
    """
    if httpx is None:
        # Fallback: urllib (同步, 不优雅但能用)
        import json, urllib.request
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": text}],
            "max_tokens": 200,
        }).encode("utf-8")
        req = urllib.request.Request(
            BRIDGE_CHAT_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=BRIDGE_CHAT_TIMEOUT_S) as r:
            data = json.loads(r.read())
    else:
        async with httpx.AsyncClient(timeout=BRIDGE_CHAT_TIMEOUT_S) as c:
            r = await c.post(BRIDGE_CHAT_URL, json={
                "model": model,
                "messages": [{"role": "user", "content": text}],
                "max_tokens": 200,
            })
            data = r.json()
    return data["choices"][0]["message"]["content"]


async def edge_tts_mp3(text: str, voice: str = "zh-CN-XiaoxiaoNeural") -> bytes:
    """4C: 用 edge-tts 把文字 → MP3 bytes (4B audio_engine.play_mp3_bytes 接).

    Returns:
        完整 MP3 file bytes (可能空 bytes 如果 edge-tts 失败 — caller 检查)
    """
    try:
        communicate = edge_tts.Communicate(text, voice=voice)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)
    except Exception as exc:
        log.warning("edge-tts failed: %s", exc)
        return b""


class ChatDockWindow(QMainWindow):
    """4C: 独立的 chat dock window — 双击桌宠打开.

    Layout:
      ┌────────────────────────────────────┐
      │ ChatDock — 伊卡洛斯 chat            │
      ├────────────────────────────────────┤
      │ [QTextEdit history — read-only]     │
      │                                     │
      │                                     │
      ├────────────────────────────────────┤
      │ [QLineEdit] [Send]  intent: chat   │  ← 输入区 + intent 实时显示
      └────────────────────────────────────┘

    Connections:
      - 输入 → IntentRouter.classify() → intent_label
      - Send → bridge_chat(text) → history append
      - reply → edge_tts_mp3(reply) → audio_engine.play_mp3_bytes (4B 输出)
    """

    WIDTH, HEIGHT = 420, 560

    def __init__(self, audio_engine=None, bridge_signal: SignalBridge = None):
        super().__init__()
        self.audio = audio_engine  # may be None
        self.bridge_signal = bridge_signal  # for state broadcasts

        self.setWindowTitle("💬 伊卡洛斯 chat — 双击桌宠收起")
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(8, 8, 8, 8)

        # History
        self.history = QTextEdit()
        self.history.setReadOnly(True)
        self.history.setStyleSheet(
            "QTextEdit { font-family: 'Microsoft YaHei', 'Consolas', monospace;"
            "            font-size: 12pt; background: #fafafa;"
            "            border: 1px solid #ccc; border-radius: 4px; }"
        )
        v.addWidget(self.history, 1)

        # Input row
        input_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("跟伊卡洛斯说点啥… (Enter 发送, Shift+Enter 换行)")
        self.input_edit.setStyleSheet(
            "QLineEdit { font-size: 12pt; padding: 6px;"
            "            border: 1px solid #ccc; border-radius: 4px; }"
        )
        self.input_edit.returnPressed.connect(self._on_send)
        input_row.addWidget(self.input_edit, 1)

        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._on_send)
        input_row.addWidget(self.send_btn)

        v.addLayout(input_row)

        # Status row (intent + hint)
        self.status_label = QLabel("intent: —  |  cloud auto-flip on (minimax-cn)")
        self.status_label.setStyleSheet(
            "QLabel { font-size: 9pt; color: #888; padding: 2px 4px; }"
        )
        v.addWidget(self.status_label)

        self._append_history("[伊卡洛斯] 哥哥好, 我在听~")

    def _append_history(self, line: str):
        self.history.append(line)
        # auto-scroll
        sb = self.history.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def _on_send(self):
        text = self.input_edit.text().strip()
        if not text:
            return

        # 1. IntentRouter classify (Layer 1, ms 级)
        if _IntentRouter is not None:
            try:
                intent = _IntentRouter.classify(text)
            except Exception as exc:
                log.debug("IntentRouter classify failed: %s", exc)
                intent = "ambiguous"
        else:
            intent = "ambiguous"
        self._set_status(f"intent: {intent}  |  sending…")
        self._append_history(f"[你] {text}")
        self.input_edit.clear()

        # 2. Disable UI during request
        self.input_edit.setEnabled(False)
        self.send_btn.setEnabled(False)

        # 3. Async chain — call bridge_chat, then TTS, then re-enable UI
        self._run_chat_chain(text, intent)

    def _run_chat_chain(self, text: str, intent: str):
        """QTimer.singleShot 把 coroutine 送进 asyncio loop.

        Qt 没有原生 async 支持, 用 QTimer 0ms 触发 + 在 QThread 里跑 asyncio
        的常见做法: 用 asyncio.run_coroutine_threadsafe 把 coroutine 送进主 loop.
        但我们没显式主 loop — 改用 threading 直接 run.
        """
        def worker():
            try:
                # 在新 loop 里跑 (避免 Qt 主线程 asyncio 冲突)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    reply = loop.run_until_complete(bridge_chat(text))
                finally:
                    loop.close()
                # 回到 Qt 主线程更新 UI
                QTimer.singleShot(0, lambda: self._on_reply(reply, intent))
            except Exception as exc:
                log.error("chat chain failed: %s", exc)
                QTimer.singleShot(0, lambda: self._on_reply(f"⚠️ {exc}", intent))

        threading.Thread(target=worker, daemon=True).start()

    def _on_reply(self, reply: str, intent: str):
        """Reply 回来后: history + TTS + 重启用 UI."""
        self._append_history(f"[伊卡洛斯] {reply}")
        self._set_status(f"intent: {intent}  |  cloud ✓")
        self.input_edit.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.input_edit.setFocus()

        # TTS 播 (4B) — 在后台线程跑, 不阻塞 UI
        if self.audio is not None and reply and not reply.startswith("⚠️"):
            def tts_worker():
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        mp3 = loop.run_until_complete(edge_tts_mp3(reply))
                        if mp3:
                            self.audio.play_mp3_bytes(mp3)
                    finally:
                        loop.close()
                except Exception as exc:
                    log.warning("TTS worker failed: %s", exc)
            threading.Thread(target=tts_worker, daemon=True).start()

    def show_near_pet(self, pet_window):
        """显示在桌宠旁边 (右上角偏右)."""
        if pet_window is None or not pet_window.isVisible():
            self.show()
            self.raise_()
            return
        pet_geo = pet_window.geometry()
        # 桌宠右侧 +8px, 垂直居中
        x = pet_geo.x() + pet_geo.width() + 8
        y = pet_geo.y() + max(0, (pet_geo.height() - self.HEIGHT) // 2)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.input_edit.setFocus()


# ─── Context thread (Neuro context detection) ───
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

class IcarusApp(QObject):
    def __init__(self):
        super().__init__()
        self.bridge = SignalBridge()
        self.window = PetWindow(self.bridge)
        self.audio = None
        self.tray = None
        self.neuro = None  # NeuroClient, set in run()

        # Connect signals
        self.bridge.state_changed.connect(self._on_state)
        self.bridge.bubble_shown.connect(self._on_bubble)
        self.bridge.context_changed.connect(self._on_context)
        # Neuro signals
        self.bridge.neuro_state_changed.connect(self._on_neuro_state)
        self.bridge.neuro_patience_changed.connect(self._on_neuro_patience)

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

    def _on_neuro_state(self, state: str):
        """Neuro AI 状态变化 → 桌宠表情 + 状态指示器."""
        log.debug("neuro state → %s (patience %.1fs, t=%.1f)",
                  state, self.neuro.patience, self.neuro.time_since_last)
        # 映射到现有 character state
        # idle / listening / thinking / speaking / bored
        self.window.set_state(state)
        # 更新 Neuro 状态指示器 (底部 emoji + 文字)
        self.window.show_neuro_state(state)

    def _on_neuro_patience(self, seconds: float):
        """PATIENCE 变化 → tray tooltip"""
        if self.tray:
            self.tray.update_status(f"Neuro {seconds:.0f}s · {self.neuro.history_len} 条记忆")

    def _on_neuro_update(self, status: dict):
        """NeuroClient 1Hz 回调 → 推到 Qt signal"""
        # 1) 状态变化
        new_state = self.neuro.ai_state
        if not hasattr(self, '_last_neuro_state') or self._last_neuro_state != new_state:
            self._last_neuro_state = new_state
            self.bridge.neuro_state_changed.emit(new_state)
        # 2) PATIENCE 变化
        new_patience = status.get("patience", 30.0)
        if not hasattr(self, '_last_patience') or abs(self._last_patience - new_patience) > 0.5:
            self._last_patience = new_patience
            self.bridge.neuro_patience_changed.emit(new_patience)


    def run(self):

        # 4A: Audio engine re-enabled (sounddevice 替 pyaudio)
        try:
            from audio_engine import AudioEngine
            self.audio = AudioEngine()
            self.audio.start()
            log.info("✓ audio engine started (sounddevice mic + TTS playback)")
        except Exception as exc:
            log.warning("⚠ audio engine failed to start: %s", exc)
            self.audio = None

        # 4D: Wire audio engine callbacks → SignalBridge → Live2D 表情
        # audio_engine 在 WS 收到 thinking/SPEAKING/done 等时 emit 状态.
        # 通过 SignalBridge 推到 _on_state → PetWindow.set_state() → window.setState() JS
        if self.audio is not None:
            self.audio.on_state = lambda s: self.bridge.state_changed.emit(s)
            self.audio.on_bubble = lambda t, d: self.bridge.bubble_shown.emit(t, d)
            log.info("✓ audio.on_state/on_bubble wired → SignalBridge → Live2D 表情")
        # 4C: Chat dock window (独立 window, 双击桌宠打开)
        try:
            self.chat_dock = ChatDockWindow(
                audio_engine=self.audio,
                bridge_signal=self.bridge,
            )
            log.info("✓ chat dock ready (双击桌宠打开)")
        except Exception as exc:
            log.warning("⚠ chat dock init failed: %s", exc)
            self.chat_dock = None

        # 4C: wire 桌宠 mouseDoubleClickEvent → toggle chat dock
        # (PetWindow 本身没 mouseDoubleClickEvent, 我们 installEventFilter 或
        #  改 PetWindow.__init__ 添加 handler — 走 eventFilter 最干净)
        self.window.installEventFilter(self)
        log.info("✓ pet window event filter installed (double-click → chat dock)")

        # Other components still disabled (tray/context Quest 默认)
        self.tray = None
        self._context = None

        # Phase 5: NeuroClient (PATIENCE 主动通知 + AI 状态轮询)
        try:
            from neuro_client import NeuroClient as _NeuroClient
            self.neuro = _NeuroClient(on_status_change=self._on_neuro_update)
            self.neuro.start()
            log.info("✓ NeuroClient started (1Hz poll → Live2D 表情 + PATIENCE)")
        except Exception as exc:
            log.warning("⚠ NeuroClient start failed: %s", exc)
            self.neuro = None

        log.info("🪶 show window + exec")
        self.window.show()
        return QApplication.exec()

    def eventFilter(self, obj, event):
        """4C: 拦截 PetWindow 的鼠标双击 → toggle chat dock."""
        if obj is self.window and event.type() == QEvent.Type.MouseButtonDblClick:
            if self.chat_dock is not None:
                if self.chat_dock.isVisible():
                    self.chat_dock.hide()
                    log.debug("chat dock hidden")
                else:
                    self.chat_dock.show_near_pet(self.window)
                    log.debug("chat dock shown")
                return True  # 拦截, 不传给原 handler
        return super().eventFilter(obj, event)

    def cleanup(self):
        if self.audio:
            self.audio.stop()
        if hasattr(self, '_context'):
            self._context.stop()
        if self.neuro:
            self.neuro.stop()


def register_autostart():
    """Register desktop pet for Windows autostart."""
    try:
        import winreg
        python = sys.executable
        # Use the detached launcher so the pet survives HKCU boot
        cmd = f'"{python}" "{HERE / "main.py"}"'
        # Also register the .bat wrapper for nice double-click UX
        bat_cmd = f'""{HERE / "start.bat"}""'
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
    # Expose to NeuroClient lookup (tray needs to find pet.neuro)
    app._icarus_pet = pet
    rc = pet.run()
    pet.cleanup()
    sys.exit(rc)


if __name__ == "__main__":
    main()
