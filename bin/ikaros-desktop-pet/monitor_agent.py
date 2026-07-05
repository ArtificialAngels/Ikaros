#!/usr/bin/env python3
"""
🪶 Ikaros 监控代理 — 桌宠状态监控 + 崩溃自动重启

独立 PyQt6 窗口，显示:
- 🎤 STT 语音识别内容
- 💬 LLM 回答内容
- 🧠 AI 状态 (听/思考/说话/待机)
- 🔄 崩溃自动重启桌宠
- 📊 CPU/内存/运行时间

启动方式: 桌宠右键菜单「📊 监控面板」
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QRect, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

# ── Paths ──
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
_MAIN_PY = _HERE / "main.py"
_LOCK_PATH = _ROOT / "data" / "logs" / "ikaros-pet.lock"
_JSONL_PATH = _ROOT / "data" / "logs" / "ikaros-monitor.jsonl"
_EXIT_FLAG = _ROOT / "data" / "logs" / "ikaros-pet.exit"

# ── Timing ──
EVENT_POLL_MS = 500          # JSONL 文件轮询间隔
HEALTH_CHECK_MS = 3000       # 健康检查间隔
NEURO_POLL_MS = 2000         # Neuro 状态轮询
RESTART_WAIT_MS = 2500       # 检测到死亡后等待自重启的时间
POST_RESTART_COOLDOWN_MS = 5000  # 重启后冷却时间，等新 PID 写入
MAX_TABLE_ROWS = 500

# ── Event styling ──
_EVENT_ICON = {
    "stt": "🎤", "llm_reply": "💬", "state": "🧠",
    "status": "📋", "neuro_state": "🧠", "error": "⚠️",
    "voice_activity": "🎙️", "model_info": "🤖",
}
_EVENT_COLOR = {
    "stt": "#4fc3f7", "llm_reply": "#81c784", "state": "#ffb74d",
    "status": "#90a4ae", "neuro_state": "#ce93d8", "error": "#f44336",
    "voice_activity": "#4fc3f7", "model_info": "#80cbc4",
}
_STATE_LABEL = {
    "listening": "👂 听", "thinking": "🧠 思考", "speaking": "🔊 说话",
    "idle": "💤 待机", "bored": "😴 无聊",
    "LISTENING": "👂 听", "THINKING": "🧠 思考", "SPEAKING": "🔊 说话",
}
_STATE_COLOR = {
    "listening": "#4fc3f7", "thinking": "#ffb74d",
    "speaking": "#81c784", "idle": "#90a4ae",
}

# ── Dashboard color palette ──
_BG = "#0d1117"           # main background
_CARD_BG = "#161b22"      # card background
_CARD_BORDER = "#21262d"  # card border
_ACCENT = "#58a6ff"       # accent blue
_TEXT = "#c9d1d9"         # primary text
_TEXT_DIM = "#8b949e"     # dim text
_GREEN = "#3fb950"
_RED = "#f85149"
_YELLOW = "#d29922"
_PURPLE = "#bc8cff"
_CYAN = "#39d353"

# ── Event bubble colors ──
_BUBBLE = {
    "stt":           ("#1a3a5c", "#4fc3f7"),
    "llm_reply":     ("#1a3c2a", "#81c784"),
    "state":         ("#2a2a1a", "#ffb74d"),
    "status":        ("#1a1a2e", "#90a4ae"),
    "neuro_state":   ("#2a1a3a", "#ce93d8"),
    "error":         ("#3a1a1a", "#f44336"),
    "voice_activity":("#1a3a5c", "#4fc3f7"),
    "model_info":    ("#1a2a2a", "#80cbc4"),
}

# ── Filter labels ──
_FILTERS = [
    ("all",    "全部",   None),
    ("stt",    "🎤 语音", {"stt", "voice_activity"}),
    ("llm",    "💬 回答", {"llm_reply"}),
    ("state",  "🧠 状态", {"state", "neuro_state"}),
    ("error",  "⚠️ 错误", {"error"}),
    ("sys",    "📋 系统", {"status", "module_status", "heartbeat"}),
]


# ══════════════════════════════════════════
#  Process helpers
# ══════════════════════════════════════════

def _get_pet_pid() -> Optional[int]:
    """从 singleton lock 文件读取桌宠 PID."""
    try:
        if not _LOCK_PATH.exists():
            return None
        for line in _LOCK_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("pid="):
                return int(line.split("=", 1)[1])
    except Exception:
        pass
    return None


def _is_process_alive(pid: int) -> bool:
    """检查 PID 对应的进程是否存活."""
    try:
        import psutil
        return psutil.Process(pid).is_running()
    except ImportError:
        pass
    except Exception:
        return False
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in r.stdout
    except Exception:
        return False


def _is_pet_alive() -> bool:
    pid = _get_pet_pid()
    if pid is None:
        return False
    return _is_process_alive(pid)


def _restart_pet() -> bool:
    try:
        subprocess.Popen(
            [sys.executable, str(_MAIN_PY)],
            cwd=str(_HERE),
            creationflags=subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
        return True
    except Exception as e:
        print(f"[monitor] restart failed: {e}", flush=True)
        return False


def _clean_exit_flag():
    try:
        if _EXIT_FLAG.exists():
            _EXIT_FLAG.unlink()
    except Exception:
        pass


# ══════════════════════════════════════════
#  Monitor Engine (background timers)
# ══════════════════════════════════════════

class MonitorEngine(QObject):
    """监控引擎: JSONL tail + 健康检查 + 资源监控."""

    event_received = pyqtSignal(dict)
    pet_status_changed = pyqtSignal(bool)
    neuro_status_changed = pyqtSignal(dict)
    resource_updated = pyqtSignal(float, float, float)  # cpu%, mem_mb, uptime_sec

    def __init__(self):
        super().__init__()
        self._running = False
        self._last_read_pos = 0
        self._prev_pet_alive = False
        self._restart_count = 0
        self._auto_restart = True
        self._pet_start_time = 0.0

        # 健康检查状态机: "normal" | "dead" | "cooldown"
        self._health_state = "normal"

        # Timers
        self._file_timer = QTimer(self)
        self._file_timer.timeout.connect(self._check_new_events)

        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._check_health)

        self._resource_timer = QTimer(self)
        self._resource_timer.timeout.connect(self._emit_resources)

    # ── Lifecycle ──

    def start(self):
        if self._running:
            return
        self._running = True
        if _JSONL_PATH.exists():
            self._last_read_pos = _JSONL_PATH.stat().st_size

        self._prev_pet_alive = _is_pet_alive()
        self.pet_status_changed.emit(self._prev_pet_alive)

        self._file_timer.start(EVENT_POLL_MS)
        self._health_timer.start(HEALTH_CHECK_MS)
        self._resource_timer.start(2000)
        self._start_neuro_poll()
        self._check_new_events()

    def stop(self):
        self._running = False
        self._file_timer.stop()
        self._health_timer.stop()
        self._resource_timer.stop()

    # ── Resource monitoring ──

    def _emit_resources(self):
        pid = _get_pet_pid()
        if pid is None:
            self.resource_updated.emit(0.0, 0.0, 0.0)
            return
        try:
            import psutil
            p = psutil.Process(pid)
            cpu = p.cpu_percent(interval=0)
            mem = p.memory_info().rss / (1024 * 1024)
            uptime = time.time() - p.create_time()
            self.resource_updated.emit(cpu, mem, uptime)
        except ImportError:
            self.resource_updated.emit(-1, 0, 0)
        except Exception:
            self.resource_updated.emit(0.0, 0.0, 0.0)

    # ── JSONL tail ──

    def _check_new_events(self):
        if not _JSONL_PATH.exists():
            return
        try:
            size = _JSONL_PATH.stat().st_size
            if size <= self._last_read_pos:
                return
            with open(_JSONL_PATH, "r", encoding="utf-8") as f:
                f.seek(self._last_read_pos)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        self.event_received.emit(ev)
                    except json.JSONDecodeError:
                        continue
                self._last_read_pos = f.tell()
        except Exception:
            pass

    # ── Health check ──

    def _check_health(self):
        if not self._running:
            return
        if self._health_state == "cooldown":
            return

        alive = _is_pet_alive()

        if self._health_state == "normal":
            if alive != self._prev_pet_alive:
                self._prev_pet_alive = alive
                self.pet_status_changed.emit(alive)
            if alive:
                return
            if _EXIT_FLAG.exists():
                try:
                    _EXIT_FLAG.unlink()
                except Exception:
                    pass
                return
            if not self._auto_restart:
                return
            self._health_state = "dead"
            self._prev_pet_alive = False
            QTimer.singleShot(RESTART_WAIT_MS, self._on_death_wait_end)

        elif self._health_state == "dead":
            pass

    def _on_death_wait_end(self):
        if not self._running or self._health_state != "dead":
            return
        if _is_pet_alive():
            self._health_state = "normal"
            self._prev_pet_alive = True
            self.pet_status_changed.emit(True)
            return

        self._restart_count += 1
        ok = _restart_pet()
        if ok:
            self.event_received.emit({
                "ts": time.time(),
                "type": "status",
                "text": f"🔄 桌宠已自动重启 (第 {self._restart_count} 次)",
            })
            self._prev_pet_alive = True
            self.pet_status_changed.emit(True)

        self._health_state = "cooldown"
        QTimer.singleShot(POST_RESTART_COOLDOWN_MS, self._on_cooldown_end)

    def _on_cooldown_end(self):
        if self._health_state == "cooldown":
            self._health_state = "normal"

    # ── Neuro status poll ──

    def _start_neuro_poll(self):
        pass


# ══════════════════════════════════════════
#  Monitor Window (UI) — Dashboard v2
# ══════════════════════════════════════════

def _fmt_uptime(sec: float) -> str:
    if sec <= 0:
        return "--"
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _make_card(parent=None, title: str = "", value: str = "--",
               color: str = _TEXT, icon: str = "") -> tuple:
    card = QFrame(parent)
    card.setStyleSheet(f"""
        QFrame {{
            background: {_CARD_BG};
            border: 1px solid {_CARD_BORDER};
            border-radius: 8px;
            padding: 8px 12px;
        }}
    """)
    card.setMinimumHeight(68)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(10, 6, 10, 6)
    lay.setSpacing(2)

    title_lbl = QLabel(f"{icon}  {title}" if icon else title)
    title_lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px; background: transparent; border: none;")
    lay.addWidget(title_lbl)

    val_lbl = QLabel(value)
    val_lbl.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: bold; background: transparent; border: none;")
    lay.addWidget(val_lbl)

    return card, val_lbl


class _EventBubble(QWidget):
    """单条事件的聊天气泡样式 widget."""

    def __init__(self, ts: float, etype: str, text: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(8)

        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        bg, fg = _BUBBLE.get(etype, ("#1a1a2e", _TEXT))
        icon = _EVENT_ICON.get(etype, "📋")

        t_lbl = QLabel(time_str)
        t_lbl.setFixedWidth(58)
        t_lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px; background: transparent; border: none;")
        t_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        lay.addWidget(t_lbl)

        bubble = QFrame()
        bubble.setStyleSheet(f"""
            QFrame {{
                background: {bg};
                border-left: 3px solid {fg};
                border-radius: 6px;
                padding: 6px 10px;
            }}
        """)
        b_lay = QVBoxLayout(bubble)
        b_lay.setContentsMargins(8, 4, 8, 4)
        b_lay.setSpacing(0)

        header = QLabel(f"{icon} {etype}")
        header.setStyleSheet(f"color: {fg}; font-size: 10px; font-weight: bold; background: transparent; border: none;")
        b_lay.addWidget(header)

        content = QLabel(text[:300])
        content.setWordWrap(True)
        content.setStyleSheet(f"color: {_TEXT}; font-size: 12px; background: transparent; border: none;")
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        b_lay.addWidget(content)

        lay.addWidget(bubble, 1)


class MonitorWindow(QMainWindow):
    WIDTH, HEIGHT = 720, 600

    def __init__(self):
        super().__init__()
        self._engine = MonitorEngine()
        self._engine.event_received.connect(self._on_event)
        self._engine.pet_status_changed.connect(self._on_pet_status)
        self._engine.neuro_status_changed.connect(self._on_neuro_status)
        self._engine.resource_updated.connect(self._on_resources)

        self._current_model = ""
        self._module_status = {
            "stt": ("unknown", 0.0),
            "tts": ("unknown", 0.0),
            "voice_ws": ("unknown", 0.0),
            "stt_local": ("unknown", 0.0),
        }
        self._stale_timer = QTimer(self)
        self._stale_timer.timeout.connect(self._check_stale_modules)
        self._stale_timer.start(10000)

        self._active_filter = None
        self._search_text = ""
        self._all_events = []

        self._build_ui()

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.right() - self.WIDTH - 16, geo.bottom() - self.HEIGHT - 80)

    def _build_ui(self):
        self.setWindowTitle("🪶 Ikaros Monitor")
        self.resize(self.WIDTH, self.HEIGHT)
        self.setMinimumSize(480, 360)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        central = QWidget()
        central.setObjectName("root")
        central.setStyleSheet(f"""
            #root {{
                background: {_BG};
                border: 1px solid {_CARD_BORDER};
                border-radius: 12px;
            }}
            QWidget {{ color: {_TEXT}; font-family: 'Microsoft YaHei','Segoe UI',sans-serif; font-size: 12px; }}
        """)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(1, 1, 1, 1)
        root_layout.setSpacing(0)

        # ── Title bar ──
        self._build_title_bar(root_layout)
        self._install_drag(self._title_bar)

        # ── Scroll area ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {_BG}; border: none; }}")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setStyleSheet(f"background: {_BG};")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(12, 8, 12, 8)
        self._content_layout.setSpacing(8)

        self._build_status_cards()
        self._build_module_cards()
        self._build_filter_bar()
        self._build_event_log()
        self._build_bottom_bar()

        scroll.setWidget(content)
        root_layout.addWidget(scroll)

        self._drag_pos = None

    def _build_title_bar(self, parent_layout):
        title = QWidget()
        title.setFixedHeight(36)
        title.setStyleSheet(f"background: {_CARD_BG}; border-radius: 11px 11px 0 0;")
        tl = QHBoxLayout(title)
        tl.setContentsMargins(14, 0, 8, 0)

        self._title_lbl = QLabel("🪶 Ikaros Monitor")
        self._title_lbl.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {_ACCENT}; background: transparent; border: none;")
        tl.addWidget(self._title_lbl)
        tl.addStretch()

        min_btn = QPushButton("─")
        min_btn.setFixedSize(24, 24)
        min_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none; color: {_TEXT_DIM}; font-size: 14px; font-weight: bold; }}
            QPushButton:hover {{ color: {_TEXT}; }}
        """)
        min_btn.clicked.connect(self.showMinimized)
        tl.addWidget(min_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none; color: {_TEXT_DIM}; font-size: 13px; font-weight: bold; }}
            QPushButton:hover {{ color: {_RED}; }}
        """)
        close_btn.clicked.connect(self.close)
        tl.addWidget(close_btn)

        parent_layout.addWidget(title)
        self._title_bar = title

    def _build_status_cards(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        c1, self._alive_val = _make_card(title="桌宠", value="检测中...", color=_YELLOW, icon="📡")
        row.addWidget(c1, 1)

        c2, self._state_val = _make_card(title="AI 状态", value="💤 待机", color=_TEXT_DIM, icon="🧠")
        row.addWidget(c2, 1)

        c3, self._resource_val = _make_card(title="资源", value="--", color=_TEXT_DIM, icon="📊")
        row.addWidget(c3, 1)

        c4, self._model_val = _make_card(title="模型", value="--", color="#80cbc4", icon="🤖")
        row.addWidget(c4, 1)

        self._content_layout.addLayout(row)

    def _build_module_cards(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        stt_card = QFrame()
        stt_card.setStyleSheet(f"QFrame {{ background: {_CARD_BG}; border: 1px solid {_CARD_BORDER}; border-radius: 8px; }}")
        stt_card.setMinimumHeight(56)
        stt_lay = QHBoxLayout(stt_card)
        stt_lay.setContentsMargins(12, 8, 12, 8)

        stt_icon = QLabel("🎤")
        stt_icon.setStyleSheet("font-size: 20px; background: transparent; border: none;")
        stt_lay.addWidget(stt_icon)

        stt_info = QVBoxLayout()
        stt_info.setSpacing(0)
        stt_name = QLabel("STT 语音识别")
        stt_name.setStyleSheet(f"color: {_TEXT}; font-size: 12px; font-weight: bold; background: transparent; border: none;")
        stt_info.addWidget(stt_name)
        self._stt_detail = QLabel("FunASR paraformer-zh · 检测中")
        self._stt_detail.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px; background: transparent; border: none;")
        stt_info.addWidget(self._stt_detail)
        stt_lay.addLayout(stt_info)
        stt_lay.addStretch()

        self._stt_dot = QLabel("⚪")
        self._stt_dot.setStyleSheet("font-size: 16px; background: transparent; border: none;")
        stt_lay.addWidget(self._stt_dot)
        row.addWidget(stt_card, 1)

        tts_card = QFrame()
        tts_card.setStyleSheet(f"QFrame {{ background: {_CARD_BG}; border: 1px solid {_CARD_BORDER}; border-radius: 8px; }}")
        tts_card.setMinimumHeight(56)
        tts_lay = QHBoxLayout(tts_card)
        tts_lay.setContentsMargins(12, 8, 12, 8)

        tts_icon = QLabel("🔊")
        tts_icon.setStyleSheet("font-size: 20px; background: transparent; border: none;")
        tts_lay.addWidget(tts_icon)

        tts_info = QVBoxLayout()
        tts_info.setSpacing(0)
        tts_name = QLabel("TTS 语音合成")
        tts_name.setStyleSheet(f"color: {_TEXT}; font-size: 12px; font-weight: bold; background: transparent; border: none;")
        tts_info.addWidget(tts_name)
        self._tts_detail = QLabel("edge-tts · 检测中")
        self._tts_detail.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px; background: transparent; border: none;")
        tts_info.addWidget(self._tts_detail)
        tts_lay.addLayout(tts_info)
        tts_lay.addStretch()

        self._tts_dot = QLabel("⚪")
        self._tts_dot.setStyleSheet("font-size: 16px; background: transparent; border: none;")
        tts_lay.addWidget(self._tts_dot)
        row.addWidget(tts_card, 1)

        self._content_layout.addLayout(row)

    def _build_filter_bar(self):
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self._filter_btns = {}
        for key, label, types in _FILTERS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};
                    border-radius: 12px; padding: 4px 12px;
                    color: {_TEXT_DIM}; font-size: 11px;
                }}
                QPushButton:checked {{
                    background: {_ACCENT}; border-color: {_ACCENT};
                    color: #ffffff;
                }}
                QPushButton:hover {{ border-color: {_ACCENT}; }}
            """)
            btn.clicked.connect(lambda checked, k=key: self._set_filter(k))
            bar.addWidget(btn)
            self._filter_btns[key] = btn

        self._filter_btns["all"].setChecked(True)
        bar.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍 搜索事件...")
        self._search.setFixedWidth(160)
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background: {_CARD_BG}; border: 1px solid {_CARD_BORDER};
                border-radius: 12px; padding: 4px 12px;
                color: {_TEXT}; font-size: 11px;
            }}
            QLineEdit:focus {{ border-color: {_ACCENT}; }}
        """)
        self._search.textChanged.connect(self._on_search_changed)
        bar.addWidget(self._search)

        self._content_layout.addLayout(bar)

    def _build_event_log(self):
        self._event_list = QListWidget()
        self._event_list.setStyleSheet(f"""
            QListWidget {{
                background: {_BG};
                border: 1px solid {_CARD_BORDER};
                border-radius: 8px;
                outline: none;
            }}
            QListWidget::item {{ border-bottom: 1px solid {_CARD_BORDER}; padding: 0px; }}
            QListWidget::item:selected {{ background: transparent; }}
        """)
        self._event_list.setMinimumHeight(200)
        self._event_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_layout.addWidget(self._event_list, 1)

    def _build_bottom_bar(self):
        bar = QHBoxLayout()
        bar.setSpacing(8)

        restart_btn = QPushButton("🔄 重启桌宠")
        restart_btn.setStyleSheet(f"""
            QPushButton {{ background: {_RED}; border: none; border-radius: 6px; padding: 6px 16px; color: white; font-size: 12px; font-weight: bold; }}
            QPushButton:hover {{ background: #ff6b81; }}
        """)
        restart_btn.clicked.connect(self._manual_restart)
        bar.addWidget(restart_btn)

        self._auto_cb = QCheckBox("自动重启")
        self._auto_cb.setChecked(True)
        self._auto_cb.setStyleSheet(f"""
            QCheckBox {{ color: {_TEXT_DIM}; font-size: 11px; spacing: 4px; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {_CARD_BORDER}; border-radius: 3px; }}
            QCheckBox::indicator:checked {{ background: {_GREEN}; border-color: {_GREEN}; }}
        """)
        self._auto_cb.toggled.connect(self._on_auto_toggle)
        bar.addWidget(self._auto_cb)

        bar.addStretch()

        self._restart_lbl = QLabel("重启: 0")
        self._restart_lbl.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px;")
        bar.addWidget(self._restart_lbl)

        clear_btn = QPushButton("🗑 清空")
        clear_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: 1px solid {_CARD_BORDER}; border-radius: 6px; padding: 4px 12px; color: {_TEXT_DIM}; font-size: 11px; }}
            QPushButton:hover {{ border-color: {_TEXT_DIM}; color: {_TEXT}; }}
        """)
        clear_btn.clicked.connect(self._clear_events)
        bar.addWidget(clear_btn)

        self._content_layout.addLayout(bar)

    # ── Drag ──
    def _on_title_press(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def _on_title_move(self, e):
        if self._drag_pos is not None:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def _on_title_release(self, e):
        self._drag_pos = None
        e.accept()

    def _install_drag(self, title_widget):
        title_widget.mousePressEvent = self._on_title_press
        title_widget.mouseMoveEvent = self._on_title_move
        title_widget.mouseReleaseEvent = self._on_title_release

    # ── Filter & Search ──

    def _set_filter(self, key: str):
        for k, btn in self._filter_btns.items():
            btn.setChecked(k == key)
        _, _, types = next(f for f in _FILTERS if f[0] == key)
        self._active_filter = types
        self._rerender_events()

    def _on_search_changed(self, text: str):
        self._search_text = text.lower()
        self._rerender_events()

    def _rerender_events(self):
        self._event_list.clear()
        for ts, etype, text in self._all_events:
            if self._active_filter and etype not in self._active_filter:
                continue
            if self._search_text and self._search_text not in text.lower():
                continue
            self._add_bubble(ts, etype, text)

    def _add_bubble(self, ts: float, etype: str, text: str):
        bubble = _EventBubble(ts, etype, text)
        item = QListWidgetItem()
        item.setSizeHint(bubble.sizeHint())
        self._event_list.addItem(item)
        self._event_list.setItemWidget(item, bubble)
        self._event_list.scrollToBottom()

    def _clear_events(self):
        self._all_events.clear()
        self._event_list.clear()

    # ── Events ──

    _MODULE_LABELS = {
        "stt": "_stt_dot", "tts": "_tts_dot", "voice_ws": "_stt_dot",
    }

    def _update_module_indicator(self, module: str, status: str, ts: float):
        colors = {
            "running": (_GREEN, "🟢"),
            "connected": (_GREEN, "🟢"),
            "ready": (_GREEN, "🟢"),
            "stopped": (_RED, "🔴"),
            "disconnected": (_RED, "🔴"),
        }
        color, dot = colors.get(status, (_TEXT_DIM, "⚪"))

        if module in ("stt", "stt_local"):
            self._update_stt_indicator()
            return
        elif module == "tts":
            self._tts_dot.setText(dot)
            self._tts_dot.setStyleSheet(f"font-size: 16px; color: {color}; background: transparent; border: none;")
            if status in ("running", "ready"):
                self._tts_detail.setText("edge-tts · 就绪")
            elif status == "stopped":
                self._tts_detail.setText("edge-tts · 已停止")

    def _update_stt_indicator(self):
        local_status, _ = self._module_status.get("stt_local", ("unknown", 0))
        stt_status, _ = self._module_status.get("stt", ("unknown", 0))

        if local_status == "ready":
            self._stt_dot.setText("🟢")
            self._stt_dot.setStyleSheet(f"font-size: 16px; color: {_GREEN}; background: transparent; border: none;")
            self._stt_detail.setText("FunASR paraformer-zh · GPU 就绪")
        elif local_status == "failed":
            self._stt_dot.setText("🔴")
            self._stt_dot.setStyleSheet(f"font-size: 16px; color: {_RED}; background: transparent; border: none;")
            self._stt_detail.setText("FunASR · 加载失败")
        elif local_status == "unknown" and stt_status == "running":
            self._stt_dot.setText("🟡")
            self._stt_dot.setStyleSheet(f"font-size: 16px; color: {_YELLOW}; background: transparent; border: none;")
            self._stt_detail.setText("FunASR · 模型加载中...")
        elif stt_status == "stopped":
            self._stt_dot.setText("🔴")
            self._stt_dot.setStyleSheet(f"font-size: 16px; color: {_RED}; background: transparent; border: none;")
            self._stt_detail.setText("STT · 已停止")
        else:
            self._stt_dot.setText("⚪")
            self._stt_dot.setStyleSheet(f"font-size: 16px; color: {_TEXT_DIM}; background: transparent; border: none;")
            self._stt_detail.setText("FunASR paraformer-zh · 检测中")

    def _check_stale_modules(self):
        now = time.time()
        status, last_ts = self._module_status.get("tts", ("unknown", 0.0))
        if status in ("running",) and last_ts > 0 and now - last_ts > 30:
            self._tts_dot.setText("🟡")
            self._tts_dot.setStyleSheet(f"font-size: 16px; color: {_YELLOW}; background: transparent; border: none;")
            self._tts_detail.setText("edge-tts · 心跳超时")

    def _on_event(self, ev: dict):
        ts = ev.get("ts", time.time())
        etype = ev.get("type", "status")
        text = ev.get("text", ev.get("state", ""))

        if etype == "module_status":
            module = ev.get("module", "")
            self._update_module_indicator(module, text, ts)

        elif etype == "heartbeat":
            module = ev.get("module", "")
            if module in self._module_status:
                cur_status, _ = self._module_status[module]
                if module == "stt_local" and cur_status in ("unknown", "running"):
                    new_status = "ready"
                else:
                    new_status = cur_status if cur_status != "unknown" else "running"
                self._module_status[module] = (new_status, ts)
                self._update_module_indicator(module, new_status, ts)
            if not text:
                return

        elif etype == "voice_activity":
            self._stt_dot.setText("🔵")
            self._stt_dot.setStyleSheet(f"font-size: 16px; color: {_ACCENT}; background: transparent; border: none;")
            QTimer.singleShot(1200, self._update_stt_indicator)

        elif etype == "model_info":
            self._current_model = text
            short = text.split("/")[-1] if "/" in text else text
            self._model_val.setText(short)
            self._model_val.setToolTip(f"当前对话模型: {text}")

        elif etype == "state":
            state = text.lower()
            label = _STATE_LABEL.get(state, text)
            color = _STATE_COLOR.get(state, _TEXT_DIM)
            self._state_val.setText(label)
            self._state_val.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: bold; background: transparent; border: none;")

        self._all_events.append((ts, etype, text))
        if len(self._all_events) > MAX_TABLE_ROWS:
            self._all_events = self._all_events[-MAX_TABLE_ROWS:]

        if self._active_filter and etype not in self._active_filter:
            return
        if self._search_text and self._search_text not in text.lower():
            return

        self._add_bubble(ts, etype, text)

    def _on_pet_status(self, alive: bool):
        if alive:
            self._alive_val.setText("🟢 在线")
            self._alive_val.setStyleSheet(f"color: {_GREEN}; font-size: 16px; font-weight: bold; background: transparent; border: none;")
        else:
            self._alive_val.setText("🔴 离线")
            self._alive_val.setStyleSheet(f"color: {_RED}; font-size: 16px; font-weight: bold; background: transparent; border: none;")

    def _on_neuro_status(self, status: dict):
        if status.get("AI_thinking"):
            state = "thinking"
        elif status.get("AI_speaking"):
            state = "speaking"
        elif status.get("human_speaking"):
            state = "listening"
        else:
            state = "idle"
        label = _STATE_LABEL.get(state, state)
        color = _STATE_COLOR.get(state, _TEXT_DIM)
        self._state_val.setText(label)
        self._state_val.setStyleSheet(f"color: {color}; font-size: 16px; font-weight: bold; background: transparent; border: none;")
        self._restart_lbl.setText(f"重启: {self._engine._restart_count}")

    def _on_resources(self, cpu: float, mem: float, uptime: float):
        if cpu < 0:
            self._resource_val.setText("N/A")
            return
        parts = []
        if cpu > 0:
            parts.append(f"CPU {cpu:.0f}%")
        if mem > 0:
            parts.append(f"MEM {mem:.0f}MB")
        if uptime > 0:
            parts.append(f"⬆ {_fmt_uptime(uptime)}")
        text = " · ".join(parts) if parts else "--"
        self._resource_val.setText(text)
        self._resource_val.setToolTip(f"CPU: {cpu:.1f}%\n内存: {mem:.1f} MB\n运行时间: {_fmt_uptime(uptime)}")

        if uptime > 0:
            alive_text = f"🟢 在线 · {_fmt_uptime(uptime)}"
            pid = _get_pet_pid()
            if pid and _is_process_alive(pid):
                self._alive_val.setText(alive_text)

    def _manual_restart(self):
        _clean_exit_flag()
        ok = _restart_pet()
        if ok:
            self._on_event({"ts": time.time(), "type": "status", "text": "🔄 手动重启桌宠..."})

    def _on_auto_toggle(self, checked: bool):
        self._engine._auto_restart = checked

    def closeEvent(self, e):
        self._engine.stop()
        super().closeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(200, self._engine.start)


# ══════════════════════════════════════════

def main():
    for k in list(os.environ.keys()):
        if "proxy" in k.lower():
            os.environ.pop(k, None)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")

    win = MonitorWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
