# bin/Hermes-dashboard.bat — Hermes Dashboard 启动器

## 用途
启动 Web 仪表盘（若未运行）并打开浏览器 http://127.0.0.1:9119/。

## Bugfix（2026-07-13）
`HERMES_SERVE_HEADLESS` 可能从上一次 Desktop spawn 泄漏，导致 `mount_spa()` 在 import 时就跳过整个 SPA。本脚本显式 `set "HERMES_SERVE_HEADLESS="` 与 `set "HERMES_WEB_DIST="` 清除。

## 流程
1. 清泄漏环境变量。
2. 端口 :9119 快速探测：已在运行则直接开浏览器退出。
3. 否则 `hermes.exe dashboard --port 9119 --no-open --skip-build` 启动。
4. 轮询就绪（最多约 30×2s）后开浏览器。
