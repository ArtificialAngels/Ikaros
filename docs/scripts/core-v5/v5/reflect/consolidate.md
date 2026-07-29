# consolidate.py

> 源文件：`Ikaros-memory/v5/reflect/consolidate.py`

v5.reflect.consolidate — V5.1 对话整合 (小模型提取 + 大模型验证)

设计目标:
  - API 兼容 V3 memory_reflect.consolidate_conversations
  - 双轨: 小模型(local LLM)提取 + 大模型(DeepSeek V4 flash)验证
  - 容错: 大模型验证失败时降级到本地 (V3 默认保留有"全保留"bug, V4 改成显式)

V3 痛点 (memory_reflect.py:259-293 _verify_extractions):
  - LLM 失败时默认保留所有 (line 276-277 "keep all")
  - 失败时垃圾累积, 无质量门
  V4 修: 大模型失败时显式 log, 保留前 50% (按 weight 截断, 不用 LLM 决定)
