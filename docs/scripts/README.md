# docs/scripts — 脚本注释归档约定

本目录集中存放各脚本（启动 / 控制 / 核心库）的**注释正文与说明文档**。
目的是让脚本本身保持精简（只留必要提示 + 一行指针），把成段说明、背景、日志示例都收归此处。

## 映射规则

脚本源路径（相对 `E:/Ikaros`）→ 文档路径 `docs/scripts/<脚本相对路径>.md`：

| 脚本 | 文档 |
| --- | --- |
| `bin/ikaros-start.bat` | `docs/scripts/bin/ikaros-start.md` |
| `Ikaros-environment/init.bat` | `docs/scripts/Ikaros-environment/init.md` |
| `Ikaros-memory/cloud_chat.py` | `docs/scripts/Ikaros-memory/cloud_chat.md` |

即：同名，扩展名改 `.md`，保留相对目录层级。

## 脚本内应保留什么

- **一行指针**（指向本文档）：
  - `.bat` / `.ps1`（须纯 ASCII，避免 GBK 解析崩溃）：`REM See docs/scripts/bin/xxx.md`
  - `.py`（UTF-8，可中文）：`# 详细说明见 docs/scripts/.../xxx.md`
- **必要安全提示**：如 `.bat` 的「必须纯 ASCII / 禁 setlocal / 禁 timeout(用 ping)」等会直接导致脚本崩溃的硬约束，保留在脚本内。
- **简短导航标签**：如 `REM ---- 2. Memory watchdog ----` 这类步骤标题，保留以便阅读。

## 脚本内应抽出什么（写入本文档）

- 模块级大段说明 / 设计意图
- 成段背景解释（为什么这么做、踩过的坑、回滚原因）
- 日志 / 输出示例
- 参数说明、调用关系

## 维护约定

- 改了脚本逻辑且涉及说明，同步更新对应 `.md`。
- 文档用简体中文；脚本内指针用英文（`.bat` 约束）。
- 不放 secrets；路径优先走 `Ikaros-environment` 注册的环境变量。

## 批次进度

- [x] 结构建立（本文件）
- [x] 批次1：bin/*.bat 运维脚本（11 个 .bat + launch-hidden.vbs）
- [x] 批次2：bin/*.py 控制类脚本（8 个）
- [x] 批次3：bin 核心库（cloud_chat / voice-ws / monitor / activity_keywords / qwen_realtime / hermes_tts / download_sensevoice / audio_preprocessor）
- [x] 批次4：Ikaros-environment/*.bat/*.ps1（init / ikaros-env / init-ps1 / detect-root / validate-paths）
- [x] 批次5：Ikaros-memory 根与 v5 核心（49 .py 模块 docstring + 内联段落抽取至 docs/scripts/Ikaros-memory/）
- [x] 批次6：清理重组 docs/（演示图归档 docs/assets/、无重复文件、新增 docs/README.md 索引）
