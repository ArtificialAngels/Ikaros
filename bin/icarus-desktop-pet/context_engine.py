"""
Icarus Context Engine — Windows context awareness.

Monitors the active window (foreground window) via win32gui,
classifies what the user is doing (gaming/working/browsing/etc.),
and emits context events to the main app for pet behavior.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Callable, Optional

import win32gui
import win32process
import psutil

logger = logging.getLogger("icarus.context")

POLL_INTERVAL = 1.0  # seconds between window checks

# ───── Static registry: process name → context tag ─────
APP_REGISTRY = {
    # Games (detected by keyword patterns in window title)
    # Coding
    "code.exe": "Coding",
    "cursor.exe": "Coding",
    "pycharm64.exe": "Coding",
    "idea64.exe": "Coding",
    "clion64.exe": "Coding",
    "webstorm64.exe": "Coding",
    "vscode.exe": "Coding",
    "notepad++.exe": "Coding",
    "sublime_text.exe": "Coding",
    "atom.exe": "Coding",
    "goland64.exe": "Coding",
    "rider64.exe": "Coding",
    "eclipse.exe": "Coding",
    "androidstudio.exe": "Coding",
    # Browsers
    "chrome.exe": "Browser",
    "msedge.exe": "Browser",
    "firefox.exe": "Browser",
    "brave.exe": "Browser",
    "opera.exe": "Browser",
    # Office
    "excel.exe": "Office",
    "winword.exe": "Office",
    "powerpnt.exe": "Office",
    "outlook.exe": "Office",
    "onenote.exe": "Office",
    "wps.exe": "Office",
    "et.exe": "Office",
    "wpp.exe": "Office",
    # Social / Communication
    "wechat.exe": "Social",
    "qq.exe": "Social",
    "tim.exe": "Social",
    "dingtalk.exe": "Social",
    "feishu.exe": "Social",
    "lark.exe": "Social",
    "telegram.exe": "Social",
    "discord.exe": "Social",
    "slack.exe": "Social",
    # Media
    "spotify.exe": "Media",
    "netease.exe": "Media",
    "qqmusic.exe": "Media",
    "kugou.exe": "Media",
    "vlc.exe": "Media",
    "potplayer.exe": "Media",
    # Design
    "photoshop.exe": "Design",
    "illustrator.exe": "Design",
    "premiere.exe": "Design",
    "afterfx.exe": "Design",
    "blender.exe": "Design",
    "figma.exe": "Design",
    # Terminal / System
    "cmd.exe": "System",
    "powershell.exe": "System",
    "windowsTerminal.exe": "System",
    "explorer.exe": "System",
    "taskmgr.exe": "System",
    # AI tools
    "hermes-web-ui.exe": "AI",
    "hermes.exe": "AI",
}

GAME_KEYWORDS = [
    r"(?i)\bgame\b", r"(?i)\bplay\b", r"(?i)steam",
    r"(?i)\bdiablo\b", r"(?i)\bworld of warcraft\b", r"(?i)\bleague of legends\b",
    r"(?i)\bdota\b", r"(?i)\boverwatch\b", r"(?i)\bvalorant\b",
    r"(?i)\bcyberpunk\b", r"(?i)\bfinal fantasy\b", r"(?i)\bmonster hunter\b",
    r"(?i)\bapex\b", r"(?i)\bfortnite\b", r"(?i)\bminecraft\b",
    r"(?i)\bgenshin\b", r"(?i)原神", r"(?i)崩坏", r"(?i)星穹",
    r"(?i)绝区零", r"(?i)永劫无间", r"(?i)英雄联盟",
    r"(?i)魔兽", r"(?i)梦幻", r"(?i)大话",
]

LEARNING_KEYWORDS = [
    r"(?i)\bcourse\b", r"(?i)\btutorial\b", r"(?i)\blecture\b",
    r"(?i)\bmooc\b", r"(?i)\bcoursera\b", r"(?i)\budemy\b",
    r"(?i)\bstudy\b", r"(?i)\blearn\b", r"(?i)教程",
]


class ContextEvent:
    """Emitted when the user's context changes."""

    def __init__(self, process: str, title: str, tag: str, desc: str):
        self.process = process
        self.title = title
        self.tag = tag  # Game, Coding, Browser, Office, etc.
        self.desc = desc
        self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "process": self.process,
            "title": self.title[:80],
            "desc": self.desc,
            "timestamp": self.timestamp,
        }


class ContextEngine:
    """Monitors active window and classifies user context."""

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._on_context: Optional[Callable[[ContextEvent], None]] = None
        self._last_tag: Optional[str] = None
        self._process_cache: dict[int, str] = {}

    def set_callback(self, cb: Callable[[ContextEvent], None]):
        self._on_context = cb

    def _get_process_name(self, pid: int) -> str:
        """Get process name from PID (cached)."""
        if pid in self._process_cache:
            return self._process_cache[pid]
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            self._process_cache[pid] = name
            return name
        except Exception:
            return "unknown.exe"

    def _get_window_title(self, hwnd: int) -> str:
        """Get window title."""
        try:
            return win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return ""

    def _classify(self, process: str, title: str) -> tuple[str, str]:
        """Classify window into a context tag + description."""
        pname = process.lower()

        # Check registry by process name
        tag = APP_REGISTRY.get(pname)
        if tag:
            return tag, tag

        # Check game keywords in window title
        for kw in GAME_KEYWORDS:
            if re.search(kw, title):
                return "Game", title[:60]

        # Check learning keywords
        for kw in LEARNING_KEYWORDS:
            if re.search(kw, title):
                return "Learning", title[:60]

        # Default
        return "Other", process

    def _poll(self):
        """Poll loop — check foreground window every second."""
        while self._running:
            try:
                hwnd = win32gui.GetForegroundWindow()
                if not hwnd:
                    time.sleep(POLL_INTERVAL)
                    continue

                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc = self._get_process_name(pid)
                title = self._get_window_title(hwnd)
                tag, desc = self._classify(proc, title)

                if tag != self._last_tag:
                    self._last_tag = tag
                    event = ContextEvent(proc, title, tag, desc)
                    logger.info("context: %s → %s (%s)", tag, desc, proc)
                    if self._on_context:
                        self._on_context(event)

            except Exception:
                pass

            time.sleep(POLL_INTERVAL)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        logger.info("context engine: started")

    def stop(self):
        self._running = False
