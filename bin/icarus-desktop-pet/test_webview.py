"""Minimal pywebview test — creates a small window for 5 seconds then exits."""
import sys, os

# Kill proxy env vars that interfere with WebSocket
for key in list(os.environ.keys()):
    if 'proxy' in key.lower():
        os.environ.pop(key, None)

import webview

window = webview.create_window(
    "🪶",
    html="<h1 style='color:white;font-family:sans-serif;text-align:center;margin-top:60px'>伊卡洛斯 🪶</h1>",
    width=200,
    height=200,
    frameless=True,
    transparent=True,
    on_top=True,
    resizable=False,
)

print(f"Window created: {window}")
webview.start(debug=False)
print("Window closed.")
