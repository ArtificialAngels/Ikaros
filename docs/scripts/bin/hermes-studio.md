# bin/hermes-studio.bat — Hermes Studio 启动器

## 用途
拉起 Hermes Agent 的 Web 仪表盘（hermes-web-ui，Vue 3 + Koa monorepo）。源码在 `hermes-studio/`（从 `exProject/hermes-studio-main` 拷贝）。

## 环境要求
- Node >= 23（使用项目自带的 portable node：`%IKAROS_RUNTIME%\node\node.exe`，即 `runtime/node`，当前 v23.11.1）。
- 必须纯 ASCII；禁 `setlocal`；禁 `timeout`（用 `ping -n`）。

## 关键设置
- 把 portable node（`%IKAROS_RUNTIME%\node`）放到 PATH 最前，确保 studio 用自带的 Node（不依赖系统 C 盘 Node，保证可整体拷贝到其他机器运行）。
- 清空项目 `NODE_PATH`，让 Web UI 只解析自身 `node_modules`。
- `HERMES_WEB_UI_HOME=%IKAROS_DATA%\hermes-studio`，把运行时状态留在 `data/` 树（已 gitignore）。

## 首次启动
若无 `node_modules` 则跑 `npm install`（较重，需网络，数分钟）；否则跳过。

## 模式
- **dev 模式**（默认）：Koa :8647 + Vite :8649（Vite 代理 API 到 :8647）。
- **生产替代**：`npm run build` 后 `node bin/hermes-web-ui.mjs`。
- 等待客户端端口 8649 就绪（最多约 90×2s）。
