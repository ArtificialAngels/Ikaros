"""
Icarus Live2D Test - PyQt6 + QWebEngineView
==========================================
测试 Live2D 渲染伊卡洛斯角色（用 xisitina 模型替代真伊卡洛斯 Live2D）
"""
import sys
import os
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QWidget
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile
from PyQt6.QtCore import QUrl, Qt, QTimer
from PyQt6.QtGui import QPainter, QColor, QPen

LIVE2D_DIR = Path(__file__).parent / "live2d"
LIVE2D_HTML = LIVE2D_DIR / "index.html"


class Live2DWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("伊卡洛斯 Live2D Test")
        self.resize(400, 500)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )

        # 中心 webview
        self.view = QWebEngineView()

        # 允许 file:// 访问本地资源（CORS bypass）
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)

        url = QUrl.fromLocalFile(str(LIVE2D_HTML))
        self.view.setUrl(url)

        # 加载完成回调
        self.view.loadFinished.connect(self._on_load_finished)

        self.setCentralWidget(self.view)
        self._load_ok = False
        self._title_overlay = QLabel(self)
        self._title_overlay.setText("loading live2d...")
        self._title_overlay.setStyleSheet("color: white; background: rgba(0,0,0,128); padding: 4px;")
        self._title_overlay.adjustSize()
        self._title_overlay.move(10, 10)

    def _on_load_finished(self, ok: bool):
        if ok:
            self._load_ok = True
            self._title_overlay.setText("✓ live2d ok")
            print("[ok] live2d page loaded")
            # Check JS status after a short delay
            QTimer.singleShot(3000, self._check_status)
        else:
            self._title_overlay.setText("✗ live2d failed")
            print("[fail] live2d page failed to load")

    def _check_status(self):
        """Check the Live2D status from JS."""
        self.view.page().runJavaScript(
            "document.title + ' | ' + document.getElementById('status').textContent",
            lambda result: print(f"[live2d status] {result}")
        )
        self.view.page().runJavaScript(
            "window.getLive2D ? JSON.stringify(window.getLive2D()) : 'no getLive2D'",
            lambda result: print(f"[live2d model] {result}")
        )

    def paintEvent(self, event):
        # 深色背景兜底，Live2D 透明 canvas 之外不会黑
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(13, 18, 35))


def main():
    app = QApplication(sys.argv)
    win = Live2DWindow()
    win.show()
    print(f"Window shown: {win.isVisible()}")
    print(f"WebEngine URL: {LIVE2D_HTML}")
    print(f"WebEngine exists: {LIVE2D_HTML.exists()}")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
