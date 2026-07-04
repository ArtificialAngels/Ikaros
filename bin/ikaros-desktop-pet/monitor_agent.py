#!/usr/bin/env python3
"""
🪶 Ikaros 监控代理 — 桌宠状态监控 + 崩溃自动重启

独立 PyQt6 窗口，显示:
- 🎤 STT 语音识别内容
- 💬 LLM 回答内容
- 🧠 AI 状态 (听/思考/说话/待机)
- 🔄 崩溃自动重启桌宠

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

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QRect
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QHBoxLayout, QHeaderView, QLabel,
    QMainWindow, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

# ── Paths ──
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
_MAIN_PY = _HERE / "main.py"
_LOCK_PATH = _ROOT / "data" / "logs" / "ikaros-pet.lock"
_JSONL_PATH = _ROOT / "data" / "logs" / "ikaros-monitor.jsonl"
_EXIT_FLAG = _ROOT / "data" / "logs" / "ikaros-pet.exit"
# 去桥: 不再需要 _BRIDGE_BASE (桌宠独立运行)

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


# ══════════════════════════════════════════
#  Process helpers
# ══════════════════════════════════════════

def _get_pet_pid() -> Optional[int]:
    """从 singleton lock 文件读取桌宠 PID. 返回 None 如果文件不存在或格式错误."""
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
    # Fallback: tasklist
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in r.stdout
    except Exception:
        return False


def _is_pet_alive() -> bool:
    """检查桌宠进程是否存活. 先从 lock 读 PID，再检查进程."""
    pid = _get_pet_pid()
    if pid is None:
        return False
    return _is_process_alive(pid)


def _restart_pet() -> bool:
    """启动新桌宠进程 (DETACHED_PROCESS 使其独立于监控进程)."""
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
    """清除退出标记."""
    try:
        if _EXIT_FLAG.exists():
            _EXIT_FLAG.unlink()
    except Exception:
        pass


# ══════════════════════════════════════════
#  Monitor Engine (background timers)
#  ⚠ 所有回调必须非阻塞 — 严禁 time.sleep 在主线程
# ══════════════════════════════════════════

class MonitorEngine(QObject):
    """监控引擎: JSONL tail + 健康检查 + Neuro 轮询."""

    event_received = pyqtSignal(dict)
    pet_status_changed = pyqtSignal(bool)
    neuro_status_changed = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self._running = False
        self._last_read_pos = 0
        self._prev_pet_alive = False
        self._restart_count = 0
        self._auto_restart = True

        # 健康检查状态机: "normal" | "dead" | "cooldown"
        self._health_state = "normal"

        # Timers
        self._file_timer = QTimer(self)
        self._file_timer.timeout.connect(self._check_new_events)

        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._check_health)

    # ── Lifecycle ──

    def start(self):
        if self._running:
            return
        self._running = True
        if _JSONL_PATH.exists():
            self._last_read_pos = _JSONL_PATH.stat().st_size

        # 初始检测
        self._prev_pet_alive = _is_pet_alive()
        self.pet_status_changed.emit(self._prev_pet_alive)

        self._file_timer.start(EVENT_POLL_MS)
        self._health_timer.start(HEALTH_CHECK_MS)
        # Neuro 轮询在独立线程跑，不阻塞 UI
        self._start_neuro_poll()

        self._check_new_events()

    def stop(self):
        self._running = False
        self._file_timer.stop()
        self._health_timer.stop()
        # Neuro 线程会自己退出 (daemon)

    # ── JSONL tail ──

    def _check_new_events(self):
        """读取 JSONL 新增行并发送到 UI."""
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

    # ── Health check (完全异步，无阻塞) ──

    def _check_health(self):
        """
        健康检查 — 异步状态机:
          normal  → 检测到死亡 → dead
          dead    → 延迟后检查 → 自重启成功则 normal, 否则 restart + cooldown
          cooldown → 跳过检查，等冷却结束回 normal
        """
        if not self._running:
            return

        if self._health_state == "cooldown":
            # 冷却中，跳过检查
            return

        alive = _is_pet_alive()

        if self._health_state == "normal":
            # 状态变化通知
            if alive != self._prev_pet_alive:
                self._prev_pet_alive = alive
                self.pet_status_changed.emit(alive)

            if alive:
                return  # 一切正常

            # 进程挂了
            if _EXIT_FLAG.exists():
                try:
                    _EXIT_FLAG.unlink()
                except Exception:
                    pass
                return  # 主动退出不重启

            if not self._auto_restart:
                return

            # → 进入死亡等待状态，给自重启留时间
            self._health_state = "dead"
            self._prev_pet_alive = False
            QTimer.singleShot(RESTART_WAIT_MS, self._on_death_wait_end)

        elif self._health_state == "dead":
            # 由 _on_death_wait_end 触发，不再重复进
            pass

    def _on_death_wait_end(self):
        """死亡等待结束 — 检查是否自重启成功."""
        if not self._running or self._health_state != "dead":
            return

        if _is_pet_alive():
            # 自重启成功
            self._health_state = "normal"
            self._prev_pet_alive = True
            self.pet_status_changed.emit(True)
            return

        # 真死了，执行重启
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

        # → 进入冷却（等新进程写入 PID）
        self._health_state = "cooldown"
        QTimer.singleShot(POST_RESTART_COOLDOWN_MS, self._on_cooldown_end)

    def _on_cooldown_end(self):
        """冷却结束 — 恢复正常监控."""
        if self._health_state == "cooldown":
            self._health_state = "normal"

    # ── Neuro status poll (后台线程，不阻塞 UI) ──

    def _start_neuro_poll(self):
        """去桥: 不再轮询 bridge neuro status. 状态由音频引擎直驱 Live2D."""
        # 静默跳过 — audio_engine.on_state 已直连 Live2D (main.py:1623)
        pass


# ══════════════════════════════════════════
#  Monitor Window (UI)
# ══════════════════════════════════════════

class MonitorWindow(QMainWindow):
    WIDTH, HEIGHT = 640, 480

    def __init__(self):
        super().__init__()
        self._engine = MonitorEngine()
        self._engine.event_received.connect(self._on_event)
        self._engine.pet_status_changed.connect(self._on_pet_status)
        self._engine.neuro_status_changed.connect(self._on_neuro_status)

        # Current model tracking
        self._current_model = ""

        # Module status tracking
        self._module_status = {
            "stt": ("unknown", 0.0),
            "tts": ("unknown", 0.0),
            "voice_ws": ("unknown", 0.0),
            "stt_local": ("unknown", 0.0),
        }
        self._stale_timer = QTimer(self)
        self._stale_timer.timeout.connect(self._check_stale_modules)
        self._stale_timer.start(10000)  # every 10s

        self._build_ui()

        # 屏幕右下角
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.right() - self.WIDTH - 16, geo.bottom() - self.HEIGHT - 80)

    def _build_ui(self):
        self.setWindowTitle("🪶 Ikaros 监控面板")
        self.resize(self.WIDTH, self.HEIGHT)
        self.setMinimumSize(400, 300)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        central = QWidget()
        central.setObjectName("root")
        central.setStyleSheet("""
            #root {
                background: #1a1a2e;
                border: 1px solid #0f3460;
                border-radius: 8px;
            }
            QWidget { color: #e0e0e0; font-family: 'Microsoft YaHei','Segoe UI',sans-serif; font-size: 12px; }
        """)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        # ── Title bar ──
        title = QWidget()
        title.setFixedHeight(30)
        title.setStyleSheet("background: #0f3460; border-radius: 7px 7px 0 0;")
        tl = QHBoxLayout(title)
        tl.setContentsMargins(10, 0, 6, 0)

        self._title_lbl = QLabel("🪶 Ikaros 监控面板")
        self._title_lbl.setStyleSheet("font-weight: bold; font-size: 13px; background: transparent;")
        tl.addWidget(self._title_lbl)
        tl.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet("""
            QPushButton { background:#e94560; border:none; border-radius:11px; color:white; font-weight:bold; font-size:11px; }
            QPushButton:hover { background:#ff6b81; }
        """)
        close_btn.clicked.connect(self.close)
        tl.addWidget(close_btn)
        layout.addWidget(title)

        # ── Status bar ──
        sbar = QWidget()
        sbar.setStyleSheet("background: #16213e; padding: 4px 8px;")
        sl = QHBoxLayout(sbar)
        sl.setContentsMargins(8, 4, 8, 4)
        sl.setSpacing(6)

        self._status_dot = QLabel("🟡 检测中...")
        self._status_dot.setStyleSheet("font-size: 12px; background: transparent;")
        sl.addWidget(self._status_dot)

        sep1 = QLabel("│")
        sep1.setStyleSheet("color: #444; background: transparent;")
        sl.addWidget(sep1)

        # ── STT indicator ──
        self._stt_lbl = QLabel("🎤 STT ⚪")
        self._stt_lbl.setToolTip("语音识别模块 (从麦克风捕获音频→WS发送)")
        self._stt_lbl.setStyleSheet("font-size: 11px; color: #666; background: transparent;")
        sl.addWidget(self._stt_lbl)

        sep_stt = QLabel("│")
        sep_stt.setStyleSheet("color: #444; background: transparent;")
        sl.addWidget(sep_stt)

        # ── TTS indicator ──
        self._tts_lbl = QLabel("🔊 TTS ⚪")
        self._tts_lbl.setToolTip("语音合成模块 (edge-tts 本地 TTS → 扬声器)")
        self._tts_lbl.setStyleSheet("font-size: 11px; color: #666; background: transparent;")
        sl.addWidget(self._tts_lbl)

        sep_tts = QLabel("│")
        sep_tts.setStyleSheet("color: #444; background: transparent;")
        sl.addWidget(sep_tts)

        # ── 去桥: WS indicator 改为语音管线状态 ──
        self._ws_lbl = QLabel("🎤 语音 ⚪")
        self._ws_lbl.setToolTip("语音管线 (本地 STT → cloud_chat → 本地 TTS)")
        self._ws_lbl.setStyleSheet("font-size: 11px; color: #666; background: transparent;")
        sl.addWidget(self._ws_lbl)

        sep_ws = QLabel("│")
        sep_ws.setStyleSheet("color: #444; background: transparent;")
        sl.addWidget(sep_ws)

        self._state_lbl = QLabel("💤 待机")
        self._state_lbl.setStyleSheet("font-size: 12px; background: transparent;")
        sl.addWidget(self._state_lbl)

        sep2 = QLabel("│")
        sep2.setStyleSheet("color: #444; background: transparent;")
        sl.addWidget(sep2)

        # ── Model indicator ──
        self._model_lbl = QLabel("🤖 --")
        self._model_lbl.setToolTip("当前对话模型")
        self._model_lbl.setStyleSheet("font-size: 11px; color: #80cbc4; background: transparent;")
        sl.addWidget(self._model_lbl)

        sep3 = QLabel("│")
        sep3.setStyleSheet("color: #444; background: transparent;")
        sl.addWidget(sep3)

        self._restart_lbl = QLabel("重启: 0")
        self._restart_lbl.setStyleSheet("font-size: 12px; color: #888; background: transparent;")
        sl.addWidget(self._restart_lbl)

        sl.addStretch()

        restart_btn = QPushButton("🔄 重启桌宠")
        restart_btn.setStyleSheet("""
            QPushButton { background:#e94560; border:none; border-radius:4px; padding:4px 12px; color:white; font-size:11px; }
            QPushButton:hover { background:#ff6b81; }
        """)
        restart_btn.clicked.connect(self._manual_restart)
        sl.addWidget(restart_btn)

        self._auto_cb = QCheckBox("自动重启")
        self._auto_cb.setChecked(True)
        self._auto_cb.setStyleSheet("""
            QCheckBox { color:#aaa; font-size:11px; spacing:4px; background:transparent; }
            QCheckBox::indicator { width:14px; height:14px; border:1px solid #555; border-radius:3px; background:transparent; }
            QCheckBox::indicator:checked { background:#4caf50; border-color:#4caf50; }
        """)
        self._auto_cb.toggled.connect(self._on_auto_toggle)
        sl.addWidget(self._auto_cb)

        layout.addWidget(sbar)

        # ── Event table ──
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["时间", "", "内容"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.setStretchLastSection(True)
        self._table.setColumnWidth(0, 68)
        self._table.setColumnWidth(1, 34)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("""
            QTableWidget {
                border:none; border-radius:0px; background:#1a1a2e;
                alternate-background-color:#16213e; gridline-color:#0f3460;
                font-size:12px;
            }
            QTableWidget::item { padding:3px 6px; border-bottom:1px solid #0f3460; }
            QHeaderView::section {
                background:#0f3460; color:#666; border:none;
                padding:3px 6px; font-weight:bold; font-size:11px;
            }
            QTableWidget::item:selected { background:#1a3a5e; }
        """)
        layout.addWidget(self._table)

        # ── Drag (title bar) + Resize (bottom-right corner) ──
        self._drag_pos = None
        self._resizing = False
        self._resize_start_pos = None
        self._resize_start_size = None
        title.mousePressEvent = self._on_title_press
        title.mouseMoveEvent = self._on_title_move
        title.mouseReleaseEvent = self._on_title_release

    # ── Drag (title bar) ──
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

    # ── Resize (bottom-right corner 20x20 grip) ──
    _RESIZE_GRIP = 20

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            rect = self.rect()
            grip = QRect(rect.right() - self._RESIZE_GRIP, rect.bottom() - self._RESIZE_GRIP,
                         self._RESIZE_GRIP, self._RESIZE_GRIP)
            if grip.contains(e.pos()):
                self._resizing = True
                self._resize_start_pos = e.globalPosition().toPoint()
                self._resize_start_size = self.size()
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resizing and self._resize_start_pos is not None:
            delta = e.globalPosition().toPoint() - self._resize_start_pos
            new_w = max(self.minimumWidth(), self._resize_start_size.width() + delta.x())
            new_h = max(self.minimumHeight(), self._resize_start_size.height() + delta.y())
            self.resize(new_w, new_h)
            e.accept()
            return
        # Cursor shape hint for resize grip
        rect = self.rect()
        grip = QRect(rect.right() - self._RESIZE_GRIP, rect.bottom() - self._RESIZE_GRIP,
                     self._RESIZE_GRIP, self._RESIZE_GRIP)
        if grip.contains(e.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._resizing = False
        self._resize_start_pos = None
        self._resize_start_size = None
        self.unsetCursor()
        super().mouseReleaseEvent(e)

    # ── Events ──

    _MODULE_LABELS = {
        "stt": "_stt_lbl", "tts": "_tts_lbl", "voice_ws": "_ws_lbl",
    }

    def _update_module_indicator(self, module: str, status: str, ts: float):
        """更新 STT/TTS/WS 状态指示灯."""
        lbl_name = self._MODULE_LABELS.get(module)
        if not lbl_name:
            return
        lbl: QLabel = getattr(self, lbl_name, None)
        if lbl is None:
            return

        now = time.time()
        self._module_status[module] = (status, ts if ts > 0 else now)

        colors = {
            "running": ("#4caf50", "🟢"),
            "connected": ("#4caf50", "🟢"),
            "ready": ("#4caf50", "🟢"),
            "stopped": ("#f44336", "🔴"),
            "disconnected": ("#f44336", "🔴"),
        }
        prefix = {
            "stt": "",
            "tts": "🔊 TTS",
            "voice_ws": "🌐 WS",
            "stt_local": "",
        }
        color, dot = colors.get(status, ("#666", "⚪"))

        if module == "stt" or module == "stt_local":
            # STT 综合状态由 _update_stt_indicator 统一处理
            self._update_stt_indicator()
            return

        lbl.setText(f"{prefix[module]} {dot}")
        lbl.setStyleSheet(f"font-size: 11px; color: {color}; background: transparent;")

    def _update_stt_indicator(self):
        """STT 已在客户端做 (faster-whisper), 指示灯反映本地模型状态."""
        lbl = self._stt_lbl
        local_status, _ = self._module_status.get("stt_local", ("unknown", 0))
        stt_status, _ = self._module_status.get("stt", ("unknown", 0))

        if local_status == "ready":
            # 本地模型已加载 — 主路径
            lbl.setText("🎤 STT 🟢")
            lbl.setToolTip("本地 STT 就绪 (faster-whisper tiny, 离线可用)")
            lbl.setStyleSheet("font-size: 11px; color: #4caf50; background: transparent;")
        elif local_status == "failed":
            # 加载失败
            lbl.setText("🎤 STT 🔴")
            lbl.setToolTip("本地 STT 模型加载失败")
            lbl.setStyleSheet("font-size: 11px; color: #f44336; background: transparent;")
        elif local_status == "unknown" and stt_status == "running":
            # 正在加载
            lbl.setText("🎤 STT 🟡")
            lbl.setToolTip("本地 STT 模型加载中...")
            lbl.setStyleSheet("font-size: 11px; color: #ffb74d; background: transparent;")
        elif stt_status == "stopped":
            lbl.setText("🎤 STT 🔴")
            lbl.setToolTip("STT 未启动")
            lbl.setStyleSheet("font-size: 11px; color: #f44336; background: transparent;")
        else:
            lbl.setText("🎤 STT ⚪")
            lbl.setToolTip("STT 状态未知")
            lbl.setStyleSheet("font-size: 11px; color: #666; background: transparent;")

    def _check_stale_modules(self):
        """检查心跳是否过期 (30s 无心跳 → 灰 ⚪)."""
        now = time.time()
        for module in ("tts",):
            status, last_ts = self._module_status.get(module, ("unknown", 0.0))
            if status not in ("running",):
                continue
            if last_ts > 0 and now - last_ts > 30:
                lbl_name = self._MODULE_LABELS.get(module)
                lbl: QLabel = getattr(self, lbl_name, None)
                if lbl:
                    prefix = "🔊 TTS"
                    lbl.setText(f"{prefix} 🟡")
                    lbl.setStyleSheet("font-size: 11px; color: #ffb74d; background: transparent;")

    def _on_event(self, ev: dict):
        ts = ev.get("ts", time.time())
        etype = ev.get("type", "status")
        text = ev.get("text", ev.get("state", ""))

        # ── Module status events (update indicators, still show in table) ──
        if etype == "module_status":
            module = ev.get("module", "")
            self._update_module_indicator(module, text, ts)

        elif etype == "heartbeat":
            module = ev.get("module", "")
            if module in self._module_status:
                cur_status, _ = self._module_status[module]
                # stt_local: heartbeat 意味着模型已加载 (心跳线程在模型加载成功后才运行)
                if module == "stt_local" and cur_status in ("unknown", "running"):
                    new_status = "ready"
                else:
                    new_status = cur_status if cur_status != "unknown" else "running"
                self._module_status[module] = (new_status, ts)
                self._update_module_indicator(module, new_status, ts)
            if not text:
                return

        elif etype == "voice_activity":
            self._stt_lbl.setStyleSheet("font-size: 11px; color: #4fc3f7; background: transparent;")
            QTimer.singleShot(1200, self._update_stt_indicator)

        elif etype == "model_info":
            # 更新当前模型显示
            self._current_model = text
            # 简化模型名 (取最后一段)
            short = text.split("/")[-1] if "/" in text else text
            self._model_lbl.setText(f"🤖 {short}")
            self._model_lbl.setToolTip(f"当前对话模型: {text}")

        if not text:
            return

        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        icon = _EVENT_ICON.get(etype, "📋")
        color = _EVENT_COLOR.get(etype, "#e0e0e0")

        row = self._table.rowCount()
        self._table.insertRow(row)

        ti = QTableWidgetItem(time_str)
        ti.setForeground(QColor("#888"))
        ti.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, 0, ti)

        ii = QTableWidgetItem(icon)
        ii.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, 1, ii)

        ci = QTableWidgetItem(text[:200])
        ci.setForeground(QColor(color))
        self._table.setItem(row, 2, ci)

        self._table.scrollToBottom()
        while self._table.rowCount() > MAX_TABLE_ROWS:
            self._table.removeRow(0)

    def _on_pet_status(self, alive: bool):
        if alive:
            self._status_dot.setText("🟢 在线")
            self._status_dot.setStyleSheet("font-size: 12px; color: #4caf50; background: transparent;")
        else:
            self._status_dot.setText("🔴 离线")
            self._status_dot.setStyleSheet("font-size: 12px; color: #f44336; background: transparent;")

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
        self._state_lbl.setText(label)
        c = _STATE_COLOR.get(state, "#888")
        self._state_lbl.setStyleSheet(f"font-size: 12px; color: {c}; background: transparent;")
        self._restart_lbl.setText(f"重启: {self._engine._restart_count}")

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
