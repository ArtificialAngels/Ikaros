# bin/activity_keywords.py — 应用分类词库（精简版）

## 用途（原模块 docstring）
移植自 N.E.K.O 的 `config/activity_keywords.py`（3089 行全量词库），按需裁剪为 Ikaros 常用集合。职责：把 `(process_name, window_title, url)` 映射成结构化类别，供状态机推出 `activity_state`。

## 匹配语义
- 进程名精确小写匹配（PROCESS_MAP）
- 浏览器进程走域名匹配（BROWSER_DOMAIN），否则标题匹配（TITLE_MAP）
- 隐私黑名单（PRIVATE_*）命中即 `category='private'`
- 全部大小写不敏感；CJK 关键词直接子串匹配

## category 优先级（高→低）
`gaming > work > communication > entertainment`（与 N.E.K.O 一致：游戏最强免打扰信号；工作压过后台 IM/视频）。

## 用户学习覆盖（process_overrides.json）
- `PROCESS_OVERRIDES_PATH` = 同目录 `process_overrides.json`，优先级高于静态 PROCESS_MAP。
- 监测到未知进程时，`process_learner` 用 LLM 识别并写回；下次启动自动加载。
- **只存 exe 名，绝不存窗口标题（隐私）**。
