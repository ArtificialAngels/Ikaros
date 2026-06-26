# 🧠 Neuro Tray — 独立 Neuro 通知栏

## 用途
独立的 Windows system tray 工具, 显示 Neuro AI 状态。
**不嵌入 webui, 不依赖 webui**。在用户登录 session 自动启动。

## 双击运行
```
E:\Hermes Agent\bin\neuro-tray\start_neuro_tray.bat
```

## 托盘图标 (右下角通知栏)
- 圆形 ɑ 字符 + 颜色背景
- 颜色 = AI 状态:
  - 🔵 蓝 idle
  - 🟣 紫 listening
  - 🟡 黄 thinking
  - 🟢 绿 speaking
  - 🔴 红 bored (PATIENCE 接近)
  - ⚫ 灰 offline (Neuro bridge 不可达)

## 交互
| 操作 | 效果 |
|---|---|
| **双击图标** | 触发 PATIENCE (让伊卡洛斯主动说话) |
| **右键** | 弹出菜单 |
| **hover** | tooltip 显示 `Neuro · STATE · PATIENCE X/Y s · 记忆 N 条` |

## 右键菜单
- ⚪ **状态行** (disabled, 只读)
- 💬 **让伊卡洛斯主动说话** — 触发 PATIENCE
- ⏱️ **PATIENCE 阈值** — 15s / 30s / 60s / 120s 单选
- 🔄 **重置说话标志** — 卡死恢复
- 🧠 **看记忆…** — 弹窗显示 Chroma 长期记忆 (20 条)
- 📝 **加一条记忆…** — 输入框 + 提交
- 🔗 **查看 Neuro 状态…** — 完整 JSON dump
- ❌ **退出**

## PATIENCE 警报
当 PATIENCE 进度 > 85% 时, 自动弹 Windows 气泡通知:
> "伊卡洛斯想说话了"
> "已沉默 X 秒, 哥哥要不要让她说点什么?"
> 4 秒自动消失

每 60 秒最多弹一次 (防骚扰)。

## 自启动
已注册到 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\IcarusNeuroTray`。
用户登录时自动启动。

## 故障排查
| 现象 | 原因 | 修 |
|---|---|---|
| 图标一直是灰色 | Neuro bridge (:7860) 没跑 | `python bin/hermes-supervisor.py --start only bridge` |
| 图标不出现 | Win11 隐藏通知图标 | 设置 → 个性化 → 任务栏 → 其他系统托盘图标 → 打开 |
| 双击没反应 | Neuro 不可达 | 看 tooltip 是不是 "离线" |
| 菜单点不开 | tray 被卡 | 任务管理器结束 python.exe 重启 |

## 与桌宠关系
- 桌宠 (`bin/icarus-desktop-pet`) = PyQt6 透明窗口 + 角色
- Neuro tray (`bin/neuro-tray`) = system tray icon
- 两者独立, 都连 Neuro bridge (:7860)
- 可以单独跑任意一个