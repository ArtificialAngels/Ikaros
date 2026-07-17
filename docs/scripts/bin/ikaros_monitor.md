# bin/ikaros_monitor.py — 本地活动监测采集器

## 用途（原模块 docstring）
移植自 N.E.K.O 的 `main_logic/activity/system_signals.py` 思路，按 Ikaros 架构裁剪：
- 前台窗口 / 进程名：ctypes 调 Win32（GetForegroundWindow → GetWindowThreadProcessId → psutil.Process.name）
- 窗口标题：ctypes GetWindowTextW
- 系统空闲秒数：ctypes GetLastInputInfo
- CPU：psutil.cpu_percent（30s 滚动均值）
- GPU：nvidia-smi 子进程（每 2 tick 采一次，非 N 卡自动关）
- 应用分类 + 状态机：activity_keywords.classify → activity_state
- 截图 + 视觉描述（可选，配置门控）：PIL.ImageGrab + 视觉模型

## 用法
```
from ikaros_monitor import get_monitor
mon = get_monitor()      # 进程单例
mon.start()              # 启动后台轮询（幂等）
snap = mon.snapshot()    # 读最新快照（dict，无阻塞）
```

## 降级与隐私
- 非 Windows / 无 psutil 时优雅降级：snapshot 返回 `os_signals_available=False` 的默认字典。
- 隐私：`category=='private'`（KeePass 等）时，category/canonical 仍记录用于状态机，但调用方（cogno_5d）应只输出中性句，绝不下发进程细节给 LLM。
- 路径自举：`portable-python` 是内嵌发行版（`sys.path[0]=python312.zip`），不会自动加脚本目录到 sys.path，必须显式 `sys.path.insert(0, _BIN_DIR)`（与 voice-ws 一致）。
