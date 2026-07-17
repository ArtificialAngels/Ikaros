# bin/ikaros-live2d.bat — 桌面宠物启动器（Tauri v2 / Live2D）

## 用途
管理 Ikaros 桌面宠物（Tauri v2 + Live2D）的 start / stop / status。

## 用法
```
bin\ikaros-live2d.bat [start|stop|status]
```

## 行为
- `start`：先确保 Voice WS(:7870) 已起（否则拉起），再启动 pet exe；若已运行则跳过。缺失 exe 时提示 `cd Ikaros-Live2D && npx tauri build`。
- `stop`：杀 `ikaros-desktop-pet.exe`。
- `status`：报告是否运行。
- `ensure_voice`：pet 需要语音，自动补起 Voice WS。
