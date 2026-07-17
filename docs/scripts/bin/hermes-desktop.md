# bin/hermes-desktop.bat — Hermes Desktop 启动器

## 用途
拉起 Hermes Desktop（Electron 应用）。

## 所需环境变量
- `HERMES_HOME` = `%HERMES_ROOT%\data\hermes-agent`
- `HERMES_DESKTOP_HERMES_ROOT` = `%HERMES_ROOT%\hermes-agent`
- `HERMES_DESKTOP_PYTHON` = `%HERMES_ROOT%\hermes-agent\venv\Scripts\python.exe`
- `PATH` 需含 node + venv

## 说明
- 不用 `setlocal`：Electron 必须能看到这些环境变量。
- 缺失时给出构建提示：`cd hermes-agent && hermes desktop --build-only`。
