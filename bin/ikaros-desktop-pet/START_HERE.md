# 🪶 Ikaros Desktop Pet — 启动指南

## ⚠️ 重要：桌宠必须在用户桌面 session 启动

桌宠需要 `Qt.exec()` 跑 message pump + 显示 WebEngine 窗口。
**不能** 通过 `subprocess.DETACHED_PROCESS` / `nohup` / 后台跑 — 那些会失去 desktop session，导致窗口崩溃（子进程 msedgewebview2 还活着但 parent 死）。

## ✅ 正确启动方式

### 方式 1：双击（推荐给哥哥）
```
E:\Hermes Agent\bin\ikaros-desktop-pet\start.bat
```

### 方式 2：Python 直接跑（看 log）
```bash
"E:\Hermes Agent\portable-python\python.exe" "E:\Hermes Agent\bin\ikaros-desktop-pet\main.py"
```

### 方式 3：HKCU Run 自动启动（开机自启）
桌宠已注册到 `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run\IkarosDesktopPet`。
每次用户登录时自动启动。**前提是 explorer.exe 在 user session 跑**（默认行为）。

## 🧠 Neuro 集成

桌宠启动后会：
1. 连 audio engine（pyaudio）
2. 创建 system tray（Neuro 菜单）
3. 启动 context engine（检测游戏/编码/浏览器）
4. **启动 NeuroClient（1Hz 轮询 /v1/neuro/status）**
5. 显示 Live2D 窗口

### 🧠 Neuro 托盘菜单
- 💬 **让伊卡洛斯主动说话** — 触发 PATIENCE
- ⏱️ **PATIENCE 阈值** — 15s / 30s / 60s / 120s
- 🔄 **重置说话标志** — 卡死恢复
- 🧠 **看记忆** — 弹窗显示 Chroma 长期记忆
- 📝 **加一条记忆** — 手动注入

### Character state 反映 Neuro AI 状态
桌宠伊卡洛斯会根据 Neuro 状态切换表情：
- `idle` / `listening` / `thinking` / `speaking` / `bored`（PATIENCE 接近）

## 🛑 停止

### 方式 1：托盘菜单 ❌ 退出
### 方式 2：双击 stop.bat
### 方式 3：PowerShell 杀进程
```powershell
Get-Process python | Where-Object { $_.MainWindowTitle -like '*🪶*' } | Stop-Process
```

## 📋 日志

`E:\Hermes Agent\data\logs\ikaros-pet.log` — 所有 Python logging 输出。

## 🔧 故障排查

| 现象 | 原因 | 修 |
|---|---|---|
| 双击 start.bat 没窗口 | 用户 session 没 explorer.exe | 用 explorer.exe / 重新登录 |
| 桌宠窗口闪现然后消失 | WebEngine 子进程崩了 | 看 ikaros-pet.log 里 NeuroClient 启动后是否抛错 |
| 桌宠在但 Neuro 菜单点不动 | Neuro bridge (:7860) 没跑 | `python bin/hermes-supervisor.py --start only bridge` |
| 桌宠完全无响应 | pyaudio 卡 | 重启桌宠 |