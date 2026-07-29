# bin/ika-ws-restart.bat — Voice WS 热重启

## 用途
在不整体重启 Ikaros 的前提下，重启 Voice WS(:7870)。

## 流程
1. 按端口 `:7870` LISTENING 杀旧进程。
2. `ping -n 2` 稍等。
3. `start` 拉起 `ikaros-voice-ws.py`（隐藏窗口，日志到 `%IKAROS_LOGS%\voice-ws.log`）。
4. 轮询端口就绪（最多约 20×2s）。
