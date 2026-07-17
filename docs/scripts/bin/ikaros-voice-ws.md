# bin/ikaros-voice-ws.py — Live2D 语音服务（:7870/v1/voice/ws）

## 用途（原模块 docstring）
2026-07-05（哥哥下指令）：bridge :7860 已删（commit b16c8f8），:8080 给 Hermes Agent LLM + 记忆 extract 占用。Live2D voice service 走新端口 :7870 接 cogno_5D + cloud_chat 真物——不重复发明。

## 消息协议（从 App.vue:onmessage 抽）
- client → server：`start`(进 session) / `transcript`(STT 真物) / `text`(纯文本) / `look`(让 pet 看屏幕 Layer3，配置门控)
- server → client：`status` / `transcription`(复述) / `thinking` / `done`(LLM reply) / `activity`(前台活动变化推送) / `screen`(视觉描述) / `error`(失败静默) / binary frame(TTS audio bytes)

## KISS 原则
单 ws.server 真物，不抽 cogno_engine，不抽 chat_engine。

## CUDA 13.3 运行时 DLL 路径注入（关键坑，内联块抽离）
- onnxruntime-gpu 1.27 的 CUDAExecutionProvider 按 CUDA 13 编译，需要 `cudart64_13` / `cublas64_13` / `cudnn64_9`（cuDNN 9.24 cu13）等。
- **关键坑**：cuDNN 9 是 frontend(`cudnn64_9.dll`) + 多后端(`cudnn_*64_9.dll`) 分离架构，frontend 内部 `LoadLibrary` 后端时**不会自动搜索 `add_dll_directory` 加入的路径**，必须先把全部 cuDNN 9 后端 DLL 预加载进进程，否则 `cudnnCreate` 报 `Cannot load symbol` / `Could not locate cudnn_ops64_9.dll`。
- 已实测：预加载后端后 CUDA 13.3 + cuDNN 9.24 的 Whisper GPU 推理完全正常。
- 实现：`_inject_cuda13_dll_path()` 用 ctypes 预加载 `sherpa_onnx/lib/` 下全部 `cudnn_*64_9.dll`（依赖全自包含于该目录，约 1.6GB，不读 C 盘 toolkit）。

## 路径自举
`_ROOT/bin`、`_ROOT/Ikaros-memory`、`_ROOT/bin/ikaros-desktop-pet` 注入 sys.path（找 cogno_5d + cloud_chat + ikaros_monitor）。

> 注：本文件为实时语音核心链路（STT→LLM→TTS），函数级 docstring 与安全关键内联提示（超时、回退）保留在源码。CUDA 注入块已上抽。
