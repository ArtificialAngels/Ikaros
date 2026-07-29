# bin/hermes_tts.py — 复用 Hermes Agent 内置 TTS

## 用途（原模块 docstring）
本脚本在 core/hermes 的 venv 下运行，直接复用 `core/hermes/tools/tts_tool._generate_edge_tts`（Hermes Agent 内置 TTS 生成逻辑，edge-tts 后端）。voice-ws 通过 subprocess 调用本脚本，把文本合成 mp3 后再取回二进制帧下发给桌宠。

## 为什么绕一层
core/hermes 依赖其自有 venv 的包；在 voice-ws 的 portable-python 进程里硬 import 会缺依赖。subprocess 到 hermes venv 既隔离又复用「Hermes Agent 内置的 TTS 服务」。

## 用法（均由 voice-ws 调用）
```
hermes_tts.py <textfile> <outfile>
  <textfile> : UTF-8 文本路径（避免命令行引号/编码问题）
  <outfile>  : 输出 mp3 路径
```
成功打印 outfile 绝对路径；失败打印 `{"error":"..."}` 到 stdout 并退出码非 0。

## voice 解析
优先用 Hermes 配置 `tts.edge.voice`；若该值是无效占位（如 `"cn-"`），回退到 `zh-CN-XiaoxiaoNeural`，保证一定能合成。

## 路径自举
`HERMES_ROOT` = env 或 `E:/Ikaros/core/hermes`；`sys.path.insert(0, HERMES_ROOT)` 找 core/hermes 的 tools 包。
