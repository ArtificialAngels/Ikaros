"""ikaros_monitor.py — Ikaros 本地活动监测采集器

移植自 N.E.K.O 的 main_logic/activity/system_signals.py 思路，按 Ikaros
架构裁剪：

* 前台窗口 / 进程名：ctypes 调 Win32
  (GetForegroundWindow -> GetWindowThreadProcessId -> psutil.Process.name)
* 窗口标题：ctypes GetWindowTextW
* 系统空闲秒数：ctypes GetLastInputInfo
* CPU：psutil.cpu_percent（30s 滚动均值）
* GPU：nvidia-smi 子进程（每 2 tick 采一次，非 N 卡自动关）
* 应用分类 + 状态机：activity_keywords.classify -> activity_state
* 截图 + 视觉描述（可选，配置门控）：PIL.ImageGrab + 视觉模型

用法：
    from ikaros_monitor import get_monitor
    mon = get_monitor()      # 进程单例
    mon.start()              # 启动后台轮询（幂等）
    snap = mon.snapshot()    # 读最新快照（dict，无阻塞）

非 Windows / 无 psutil 时优雅降级：snapshot 返回 os_signals_available=False
的默认字典，调用方据此跳过本地信号即可。

隐私：category=='private'（KeePass 等）时，category/canonical 仍记录用于
状态机，但调用方（cogno_5d）应只输出中性句，绝不下发进程细节给 LLM。
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import threading
import time
from collections import deque

logger = logging.getLogger("ikaros.monitor")

_IS_WINDOWS = platform.system() == "Windows"
_POLL_INTERVAL = 5.0          # 轮询间隔（秒）
_CPU_WINDOW = 6              # 6 x 5s = 30s 滚动均值
_GPU_EVERY_N = 2            # 每 2 tick（10s）采一次 GPU
_AWAY_IDLE_SECONDS = 180     # 空闲超过此值视为 away（离开键盘）
_FOCUSED_WORK_MIN_IDLE = 0   # 工作态要求的前沿空闲下限（简化：>=0 即工作）


# ── Win32 常量 / 结构体 ──
class _LASTINPUTINFO:
    _fields_ = [("cbSize", "I"), ("dwTime", "I")]


def _build_user32():
    if not _IS_WINDOWS:
        return None
    try:
        import ctypes
        u = ctypes.windll.user32
        # 设置参数/返回类型，避免 64 位截断
        u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        u.GetWindowThreadProcessId.restype = ctypes.c_uint32
        u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetForegroundWindow.restype = ctypes.c_void_p
        u.GetLastInputInfo.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
        u.GetLastInputInfo.restype = ctypes.c_int
        return u
    except Exception as e:  # pragma: no cover
        logger.warning("user32 初始化失败: %s", e)
        return None


def _build_psutil():
    try:
        import psutil
        return psutil
    except Exception:
        logger.warning("psutil 不可用，CPU/进程信号关闭")
        return None


class SystemMonitor:
    """进程单例。start() 启动后台线程轮询；snapshot() 无阻塞读最新。"""

    def __init__(self, poll_interval: float = _POLL_INTERVAL) -> None:
        self._poll_interval = poll_interval
        self._user32 = _build_user32()
        self._psutil = _build_psutil()
        self._os_signals_available = bool(_IS_WINDOWS and self._user32 and self._psutil)
        self._cpu_samples: deque[float] = deque(maxlen=_CPU_WINDOW)
        self._gpu_tick = 0
        self._gpu_last: float | None = None
        self._gpu_available = True
        self._lock = threading.Lock()
        self._latest: dict = self._default_snapshot()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ── 公共 ──

    def start(self) -> None:
        """启动后台轮询线程（幂等）。"""
        if self._thread and self._thread.is_alive():
            return
        if not self._os_signals_available:
            logger.info("SystemMonitor: 本地信号不可用，跳过轮询（降级模式）")
            return
        # 预热 psutil 计数器
        try:
            if self._psutil:
                self._psutil.cpu_percent(interval=None)
        except Exception:
            pass
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ikaros-monitor", daemon=True)
        self._thread.start()
        logger.info("SystemMonitor 已启动 (interval=%.1fs, signals=%s)",
                    self._poll_interval, self._os_signals_available)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread = None

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._latest)

    @property
    def available(self) -> bool:
        return self._os_signals_available

    # ── 轮询 ──

    def _run(self) -> None:
        try:
            self._poll()  # 首拍立即出真实数据
        except Exception as e:
            logger.debug("monitor 首拍失败: %s", e)
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as e:
                logger.warning("monitor 轮询失败: %s", e)
            self._stop.wait(self._poll_interval)

    def _poll(self) -> None:
        idle = self._read_idle()
        cpu = self._read_cpu()
        title, proc = self._read_window()
        # GPU（每 N tick）
        self._gpu_tick += 1
        if self._gpu_available and self._gpu_tick % _GPU_EVERY_N == 0:
            g = self._read_gpu()
            if g is not None:
                self._gpu_last = g
            elif self._gpu_tick == _GPU_EVERY_N:
                self._gpu_available = False
        if cpu is not None:
            self._cpu_samples.append(cpu)
        cpu_avg = (sum(self._cpu_samples) / len(self._cpu_samples)) if self._cpu_samples else 0.0

        cls = self._classify(proc, title)
        state = self._derive_state(cls["category"], idle)

        snap = {
            "timestamp": time.time(),
            "process_name": proc,
            "window_title": title,
            "idle_seconds": idle if idle is not None else 0.0,
            "cpu_instant": cpu if cpu is not None else 0.0,
            "cpu_avg_30s": round(cpu_avg, 1),
            "gpu_utilization": self._gpu_last,
            "category": cls["category"],
            "subcategory": cls["subcategory"],
            "canonical": cls["canonical"],
            "is_browser": cls["is_browser"],
            "activity_state": state,
            "os_signals_available": self._os_signals_available,
            "screen_desc": self._latest.get("screen_desc"),  # 截图描述单独周期刷新
        }
        with self._lock:
            self._latest = snap

    # ── Win32 / psutil 读取 ──

    def _read_idle(self) -> float | None:
        if not self._user32:
            return None
        try:
            import ctypes
            info = _LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
            if not self._user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            now = self._user32.GetTickCount()
            elapsed = (now - info.dwTime) & 0xFFFFFFFF
            return elapsed / 1000.0
        except Exception:
            return None

    def _read_cpu(self) -> float | None:
        if not self._psutil:
            return None
        try:
            return float(self._psutil.cpu_percent(interval=None))
        except Exception:
            return None

    def _read_window(self) -> tuple[str | None, str | None]:
        if not self._user32:
            return (None, None)
        try:
            import ctypes
            hwnd = self._user32.GetForegroundWindow()
            if not hwnd:
                return (None, None)
            # 进程名
            pid = ctypes.c_uint32(0)
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            proc = None
            if pid.value and self._psutil:
                try:
                    proc = self._psutil.Process(pid.value).name()
                except Exception:
                    proc = None
            # 标题
            length = self._user32.GetWindowTextLengthW(hwnd)
            title = None
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                self._user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value or None
            return (title, proc)
        except Exception:
            return (None, None)

    def _read_gpu(self) -> float | None:
        import subprocess
        try:
            kwargs = {"capture_output": True, "text": True, "timeout": 3.0}
            if _IS_WINDOWS:
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"], **kwargs,
            )
            if r.returncode != 0:
                return None
            line = (r.stdout or "").strip().splitlines()[0:1]
            if not line:
                return None
            return float(line[0].strip())
        except Exception:
            return None

    # ── 分类 / 状态机 ──

    def _classify(self, proc, title) -> dict:
        try:
            from activity_keywords import classify as _classify
            return _classify(proc, title, url=None)
        except Exception:
            return {"category": "unknown", "subcategory": None,
                    "canonical": None, "is_browser": False}

    def _derive_state(self, category: str, idle: float | None) -> str:
        idle = idle or 0.0
        if category == "private":
            return "private"
        if category == "own_app":
            return "idle"  # 桌宠自身前台，不算用户活动
        if idle >= _AWAY_IDLE_SECONDS:
            return "away"
        if category == "gaming":
            return "gaming"
        if category == "work":
            return "focused_work"
        if category == "communication":
            return "chatting"
        if category in ("entertainment", "browser"):
            return "casual_browsing"
        return "idle"

    # ── 截图 + 视觉描述（Layer 3，配置门控）──

    def refresh_screen_desc(self) -> str | None:
        """截一张屏并（若配置了视觉模型）生成描述。无视觉模型则返回 None。

        纯可选：不配置 IKAROS_VISION_* 时直接返回 None，不影响监测主链路。
        """
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
        except Exception as e:
            logger.debug("截图失败: %s", e)
            return None
        desc = self._describe_image(img)
        with self._lock:
            self._latest = {**self._latest, "screen_desc": desc}
        return desc

    def _describe_image(self, img) -> str | None:
        model = os.getenv("IKAROS_VISION_MODEL")
        base = os.getenv("IKAROS_VISION_BASE_URL")
        key = os.getenv("IKAROS_VISION_API_KEY")
        if not (model and base and key):
            return None  # 未配置视觉模型 → 跳过
        try:
            import base64, io, json, urllib.request
            buf = io.BytesIO()
            img.convert("RGB").resize((min(img.width, 1280), int(img.height * min(img.width, 1280) / img.width))) \
                .save(buf, format="JPEG", quality=80)
            b64 = base64.b64encode(buf.getvalue()).decode()
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "用一句话中文描述这张屏幕在显示什么（不要描述桌宠自身）。"},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ]},
                ],
                "max_tokens": 80,
            }
            req = urllib.request.Request(
                base.rstrip("/") + "/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.debug("视觉描述失败: %s", e)
            return None

    # ── 默认快照 ──

    def _default_snapshot(self) -> dict:
        return {
            "timestamp": 0.0,
            "process_name": None,
            "window_title": None,
            "idle_seconds": 0.0,
            "cpu_instant": 0.0,
            "cpu_avg_30s": 0.0,
            "gpu_utilization": None,
            "category": "unknown",
            "subcategory": None,
            "canonical": None,
            "is_browser": False,
            "activity_state": "idle",
            "os_signals_available": self._os_signals_available,
            "screen_desc": None,
        }


# ── 单例 ──

_singleton: SystemMonitor | None = None


def get_monitor() -> SystemMonitor:
    global _singleton
    if _singleton is None:
        _singleton = SystemMonitor()
    return _singleton


# 活动状态 -> 自然语言（供 cogno_5d 调用）
_ACTIVITY_PHRASE = {
    "gaming": "在玩游戏",
    "focused_work": "在专注工作或写代码",
    "casual_browsing": "在浏览网页或看视频",
    "chatting": "在聊天或处理消息",
    "idle": "在发呆或没在操作",
    "away": "暂时离开了（一段时间没操作）",
    "private": "在使用一个隐私应用",
    "own_app": "在看着伊卡洛斯",
}


def activity_phrase(snap: dict | None) -> str:
    """把快照转成一句话自然语言。隐私状态只给中性句。"""
    if not snap:
        return ""
    state = snap.get("activity_state", "idle")
    canon = snap.get("canonical")
    if state == "private":
        return "哥哥在使用一个隐私应用"
    phrase = _ACTIVITY_PHRASE.get(state, "在桌前")
    if canon and state not in ("idle", "away", "private"):
        return f"哥哥{phrase}（{canon}）"
    return f"哥哥{phrase}"


if __name__ == "__main__":
    m = get_monitor()
    m.start()
    time.sleep(6)
    for _ in range(3):
        s = m.snapshot()
        print(f"state={s['activity_state']:14s} app={str(s['canonical']):12s} "
              f"proc={str(s['process_name']):22s} idle={s['idle_seconds']:.1f}s "
              f"cpu={s['cpu_avg_30s']:.1f}% title={(s['window_title'] or '')[:24]}")
        time.sleep(5)
