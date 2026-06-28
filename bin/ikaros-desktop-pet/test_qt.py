"""Test: PyQt6 transparent window with QSvgWidget."""
import sys, os, time

for k in list(os.environ.keys()):
    if 'proxy' in k.lower(): os.environ.pop(k, None)

from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtSvgWidgets import QSvgWidget

app = QApplication(sys.argv)

w = QMainWindow()
w.setWindowTitle("🪶 Test")
w.setFixedSize(200, 280)
w.setWindowFlags(
    Qt.WindowType.FramelessWindowHint |
    Qt.WindowType.WindowStaysOnTopHint |
    Qt.WindowType.Tool
)
w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

c = QWidget()
c.setStyleSheet("background: transparent;")
w.setCentralWidget(c)
layout = QVBoxLayout(c)
layout.setContentsMargins(0, 0, 0, 0)

svg = QSvgWidget("bin/ikaros-desktop-pet/character.svg")
svg.setFixedSize(180, 260)
svg.setStyleSheet("background: transparent;")
layout.addWidget(svg, 0, Qt.AlignmentFlag.AlignCenter)
layout.addStretch()

# Position: center of screen
screen = app.primaryScreen()
if screen:
    geo = screen.availableGeometry()
    x = (geo.width() - 200) // 2
    y = (geo.height() - 280) // 2
    w.move(x, y)

w.show()
print(f"visible: {w.isVisible()}, pos: {w.pos().x()},{w.pos().y()}")

# Auto-close after 8 seconds
QTimer.singleShot(8000, app.quit)
sys.exit(app.exec())
