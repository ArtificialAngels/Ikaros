"""
🪶 Ikaros Desktop Pet — PyQt6 Edition
Always-on-top transparent window, SVG chibi Ikaros, system tray, voice + context.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, pyqtSignal, pyqtSlot, QObject, QUrl, QUrlQuery, QEvent
from PyQt6.QtGui import QAction, QActionGroup, QIcon, QPainter, QPixmap, QColor, QCursor
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage


class _Live2DPage(QWebEnginePage):
    """QWebEnginePage subclass that forwards JS console messages to Python log."""
    _log = logging.getLogger("ikaros.live2d")

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


class _DelayedMenu(QMenu):
    """QMenu subclass that delays hiding when mouse moves toward a submenu.

    Prevents the submenu from closing before the user can move the cursor
    from the parent item into the submenu area.
    """
    _hide_delay_ms = 500

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(self._hide_delay_ms)
        self._hide_timer.timeout.connect(self._on_delay_expired)
        self._suppress_hide = False

    def _on_delay_expired(self):
        """Timer fired — actually hide if mouse is not over any child submenu."""
        self._suppress_hide = False
        pos = QCursor.pos()
        for action in self.actions():
            sub = action.menu()
            if sub and sub.isVisible():
                if sub.geometry().adjusted(-4, -4, 4, 4).contains(pos):
                    return  # mouse is over a submenu, stay open
        if not self._suppress_hide:
            super().hide()

    def hideEvent(self, event):
        """Intercept hide: delay if mouse is moving toward a submenu."""
        if self._suppress_hide:
            self._suppress_hide = False
            super().hideEvent(event)
            return
        pos = QCursor.pos()
        for action in self.actions():
            sub = action.menu()
            if sub and sub.isVisible():
                if sub.geometry().adjusted(-4, -4, 4, 4).contains(pos):
                    # Mouse is over a submenu — delay the hide
                    self._suppress_hide = True
                    self._hide_timer.start()
                    # Re-show ourselves (we're in the middle of hiding)
                    QTimer.singleShot(0, self.show)
                    event.ignore()
                    return
        super().hideEvent(event)

    def enterEvent(self, event):
        """Mouse entered the menu — cancel any pending hide."""
        self._suppress_hide = False
        self._hide_timer.stop()
        super().enterEvent(event)

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
_MODEL_CACHE_PATH = HERE / "llm_model_cache.json"
_LLM_MODEL_PERSIST_PATH = HERE / "last_llm_model.json"  # persists across restarts


# 便携 Python 不自动加 cwd 到 sys.path (python312._pth 机制)
# 手动加, 让 from audio_engine import AudioEngine 等本地 import 能工作
sys.path.insert(0, str(HERE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ikaros] %(message)s")
log = logging.getLogger("ikaros")

# Singleton lock (Windows LockFileEx) — 只允许一个桌宠进程
from singleton import require_singleton_or_exit, IkarosPetLock

# 4C: IntentRouter (Layer 1 规则 — task/chat/ambiguous)
# 必须在 HERE + log 都定义之后, 否则 except 分支用 log 会 NameError
try:
    import sys as _sys
    # IntentRouter 现在是 sibling module (bin/ikaros-desktop-pet/intent_router.py)
    # 2026-06-28: bridge/ 删除, IntentRouter 移到这里
    from intent_router import IntentRouter as _IntentRouter
except Exception as _exc:
    log.warning("IntentRouter import failed: %s — chat 会 fall back 到 LLM 隐式", _exc)
    _IntentRouter = None

# 4C: edge-tts (TTS) — ChatDockWindow 用它生成回复 MP3
try:
    import edge_tts  # Microsoft Edge neural TTS (offline API)
except ImportError:
    log.warning("edge-tts not installed — 4C chat 会用 fallback TTS (no playback)")
    edge_tts = None


# ─── LLM model cache helpers ───


def _scan_local_gguf_files() -> list[str]:
    """Scan data/models/*.gguf and return list of model names (no .gguf suffix)."""
    models_dir = HERMES_ROOT / "data" / "models"
    if not models_dir.exists():
        return []
    seen: set[str] = set()
    result: list[str] = []
    try:
        for f in sorted(models_dir.glob("*.gguf")):
            name = f.stem  # removes .gguf
            # Exclude: mmproj, split parts (xxxx-of-xxxx)
            if "mmproj" in name.lower():
                continue
            if "-of-" in name and re.search(r'\d{5}-of-\d{5}', name):
                continue
            if name not in seen:
                seen.add(name)
                result.append(name)
    except Exception as exc:
        log.warning("GGUF scan failed: %s", exc)
    return result


def _save_model_cache(models: list[str], cloud_models: list[str]):
    """Save model list to disk for fast startup."""
    try:
        _MODEL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MODEL_CACHE_PATH.write_text(
            json.dumps({"models": models, "cloud": cloud_models, "ts": time.time()}),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("model cache save FAILED: %s", exc)


def _save_last_llm_model(model_id: str):
    """Persist the last-selected LLM model ID across restarts."""
    try:
        _LLM_MODEL_PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LLM_MODEL_PERSIST_PATH.write_text(
            json.dumps({"model": model_id, "ts": time.time()}),
            encoding="utf-8",
        )
        log.info("persisted last LLM model: %s", model_id)
    except Exception as exc:
        log.warning("save last LLM model FAILED: %s", exc)

    # Also sync to llm-engine-last-launch.json so llama-server router knows preferred model
    try:
        llm_launch_path = HERMES_ROOT / "data" / "logs" / "llm-engine-last-launch.json"
        if llm_launch_path.exists():
            info = json.loads(llm_launch_path.read_text(encoding="utf-8"))
        else:
            info = {}
        info["preferred_model"] = model_id
        llm_launch_path.parent.mkdir(parents=True, exist_ok=True)
        llm_launch_path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        log.debug("sync preferred_model to llm-engine-last-launch.json FAILED: %s", exc)


def _load_last_llm_model() -> str | None:
    """Load the persisted last LLM model ID. Returns None if missing or stale."""
    try:
        if not _LLM_MODEL_PERSIST_PATH.exists():
            return None
        data = json.loads(_LLM_MODEL_PERSIST_PATH.read_text(encoding="utf-8"))
        model = data.get("model")
        if model:
            return model
    except Exception:
        pass
    return None


def _load_model_cache() -> dict | None:
    """Load cached model list from disk. Returns None if missing or stale (>12h)."""
    try:
        if not _MODEL_CACHE_PATH.exists():
            return None
        data = json.loads(_MODEL_CACHE_PATH.read_text(encoding="utf-8"))
        # Stale if older than 12 hours (模型文件不经常变)
        ts = data.get("ts", 0)
        if time.time() - ts > 43200:
            return None
        if not data.get("models"):
            return None
        return data
    except Exception:
        return None


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
        self._hit_frames_on = False  # track hit frame toggle state

        # LLM model selection (shared across chat dock + voice)
        persisted = _load_last_llm_model()
        self._current_llm_model: str = persisted or "Phi-4-Mini-3.8B-Q4_K_L"  # default
        if persisted:
            log.info("restored last LLM model: %s", persisted)
        self._available_models: list[str] = []  # model IDs from bridge
        self._cloud_model_set: set[str] = set()  # cloud model IDs for tag display
        self._models_fetching = False
        self._pending_llm_menu: QMenu | None = None  # context menu awaiting model list
        self._live2d_model_names_cache: list[str] | None = None  # Live2D model name cache

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

        # Load cached model list for instant right-click menu availability
        cached = _load_model_cache()
        if cached:
            self._available_models = cached["models"]
            self._cloud_model_set = set(cached["cloud"])
            log.info("loaded %d models from disk cache", len(cached["models"]))

        # Pre-fetch fresh LLM model list in background
        QTimer.singleShot(2000, self._fetch_models_async)

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

        # ── Live2D 模型切换 ──
        switch_menu = _DelayedMenu("🔄 切换形象", menu)
        menu.addMenu(switch_menu)
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
        scale_menu = _DelayedMenu("📏 模型比例", menu)
        menu.addMenu(scale_menu)
        for label, multiplier in [("50%", 0.5), ("75%", 0.75), ("100% (默认)", 1.0),
                                   ("125%", 1.25), ("150%", 1.5), ("200%", 2.0)]:
            action = scale_menu.addAction(label)
            action.triggered.connect(lambda checked, m=multiplier: self._ctx_set_scale(m))

        menu.addSeparator()

        # ── Capture screenshot ──
        menu.addAction("📸 保存图片", self._ctx_capture_model)

        # ── Toggle hit frames (checkable, shows current state) ──
        hit_action = menu.addAction("🔲 帧检测", self._ctx_toggle_hitframes)
        hit_action.setCheckable(True)
        hit_action.setChecked(self._hit_frames_on)

        # ── Random model ──
        menu.addAction("🔀 随机模型", self._ctx_random_model)

        menu.addSeparator()

        # ── 🤖 LLM 模型切换 ──
        llm_menu = _DelayedMenu(f"🤖 LLM 模型: {self._current_llm_model}", menu)
        menu.addMenu(llm_menu)

        if self._available_models:
            # Models already fetched — populate directly
            self._populate_llm_menu(llm_menu)
        else:
            # Fetch models async, show loading placeholder
            loading_action = llm_menu.addAction("⏳ 加载模型列表...")
            loading_action.setEnabled(False)
            if not self._models_fetching:
                self._fetch_models_async()
        # Track current LLM submenu so "🔄 刷新列表" can auto-update it
        self._pending_llm_menu = llm_menu

        menu.addSeparator()

        # ── 📊 监控日志 (PowerShell tail) ──
        def _open_monitor():
            import subprocess
            log_dir = HERMES_ROOT / "data" / "logs"
            log_path = str(log_dir / "ikaros-pet.log")
            # Use pwsh (PowerShell 7) if available, fall back to powershell (5.1)
            shell = "pwsh"
            try:
                subprocess.run([shell, "-Command", "exit"], capture_output=True, timeout=3)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                shell = "powershell"
            subprocess.Popen(
                [shell, "-NoExit", "-Command",
                 f"Get-Content -Wait -Tail 50 -Encoding utf8 '{log_path}'"],
                creationflags=0x00000010,  # CREATE_NEW_CONSOLE — 打开新窗口
            )
        menu.addAction("📊 监控日志", _open_monitor)

        # ── 🔄 重启 ──
        def _restart_pet():
            """Restart the desktop pet (spawn new process, then quit)."""
            import subprocess
            # Launch new instance (detached)
            subprocess.Popen(
                [sys.executable, str(HERE / "main.py")],
                cwd=str(HERE),
                creationflags=0x00000008,  # DETACHED_PROCESS
                close_fds=True,
            )
            # Quit current instance (releases singleton lock)
            QApplication.quit()

        menu.addAction("🔄 重启", _restart_pet)

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
        # Cache names for _get_model_name_by_index
        self._live2d_model_names_cache = names
        switch_menu.addSeparator()
        for idx, name in enumerate(names):
            action = switch_menu.addAction(f"  {idx + 1}. {name}")
            action.triggered.connect(
                lambda checked, i=idx: self._ctx_switch_to(i)
            )

    def _ctx_next_model(self):
        if hasattr(self, '_live2d_view') and self._live2d_view:
            self.notify_live2d_tip("切换下一个形象...")
            self._live2d_view.page().runJavaScript("window.nextModel && window.nextModel()")

    def _ctx_prev_model(self):
        if hasattr(self, '_live2d_view') and self._live2d_view:
            self.notify_live2d_tip("切换上一个形象...")
            self._live2d_view.page().runJavaScript("window.prevModel && window.prevModel()")

    def _ctx_switch_to(self, idx: int):
        if hasattr(self, '_live2d_view') and self._live2d_view:
            model_name = self._get_model_name_by_index(idx)
            self.notify_live2d_tip(f"切换到 {model_name}")
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
                ) if count and int(count) > 0 else None
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

    def _get_model_name_by_index(self, idx: int) -> str:
        """Get Live2D model name by index (from cache)."""
        names = getattr(self, '_live2d_model_names_cache', None)
        if names and 0 <= idx < len(names):
            return names[idx]
        return f"#{idx}"

    def _ctx_toggle_hitframes(self):
        """Toggle hit area frame display (checkable menu item)."""
        self._hit_frames_on = not self._hit_frames_on
        if hasattr(self, '_live2d_view') and self._live2d_view:
            if self._hit_frames_on:
                self._live2d_view.page().runJavaScript(
                    "window.showHitFrames && window.showHitFrames()"
                )
            else:
                self._live2d_view.page().runJavaScript(
                    "window.hideHitFrames && window.hideHitFrames()"
                )
        log.info("hit frames: %s", "ON" if self._hit_frames_on else "OFF")

    # ─── LLM Model switching ───

    # Cloud models by provider (shown when API key is configured)
    _CLOUD_MODELS = {
        "minimax-cn": ["MiniMax-M3", "MiniMax-M1", "abab6.5s-chat"],
        "deepseek": ["deepseek-chat", "deepseek-reasoner"],
        "openai": ["gpt-4o", "gpt-4o-mini"],
        "openrouter": ["openrouter/auto"],
    }
    # env var → provider name
    _CLOUD_KEY_MAP = {
        "MINIMAX_CN_API_KEY": "minimax-cn",
        "MINIMAX_API_KEY": "minimax-cn",
        "DEEPSEEK_API_KEY": "deepseek",
        "OPENAI_API_KEY": "openai",
        "OPENROUTER_API_KEY": "openrouter",
    }

    def _fetch_models_async(self):
        """Fetch available models: llama-server (local) + cloud (by API key).
        Falls back to scanning local GGUF files if server unreachable.
        """
        self._models_fetching = True

        def worker():
            import urllib.request

            local_models: list[str] = []
            cloud_models: list[str] = []

            # 1. Local models — try llama-server :8080 first (router mode),
            #    fall back to bridge :7860, then local GGUF scan
            api_success = False
            for port in (8080, 7860):
                try:
                    url = f"http://127.0.0.1:{port}/v1/models"
                    req = urllib.request.Request(url, method="GET")
                    with urllib.request.urlopen(req, timeout=3.0) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    raw_ids = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
                    # Deduplicate: strip .gguf suffix, remove mmproj and split parts
                    seen: set[str] = set()
                    for mid in raw_ids:
                        if "mmproj" in mid.lower():
                            continue
                        # Exclude split parts: xxx-00001-of-00002
                        if re.search(r'-\d{5}-of-\d{5}', mid):
                            continue
                        base = mid.removesuffix(".gguf").removesuffix(".GGUF")
                        if base not in seen:
                            seen.add(base)
                            local_models.append(base)
                    api_success = True
                    break  # got response, no need to try next port
                except Exception as exc:
                    log.debug("port %d models failed: %s", port, exc)
                    continue

            # 1b. Fallback: scan local GGUF files if API failed
            if not api_success:
                gguf_models = _scan_local_gguf_files()
                if gguf_models:
                    local_models = gguf_models
                    log.info("fallback: scanned %d local GGUF files", len(gguf_models))

            # 2. Cloud models — detect API keys from env + HERMES_HOME/.env
            configured_providers = self._detect_cloud_providers()
            for provider in configured_providers:
                cloud_models.extend(self._CLOUD_MODELS.get(provider, []))

            all_models = local_models + cloud_models
            log.info("models fetched: %d local + %d cloud", len(local_models), len(cloud_models))
            # Save cache in worker thread (thread-safe: file I/O only, no GUI)
            _save_model_cache(all_models, cloud_models)
            QTimer.singleShot(0, lambda: self._on_models_fetched(all_models, cloud_models))
            self._models_fetching = False

        threading.Thread(target=worker, daemon=True).start()

    def _detect_cloud_providers(self) -> list[str]:
        """Detect which cloud providers have API keys configured."""
        import os
        configured: set[str] = set()

        # Check os.environ (loaded from .env at startup)
        for env_var, provider in self._CLOUD_KEY_MAP.items():
            key = os.environ.get(env_var, "").strip()
            if key and not key.startswith("$"):
                configured.add(provider)

        # Also check HERMES_HOME/.env directly (in case not loaded)
        try:
            hermes_env = HERMES_ROOT / "data" / "hermes-agent" / ".env"
            if hermes_env.exists():
                for line in hermes_env.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"").strip()
                    if v and not v.startswith("$"):
                        for env_var, provider in self._CLOUD_KEY_MAP.items():
                            if k == env_var:
                                configured.add(provider)
        except Exception:
            pass

        return list(configured)

    def _on_models_fetched(self, models: list[str], cloud_models: list[str] | None = None):
        """Called on main thread after models are fetched."""
        self._available_models = models
        cloud_list = cloud_models or []
        self._cloud_model_set = set(cloud_list)
        log.info("available LLM models: %d total (%d cloud)", len(models), len(cloud_list))
        # If current model not in list, auto-select first local model
        if self._current_llm_model and self._current_llm_model not in models:
            local_only = [m for m in models if m not in cloud_list]
            if local_only:
                fallback = local_only[0]
                log.warning("current model '%s' not in available list — falling back to '%s'",
                            self._current_llm_model, fallback)
                self._current_llm_model = fallback
                _save_last_llm_model(fallback)
                self.notify_live2d_tip(f"模型切换: {fallback}")
            else:
                self._available_models.insert(0, self._current_llm_model)
                log.warning("current model '%s' not in available list — added anyway", self._current_llm_model)
        # NOTE: disk cache already saved in worker thread (_fetch_models_async)
        # Auto-update context menu if it's still visible
        if self._pending_llm_menu is not None:
            try:
                if self._pending_llm_menu.isVisible():
                    self._pending_llm_menu.clear()
                    self._populate_llm_menu(self._pending_llm_menu)
            except RuntimeError:
                pass  # menu was destroyed
            self._pending_llm_menu = None

    def _populate_llm_menu(self, llm_menu: QMenu):
        """Fill LLM model submenu with fetched model list."""
        if not self._available_models:
            no_action = llm_menu.addAction("(无可用模型)")
            no_action.setEnabled(False)
            retry_action = llm_menu.addAction("🔄 重试")
            retry_action.triggered.connect(self._fetch_models_async)
            return

        cloud_set = getattr(self, '_cloud_model_set', set())

        # Current model indicator
        is_cloud = self._current_llm_model in cloud_set
        tag = "☁️" if is_cloud else "💻"
        current_action = llm_menu.addAction(f"✓ {tag} {self._current_llm_model}")
        current_action.setEnabled(False)
        llm_menu.addSeparator()

        # Show cloud models first (if any)
        cloud_shown = False
        local_shown = False
        for model_id in self._available_models:
            if model_id == self._current_llm_model:
                continue
            is_cloud = model_id in cloud_set
            if is_cloud and not cloud_shown:
                header = llm_menu.addAction("── ☁️ 云端 ──")
                header.setEnabled(False)
                cloud_shown = True
            elif not is_cloud and not local_shown:
                if cloud_shown:
                    llm_menu.addSeparator()
                header = llm_menu.addAction("── 💻 本地 ──")
                header.setEnabled(False)
                local_shown = True
            tag = "☁️" if is_cloud else "💻"
            action = llm_menu.addAction(f"  {tag} {model_id}")
            action.triggered.connect(
                lambda checked, m=model_id: self._ctx_select_model(m)
            )

        llm_menu.addSeparator()
        llm_menu.addAction("🔄 刷新列表", self._fetch_models_async)

    def _ctx_select_model(self, model_id: str):
        """Select a different LLM model for chat.
        Calls bridge API to pre-load model, so next chat request has it ready.
        """
        try:
            old = self._current_llm_model
            self._current_llm_model = model_id
            log.info("LLM model: %s → %s", old, model_id)

            # Persist the selection immediately
            _save_last_llm_model(model_id)

            # Notify user via Live2D tip
            is_cloud = model_id in getattr(self, '_cloud_model_set', set())
            if is_cloud:
                self.notify_live2d_tip(f"切换到 ☁️ {model_id}")
            else:
                self.notify_live2d_tip(f"正在加载 💻 {model_id}...")
                # Pre-load local model via bridge API (async, don't block UI)
                self._preload_model_async(model_id)

            # Update chat dock status if it exists
            app = QApplication.instance()
            if app and hasattr(app, '_icarus_pet'):
                pet = app._icarus_pet
                if hasattr(pet, 'chat_dock') and pet.chat_dock:
                    try:
                        pet.chat_dock.update_model(model_id)
                    except RuntimeError as exc:
                        log.warning("chat_dock.update_model failed: %s", exc)
                # Sync model to audio engine for voice
                if hasattr(pet, 'audio') and pet.audio:
                    try:
                        pet.audio.set_model(model_id)
                    except RuntimeError as exc:
                        log.warning("audio.set_model failed: %s", exc)
        except Exception as exc:
            log.error("_ctx_select_model CRASHED: %s", exc, exc_info=True)

    def _preload_model_async(self, model_id: str):
        """Pre-load model on llama-server via bridge API (async, non-blocking).

        After completion (success or failure), shows a Live2D tip to confirm.
        """
        def worker():
            import urllib.request
            success = False
            detail = ""
            try:
                # Try bridge first (:7860 with /v1/models/load), then llama-server
                # directly (:8080 with /models/load — no v1 prefix for direct access).
                attempts = [
                    (7860, "/v1/models/load"),
                    (8080, "/models/load"),
                ]
                for port, path in attempts:
                    try:
                        url = f"http://127.0.0.1:{port}{path}"
                        body = json.dumps({"model": model_id}).encode("utf-8")
                        req = urllib.request.Request(
                            url, data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=30.0) as resp:
                            result = resp.read().decode("utf-8")
                            log.info("model pre-load %s on :%d%s: %s", model_id, port, path, result[:200])
                        success = True
                        detail = f":{port}{path}"
                        break  # success
                    except urllib.error.HTTPError as exc:
                        body_text = exc.read().decode("utf-8", errors="replace")
                        # 400 = already running (not an error)
                        if exc.code == 400:
                            log.info("model %s already loaded (bridge says running): %s", model_id, body_text[:100])
                            success = True
                            detail = "(already loaded)"
                            break
                        log.debug("pre-load :%d%s failed HTTP %d: %s", port, path, exc.code, body_text[:100])
                        detail = f"HTTP {exc.code}: {body_text[:80]}"
                        continue
                    except Exception as exc:
                        log.debug("pre-load :%d%s failed: %s", port, path, exc)
                        detail = str(exc)[:80]
                        continue
            except Exception as exc:
                log.debug("model pre-load failed: %s", exc)
                detail = str(exc)[:80]

            # Show Live2D tip on main thread

            def tip():
                if success:
                    is_cloud = model_id in getattr(self, '_cloud_model_set', set())
                    tag = "☁️" if is_cloud else "💻"
                    self.notify_live2d_tip(f"{tag} {model_id} ✓")
                    log.info("model pre-load SUCCESS: %s (%s)", model_id, detail)
                else:
                    self.notify_live2d_tip(f"⚠️ 模型加载失败: {detail}")
                    log.warning("model pre-load FAILED for %s: %s", model_id, detail)
            QTimer.singleShot(0, tip)

        threading.Thread(target=worker, daemon=True).start()


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
        p.drawText(icon.rect(), Qt.AlignmentFlag.AlignCenter, "Alpha")
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

        # ─── 🤖 LLM 模型切换 (tray menu) ───
        tray_model_label = self.window._current_llm_model if hasattr(self.window, '_current_llm_model') else "Phi-4-Mini-3.8B-Q4_K_L"
        self._llm_tray_menu = menu.addMenu(f"🤖 LLM 模型: {tray_model_label}")
        self._llm_tray_menu.addAction("⏳ 加载模型列表…", self._fetch_tray_models)
        # Trigger initial fetch
        QTimer.singleShot(2000, self._fetch_tray_models)

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
        """Get the IkarosApp's NeuroClient via window's app instance."""
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

    # ─── Tray LLM model submenu ───

    def _fetch_tray_models(self):
        """Fetch models: reuse PetWindow's data if available, else fetch fresh."""
        # If PetWindow already fetched models, reuse them
        if hasattr(self.window, '_available_models') and self.window._available_models:
            self._populate_tray_llm(
                self.window._available_models,
                getattr(self.window, '_cloud_model_set', set()),
            )
            return
        # Otherwise trigger PetWindow's fetch and wait for it
        if hasattr(self.window, '_fetch_models_async') and not getattr(self.window, '_models_fetching', False):
            self.window._fetch_models_async()
        # Poll until PetWindow has data (max 5s)
        def poll():
            if hasattr(self.window, '_available_models') and self.window._available_models:
                self._populate_tray_llm(
                    self.window._available_models,
                    getattr(self.window, '_cloud_model_set', set()),
                )
            else:
                QTimer.singleShot(500, poll)
        QTimer.singleShot(500, poll)

    def _populate_tray_llm(self, models: list[str], cloud_set: set[str] | None = None):
        """Rebuild the tray LLM submenu with fetched models."""
        self._llm_tray_menu.clear()
        # Get current model from PetWindow
        current = getattr(self.window, '_current_llm_model', "Phi-4-Mini-3.8B-Q4_K_L")
        cloud_set = cloud_set or set()
        is_cloud = current in cloud_set
        tag = "☁️" if is_cloud else "💻"
        self._llm_tray_menu.setTitle(f"🤖 LLM: {tag} {current}")

        if not models:
            no_action = self._llm_tray_menu.addAction("(无可用模型)")
            no_action.setEnabled(False)
            self._llm_tray_menu.addAction("🔄 重试", self._fetch_tray_models)
            return

        # Current model (checked)
        cur_action = self._llm_tray_menu.addAction(f"✓ {tag} {current}")
        cur_action.setEnabled(False)
        self._llm_tray_menu.addSeparator()

        # Other models (cloud first, then local)
        cloud_models = [m for m in models if m in cloud_set and m != current]
        local_models = [m for m in models if m not in cloud_set and m != current]
        for model_id in cloud_models:
            action = self._llm_tray_menu.addAction(f"  ☁️ {model_id}")
            action.triggered.connect(
                lambda checked, m=model_id: self._tray_select_model(m)
            )
        if cloud_models and local_models:
            self._llm_tray_menu.addSeparator()
        for model_id in local_models:
            action = self._llm_tray_menu.addAction(f"  💻 {model_id}")
            action.triggered.connect(
                lambda checked, m=model_id: self._tray_select_model(m)
            )
        self._llm_tray_menu.addSeparator()
        self._llm_tray_menu.addAction("🔄 刷新列表", self._fetch_tray_models)

    def _tray_select_model(self, model_id: str):
        """Handle model selection from tray menu."""
        if hasattr(self.window, '_ctx_select_model'):
            self.window._ctx_select_model(model_id)
        # Update tray submenu title
        self._llm_tray_menu.setTitle(f"🤖 LLM 模型: {model_id}")

def _quit(self):
    QApplication.quit()


# ─── Chat Dock (4C: 文本聊天入口) ───

LLAMA_CHAT_URL = "http://127.0.0.1:7860/v1/chat/completions"
LLAMA_CHAT_TIMEOUT_S = 30.0
SESSION_API_BASE = "http://127.0.0.1:7860/v1/sessions"


async def bridge_chat(text: str, *, profile: str = "default",
                   history: list = None, model: str = "Phi-4-Mini-3.8B-Q4_K_L",
                   session_id: str = "") -> str:
    """4C: 调 Hermes bridge /v1/chat/completions — cloud auto-flip 已实现.

    Args:
        text: 用户输入
        profile: Hermes profile (default)
        history: A — 之前对话列表 [{role:user|assistant, content:str}, ...]
                 (默认 None — 单条消息)
        model: 默认 Phi-4-Mini-3.8B-Q4_K_L (Quest Path A 改默认值)
        session_id: Part B — bridge session ID (传 X-Session-Id header)

    Returns:
        assistant 回复文本 (string)
    """
    # A: 拼 messages = [*history, {user msg}]
    msgs = list(history) if history else []
    msgs.append({"role": "user", "content": text})

    if httpx is None:
        # Fallback: urllib (同步, 不优雅但能用)
        import json, urllib.request
        body = json.dumps({
            "model": model,
            "messages": msgs,
            "max_tokens": 200,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if session_id:
            headers["X-Session-Id"] = session_id
        req = urllib.request.Request(
            LLAMA_CHAT_URL,
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=LLAMA_CHAT_TIMEOUT_S) as r:
            data = json.loads(r.read())
    else:
        extra_headers = {}
        if session_id:
            extra_headers["X-Session-Id"] = session_id
        async with httpx.AsyncClient(timeout=LLAMA_CHAT_TIMEOUT_S) as c:
            r = await c.post(LLAMA_CHAT_URL, json={
                "model": model,
                "messages": msgs,
                "max_tokens": 200,
            }, headers=extra_headers)
            data = r.json()
    return data["choices"][0]["message"]["content"]


async def edge_tts_mp3(text: str, voice: str = "zh-CN-XiaoxiaoNeural") -> bytes:
    """4C: 用 edge-tts 把文字 → MP3 bytes (4B audio_engine.play_mp3_bytes 接).

    Returns:
        完整 MP3 file bytes (可能空 bytes 如果 edge-tts 失败 — caller 检查)
    """
    # 2026-06-29 哥哥 axiom Rule 8: TTS 禁念 markdown 强调符号
    # edge-tts 把 ** 念成"星号", 听感差. LLM 应该已禁, 但兜底 strip 一下.
    tts_text = _strip_markdown_emphasis(text)
    try:
        communicate = edge_tts.Communicate(tts_text, voice=voice)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)
    except Exception as exc:
        log.warning("edge-tts failed: %s", exc)
        return b""


def _strip_markdown_emphasis(text: str) -> str:
    """TTS 兜底: 去掉 markdown 强调符号, 但保留文字内容.

    - **字** / __字__ → 字 (双星 = 粗体)
    - *字* / _字_   → 字 (单星 = 斜体)
    - ***字***      → 字 (粗斜体)
    - 保留 markdown 链接/标题/代码/列表 (webui 渲染靠这些, TTS 念不到)

    设计: 只 strip 强调, 不动其他 markdown. webui 显示仍正常.
    14 case 单测 ALL PASS (2026-06-29): 粗体/斜体/粗斜体/列表/链接/标题/单词内 _
    """
    import re
    # 三星 (粗斜体) — 先处理, 否则会被前两条吃掉
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'___(.+?)___',     r'\1', text, flags=re.DOTALL)
    # 双星 (粗体)
    text = re.sub(r'\*\*(.+?)\*\*',   r'\1', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__',       r'\1', text, flags=re.DOTALL)
    # 单星 (斜体) — *X*  X首尾非空白, 列表 * item 不会被吃 (item 后是空白但 \S 限制首字符)
    text = re.sub(r'\*(\S(?:[^*]*\S)?)\*', r'\1', text)
    # 下划线斜体 — 同上, \b word boundary 避免吃 hello_world
    text = re.sub(r'\b_(\S(?:[^_]*\S)?)_\b', r'\1', text)
    return text


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
        self.status_label = QLabel("intent: —  |  model: MiniMax-M3")
        self.status_label.setStyleSheet(
            "QLabel { font-size: 9pt; color: #888; padding: 2px 4px; }"
        )
        v.addWidget(self.status_label)

        # In-memory chat history (A: in-memory persistence within pet lifetime)
        self._chat_history: deque = deque(maxlen=40)  # 最近 20 轮 (40 条)
        self._session_id: str = f"pet_{int(time.time())}_{id(self)}"
        self._bridge_session_id: str = ""  # Part B: bridge session ID (created on first send)

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

        # A: append user message to in-memory history (before chat call)
        self._chat_history.append({"role": "user", "content": text})

        # 2. Disable UI during request
        self.input_edit.setEnabled(False)
        self.send_btn.setEnabled(False)

        # 3. Async chain — call bridge_chat, then TTS, then re-enable UI
        # Pass history (snapshot) so bridge sees full conversation context
        history_snapshot = list(self._chat_history)
        self._run_chat_chain(text, intent, history_snapshot)

    def _run_chat_chain(self, text: str, intent: str, history: list = None):
        """QTimer.singleShot 把 coroutine 送进 asyncio loop.

        Qt 没有原生 async 支持, 用 QTimer 0ms 触发 + 在 QThread 里跑 asyncio
        的常见做法: 用 asyncio.run_coroutine_threadsafe 把 coroutine 送进主 loop.
        但我们没显式主 loop — 改用 threading 直接 run.

        A: history 参数携带完整对话上下文, 让 LLM 看到之前的对话.
        B: 同时持久化 user msg + assistant reply 到 bridge session.
        """
        # Get current model from PetWindow (shared state)
        model = self._get_current_model()

        def worker():
            try:
                # 在新 loop 里跑 (避免 Qt 主线程 asyncio 冲突)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # Part B: ensure bridge session exists
                    sid = self._bridge_session_id
                    if not sid:
                        sid = self._ensure_session()
                    # Part B: persist user message to bridge session
                    if sid:
                        self._post_session_msg(sid, "user", text)

                    reply = loop.run_until_complete(
                        bridge_chat(text, history=history, model=model, session_id=sid)
                    )

                    # Part B: persist assistant reply
                    if sid and reply and not reply.startswith("⚠️"):
                        self._post_session_msg(sid, "assistant", reply)
                finally:
                    loop.close()
                # 回到 Qt 主线程更新 UI
                QTimer.singleShot(0, lambda: self._on_reply(reply, intent))
            except Exception as exc:
                log.error("chat chain failed: %s", exc)
                QTimer.singleShot(0, lambda: self._on_reply(f"⚠️ {exc}", intent))

        threading.Thread(target=worker, daemon=True).start()

    # ─── Session persistence helpers (Part B) ───

    def _ensure_session(self) -> str:
        """Create a session on the bridge if not already created.
        Returns the session_id (empty string on failure)."""
        if self._bridge_session_id:
            return self._bridge_session_id
        import urllib.request
        body = json.dumps({"source": "pet", "model": self._get_current_model()}).encode("utf-8")
        req = urllib.request.Request(
            SESSION_API_BASE,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as r:
                data = json.loads(r.read())
                sid = data.get("session_id", "")
                if sid:
                    self._bridge_session_id = sid
                    log.info("bridge session created: %s", sid)
                return sid
        except Exception as exc:
            log.warning("session create failed: %s", exc)
            return ""

    def _post_session_msg(self, session_id: str, role: str, content: str):
        """POST a message to the bridge session."""
        import urllib.request
        url = f"{SESSION_API_BASE}/{session_id}/messages"
        body = json.dumps({"role": role, "content": content}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as r:
                resp = json.loads(r.read())
                log.debug("session msg appended: %s/%s (#%d)", session_id, role, resp.get("total_messages", 0))
        except Exception as exc:
            log.warning("session msg append failed (%s/%s): %s", session_id, role, exc)

    def _get_current_model(self) -> str:
        """Get the currently selected LLM model from PetWindow."""
        app = QApplication.instance()
        if app and hasattr(app, '_icarus_pet'):
            pet = app._icarus_pet
            if hasattr(pet, 'window') and hasattr(pet.window, '_current_llm_model'):
                return pet.window._current_llm_model
        return "Phi-4-Mini-3.8B-Q4_K_L"  # fallback default

    def _on_reply(self, reply: str, intent: str):
        """Reply 回来后: history + TTS + 重启用 UI."""
        # A: append assistant reply to history (so next round sees it)
        if reply and not reply.startswith("⚠️"):
            self._chat_history.append({"role": "assistant", "content": reply})
        self._append_history(f"[伊卡洛斯] {reply}")
        model = self._get_current_model()
        self._set_status(f"intent: {intent}  |  {model} ✓")
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

    def update_model(self, model_id: str):
        """Update displayed model name when user switches."""
        current = self.status_label.text()
        # Replace model portion of status text
        if "model:" in current:
            prefix = current.split("|")[0].strip()
            self.status_label.setText(f"{prefix}  |  model: {model_id}")
        else:
            self.status_label.setText(f"{current}  |  model: {model_id}")


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

class IkarosApp(QObject):
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
        self.window.show_neuro_state(state.lower())

    def _on_bubble(self, text: str, duration: int):
        self.window.setWindowTitle(f"🪶 {text[:20]}")
        self.window.show_bubble(text, duration)

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
            winreg.SetValueEx(key, "IkarosDesktopPet", 0,
                              winreg.REG_SZ, cmd)
        log.info("autostart: registered")
    except Exception as exc:
        log.warning("autostart: %s", exc)


def main():
    # Kill proxy env
    for k in list(os.environ.keys()):
        if 'proxy' in k.lower():
            os.environ.pop(k, None)

    # ── Singleton check: 只允许一个桌宠进程 ──
    pet_lock = require_singleton_or_exit()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    register_autostart()

    pet = IkarosApp()
    # Expose to NeuroClient lookup (tray needs to find pet.neuro)
    app._icarus_pet = pet
    rc = pet.run()
    pet.cleanup()
    pet_lock.release()
    sys.exit(rc)


if __name__ == "__main__":
    main()
