# bin/ikaros-sleep.bat — 优雅关停

## 用途
停止 Ikaros 全家桶：Watchdog + VoiceWS(:7870) + Pet + HermesDesktop + llama-server + gopeed + Hermes Studio + Dashboard。

## 两种调用方式
- **独立双击运行**：先 `call init.bat` 加载环境。
- **被 ikaros-start.bat 调用**：环境变量已就绪，跳过 init。

## 关停步骤
`[0]` 冲刷 V5 affect 状态 → `[1]` Watchdog → `[2]` Voice WS(:7870) → `[3]` Desktop Pet → `[4]` Hermes Desktop → `[5]` llama-server 安全清扫 → `[6]` 下载加速器 gopeed(:9999) → `[7]` Hermes Studio(:8647/:8648/:8649) → `[8]` Dashboard(:9119)。

## 设计决策
- **基于端口的 kill**（`netstat ... findstr LISTENING`）只抓当前真正监听的进程。
- **Studio 孤儿清扫（2026-07-16 补强）**：崩溃的服务可能留下不再占端口、却仍持有日志文件句柄的孤儿 `cmd.exe` / `npm` / `node` 进程。端口 kill 后追加 `taskkill /F /IM "node.exe" /T` 全量清扫 Studio 的 node 进程树。安全前提：其他 Ikaros 组件（看门狗 / 语音 / 桌宠 / llama-server）均非 Node 进程；Hermes Desktop 已在 `[4]` 被杀；Dashboard 在 `[8]` 被杀（即便有 Node 进程也被覆盖）。
