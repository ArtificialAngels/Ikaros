"""
🪶 Icarus Desktop Pet — Main Application

A transparent, always-on-top desktop pet that listens, speaks,
and responds to what you're doing on your computer.

Architecture:
  pywebview (WebView2) → character.html (SVG + CSS + JS)
  ├── AudioEngine     → pyaudio → WebSocket → bridge Whisper/LLM/TTS
  ├── ContextEngine   → win32gui → context classification
  └── System Tray     → pystray → menu controls
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# ───── Logging ─────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("icarus")

# ───── Paths ─────
HERE = Path(__file__).parent
CHARACTER_HTML = HERE / "character.html"
HERMES_ROOT = HERE.parent.parent  # E:\Hermes Agent

# Ensure modules are importable
sys.path.insert(0, str(HERE))


class IcarusDesktopPet:
    """Main application controller."""

    def __init__(self):
        self._window = None
        self._tray = None
        self._audio = None
        self._context = None
        self._running = True

        # Import submodules
        from audio_engine import AudioEngine
        from context_engine import ContextEngine
        self.AudioEngine = AudioEngine
        self.ContextEngine = ContextEngine

    # ───── Window (pywebview) ─────

    def _create_window(self):
        """Create the transparent, always-on-top pet window."""
        try:
            import webview

            self._window = webview.create_window(
                title="🪶",
                url=str(CHARACTER_HTML),
                width=300,
                height=400,
                frameless=True,
                transparent=True,
                on_top=True,
                resizable=False,
                easy_drag=False,  # We handle drag via JS
            )
            return True
        except Exception as exc:
            logger.error("window: failed to create: %s", exc)
            return False

    # ───── System Tray ─────

    def _create_tray(self):
        """Create system tray icon and menu."""
        try:
            import pystray
            from PIL import Image, ImageDraw

            # Create a simple icon
            icon_size = 64
            img = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            # Draw a small feather icon
            center = icon_size // 2
            draw.ellipse([center - 12, center - 12, center + 12, center + 12],
                         fill=(102, 126, 234, 255))  # Purple circle
            draw.text((center - 5, center - 10), "🪶", font=None, fill=(255, 255, 255, 255))

            def on_show():
                if self._window:
                    try:
                        self._window.show()
                        self._window.restore()
                        self._window.focus()
                    except Exception:
                        pass

            def on_hide():
                if self._window:
                    try:
                        self._window.hide()
                    except Exception:
                        pass

            def on_quit():
                self._running = False
                if self._window:
                    try:
                        self._window.destroy()
                    except Exception:
                        pass
                os._exit(0)

            def on_toggle_mic(icon, item):
                if self._audio and self._audio._running:
                    self._audio.stop()
                    self._send_to_ui("setState", "SLEEPING")
                    self._send_to_ui("showBubble", "🎤 麦克风已关闭")
                else:
                    self._audio.start()
                    self._send_to_ui("setState", "LISTENING")
                    self._send_to_ui("showBubble", "🎤 我听着呢~")

            menu = pystray.Menu(
                pystray.MenuItem("🪶 显示", on_show, default=True),
                pystray.MenuItem("🙈 隐藏", on_hide),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("🎤 切换麦克风", on_toggle_mic),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("❌ 退出", on_quit),
            )

            self._tray = pystray.Icon("icarus", img, "🪶 伊卡洛斯", menu)
            return True
        except Exception as exc:
            logger.error("tray: failed to create: %s", exc)
            return False

    # ───── Audio Engine ─────

    def _start_audio(self):
        async def _audio_ws():
            """Run audio engine's WebSocket client."""
            import asyncio
            import websockets
            import json

            uri = "ws://127.0.0.1:7860/v1/voice/ws"
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"action": "start", "session_id": "icarus_desktop"}))
                self._send_to_ui("showBubble", "🎤 我听着呢~")
                self._send_to_ui("setState", "LISTENING")

                while self._running:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.2)
                        if isinstance(msg, bytes):
                            # TTS audio — would need to play it
                            pass
                        else:
                            data = json.loads(msg)
                            t = data.get("type", "")
                            if t == "transcription":
                                text = data.get("text", "")
                                if text:
                                    self._send_to_ui("showBubble", f"📝 {text}")
                            elif t == "thinking":
                                self._send_to_ui("setState", "THINKING")
                            elif t == "status":
                                self._send_to_ui("showBubble", data.get("message", ""))
                            elif t == "done":
                                reply = data.get("text", "")
                                if reply:
                                    self._send_to_ui("showBubble", reply, 6000)
                                self._send_to_ui("setState", "IDLE")
                                await asyncio.sleep(1.5)
                                self._send_to_ui("setState", "LISTENING")
                                self._send_to_ui("showBubble", "🎤 继续说吧~", 2000)
                    except asyncio.TimeoutError:
                        continue

            self._send_to_ui("setState", "SLEEPING")

        def _run():
            import asyncio
            asyncio.run(_audio_ws())

        self._ws_thread = threading.Thread(target=_run, daemon=True)
        self._ws_thread.start()

    # ───── Context Engine ─────

    def _start_context(self):
        ec = self.ContextEngine()

        def on_context(event):
            tag = event.tag or "Other"
            if tag == "Game":
                self._send_to_ui("showBubble", "👀 哥哥在玩游戏~", 3000)
                self._send_to_ui("setState", "CURIOUS")
            elif tag == "Coding":
                self._send_to_ui("showBubble", "💻 哥哥写代码呢，我不吵", 3000)
                self._send_to_ui("setState", "IDLE")
            elif tag == "Browser":
                self._send_to_ui("showBubble", "🌐 刷什么呢~", 2000)
                self._send_to_ui("setState", "CURIOUS")
            elif tag == "Office":
                self._send_to_ui("setState", "IDLE")

        ec.set_callback(on_context)
        ec.start()
        self._context = ec

    # ───── JS Bridge ─────

    def _send_to_ui(self, method: str, *args):
        """Call a JS function in the pet window."""
        if self._window and self._window.loaded_event.is_set() and not self._window.events.closing:
            try:
                js = f"window.{method}("
                js += ",".join(json.dumps(a) if isinstance(a, (str, dict, list)) else str(a) for a in args)
                js += ")"
                self._window.evaluate_js(js)
            except Exception:
                pass

    # ───── Autostart ─────

    def _register_autostart(self):
        """Register this app and the Hermes services for Windows autostart.

        Both the desktop pet and the Hermes services (bridge, watchdog)
        are added to HKCU Run so everything starts on boot.
        """
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

            # Register desktop pet
            python_exe = sys.executable
            pet_main = str(HERE / "main.py")
            pet_cmd = f'"{python_exe}" "{pet_main}"'

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "IcarusDesktopPet", 0,
                                  winreg.REG_SZ, pet_cmd)

            # Register Hermes services if not already present
            services_cmd = f'"{python_exe}" "{HERMES_ROOT / "bin/hermes-supervisor.py"}"'
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                    winreg.KEY_READ) as key:
                    winreg.QueryValueEx(key, "HermesSupervisor")
            except FileNotFoundError:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                    winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, "HermesSupervisor", 0,
                                      winreg.REG_SZ, services_cmd)

            logger.info("autostart: registered")
            return True
        except Exception as exc:
            logger.warning("autostart: failed: %s", exc)
            return False

    # ───── Main ─────

    def run(self):
        """Start everything."""
        logger.info("🪶 Icarus Desktop Pet starting...")

        # Register autostart
        self._register_autostart()

        # Start context engine (window monitoring)
        self._start_context()

        # Start audio engine (WebSocket to bridge)
        self._start_audio()

        # Create system tray
        if not self._create_tray():
            logger.error("Failed to create system tray")
            return

        # Create window
        if not self._create_window():
            logger.error("Failed to create window")
            return

        # Start tray in a thread
        tray_thread = threading.Thread(target=self._tray.run, daemon=True)
        tray_thread.start()

        # Run window (blocking)
        try:
            import webview
            webview.start(debug=False, http_server=True)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            if self._audio:
                self._audio.stop()
            if self._context:
                self._context.stop()
            logger.info("🪶 Icarus Desktop Pet stopped")


def main():
    pet = IcarusDesktopPet()
    pet.run()


if __name__ == "__main__":
    main()
