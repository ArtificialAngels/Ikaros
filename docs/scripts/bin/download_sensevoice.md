# bin/download_sensevoice.py — 下载 SenseVoice 中文模型

## 用途（原模块 docstring）
下载 sherpa-onnx SenseVoice 中文模型到 Ikaros 本地目录。

## 为何需要
`ikaros-voice-ws.py` 已支持本地高精度 STT（sherpa-onnx SenseVoice），该模型离线、自带情绪/事件标签 + ITN 逆文本规整，精度远高于 vosk small-cn。但模型权重（~75MB）只分布在 HuggingFace / ModelScope，且部分网络（公司代理）会拦截。此脚本在本机正常网络下一键拉取，放到：
```
E:/Ikaros/data/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue/
  ├── model.int8.onnx
  └── tokens.txt
```

## 用法
```
E:/Ikaros/portable-python/python.exe bin/download_sensevoice.py
（可选）设置环境变量 HF_TOKEN 用于需要登录的 HF 仓库
```

## 优先级
`huggingface_hub` → `modelscope` → 手动提示。

## 常量
- `REPO_ID = k2-fsa/sherpa-onnx-sense-voice-zh-en-ja-ko-yue`
- `FILES = ["model.int8.onnx", "tokens.txt"]`
- `TARGET_DIR = <ROOT>/data/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue`
