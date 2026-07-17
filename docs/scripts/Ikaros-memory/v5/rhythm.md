# rhythm.py

> 源文件：`Ikaros-memory/v5/rhythm.py`

v5.rhythm — 节奏感知 (R2, P0)

目标: 让云端知道 "哥哥什么时候说的、距上轮多久、现在什么时段".
输出为结构化数据 (spec 2.2), 由 cloud DeepSeek 据之自然生成语气, 不注入情感化文案.

数据源: v4.db 最近一条记忆的 created (近似上轮对话时间).
  - conversation 记忆每轮对话都会写入, created 即上轮时间, 足够近似.
  - 纯规则计算, 不调 LLM (<50ms).
