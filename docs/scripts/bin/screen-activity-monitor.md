# screen-activity-monitor.ps1 — 屏幕活动监控

> 源文件：`bin/screen-activity-monitor.ps1`
> 作用：追踪当前活动窗口变化，纯 Windows 内置 API，无第三方依赖。

## 说明（PowerShell Comment-Based Help 原文）

- **SYNOPSIS**：屏幕活动监控 — 追踪当前活动窗口变化，使用纯 Windows 内置 API。
- **DESCRIPTION**：通过 `user32.dll` 的 `GetForegroundWindow` 轮询，记录活跃窗口标题、
  进程名、窗口类。仅依赖 .NET Framework / PowerShell，无需第三方软件。支持后台守护模式 + 历史报表。
- **PARAMETER Command**：
  - `start` 启动后台监控（PID 保存到 pid 文件）
  - `stop` 停止后台监控
  - `status` 显示监控是否在运行
  - `report` 打印今日活动摘要
  - `log` 打印原始日志（最近 200 条）
  - `clear` 清空日志
- **PARAMETER Interval**：轮询间隔（秒），默认 2。
- **PARAMETER LogPath**：日志文件路径，默认 `%USERPROFILE%\.screen-activity-monitor\log.csv`。
- **EXAMPLE**：
  - `.\screen-activity-monitor.ps1 start -Interval 1`
  - `.\screen-activity-monitor.ps1 report`

## 实现要点

- 通过 `Add-Type` 一次性加载 `ScreenActivityMonitor.Native`（C# P/Invoke 封装 `user32.dll`）。
- 后台守护：`-WindowStyle Hidden` 启动子进程 `_daemon`，写 PID 文件 + 互斥锁文件防止重复运行。
- 只在「窗口切换或标题变化」时才记 CSV 一行（降噪）。
- 日志 `%USERPROFILE%\.screen-activity-monitor\log.csv`，事件 `%USERPROFILE%\.screen-activity-monitor\events.log`。
