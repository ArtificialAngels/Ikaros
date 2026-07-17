# bin/ikaros-start.bat — 全栈启动编排

## 用途
Ikaros 全家桶一键启动器。按固定顺序拉起各组件，并在末尾打印状态横幅、给出停止方式。

## 启动顺序（Steps）
token → env → verify → sleep → memory → voice → think → pet → studio（dashboard / desktop 为手动备份，不自动拉起）。

## 运行期横幅（示例）
```
  Ikaros
  Pet:       Ikaros Desktop Pet v2  (Tauri v2, Live2D)
  Studio:    http://127.0.0.1:8649   (auto - default Web UI + Agent)
  Dashboard: http://127.0.0.1:9119   (manual: bin\hermes-dashboard.bat)
  Voice WS:  ws://127.0.0.1:7870/v1/voice/ws
  Memory:    :8587 embedding + :8080 local LLM
  Think:     V5.1 metacog cycle
  Logs:      %IKAROS_LOGS%\
  Stop:      bin\ikaros-sleep.bat
```

## 设计决策 / 踩坑记录
- **Hermes Dashboard 故意不自动启动**：与 Studio 的 bridge 同时运行会 spawn 第二个 Hermes Agent，写同一份 `data/hermes-agent`（会话分裂 + db 争用）。按需手动 `bin\hermes-dashboard.bat`；避免 Studio 与 Dashboard 同时跑。
- **Hermes Studio 用 dev 模式**：`npm run dev` → 并发起 `dev:server`（Koa :8647）+ `dev:client`（Vite :8649），开 http://127.0.0.1:8649。这是最早可用的变体（按需求回滚到此）。`NODE_PATH` 清空以避免 managed runtime 污染。
- **Studio 环境变量修复（2026-07-15）**：必须在父进程用正确的 `set "VAR=val"` 语法设置 Studio 环境变量，让 detached 子进程继承。原先内联 `set ""VAR=val""` 引号畸形，从未真正设置 `HERMES_WEB_UI_HOME` / `NODE_PATH` / `PATH`（经验证会生成名为 `"HERMES_WEB_UI_HOME` 的伪变量）。用 save/restore 避免污染后续启动步骤。
- **清旧日志时机**：必须在 `wscript` 启动新进程**之前** `del` 旧 log，否则新子进程占着文件句柄会导致删除失败。

## 硬约束（保留在脚本内）
- 必须纯 ASCII。
- 禁 `setlocal`（会破坏父级 cmd 栈 / 向调用方传递变量）。
- 禁 `timeout`（用 `ping -n` 替代）。
- sleep.bat 内部不要再 `call init.bat`。
