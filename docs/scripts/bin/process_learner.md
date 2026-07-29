# bin/process_learner.py — 未知进程联网识别 + 持久化学习

## 用途（原模块 docstring）
监测到 `classify()` 返回 `category='unknown'` 的进程时，用云端 LLM（DeepSeek / MiniMax，本地 LLM :8080 兜底）凭其对 Windows 软件的认知识别应用并分类，然后把映射写回 `activity_keywords.PROCESS_OVERRIDES_PATH`（`process_overrides.json`）。下次启动自动加载，实现「越用越懂哥哥在干嘛」。

## 隐私约定（重要）
- 只把**进程 exe 名**发给 LLM，绝不发送窗口标题 / URL / 文件路径（标题可能含账号、文件名、聊天内容等敏感信息）。
- 隐私黑名单（KeePass 等）与自家应用（ikaros-desktop-pet）直接跳过，不联网。
- 仅接受白名单 category，LLM 胡写会被丢弃。

## 配置
`IKAROS_LEARN_PROCESSES=0` 可关闭本功能（默认开启）。

## 类别白名单（_ALLOWED）
`gaming / work / communication / entertainment / browser / private`

## 说明
- `_PROMPT_SYS` 为内置识别 prompt（数据，非注释，保留在源码）。
- 内嵌 portable-python 不自动加脚本目录到 sys.path，此处显式 `sys.path.insert(0, _BIN)`。
