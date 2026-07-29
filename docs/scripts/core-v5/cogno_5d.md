# cogno_5d.py

> 源文件：`Ikaros-memory/cogno_5d.py`

cogno_5d.py — 伊卡洛斯 5 维认知锚 v2 (自然语言输出)

每次对话自动注入认知上下文到 system prompt, 输出为自然语言叙述:

  现在是 7 月 5 日周六深夜 23:30, 哥哥通常在这个时间写代码或调试项目。
  在上海的 LEGION9 上。对话围绕记忆系统优化, 哥哥语气好奇, 对方案持开放态度。

v2 改进:
  1. 时段推断: 时间 → "哥哥可能在做什么" (基于可配置作息表)
  2. 情绪增强: 关键词表扩充 + 模式匹配 (不只单词, 看组合)
  3. 上下文升级: 话题摘要 (不只截断上轮, 追踪对话主题)
  4. 自然语言输出: 模型直接"理解", 不需要"解析"
  5. 缓存优化: 设备 (24h) / 地理 (1h)

用法:
  from cogno_5d import enrich, enrich_reply
  prompt_prefix = enrich(user_text, history)   # → str, 自然语言认知上下文
  tagged = enrich_reply(reply, user_text)       # → dict, 记忆 ingest 元数据

API 兼容:
  所有 v1 函数签名保留: get_time_str, get_machine_id, get_geo_location,
  infer_emotion, compress_context, enrich, enrich_reply, reset_context

## 内联注释摘录

# ─── 接入真实前台活动采集器 (ikaros_monitor, 位于 bin/) ───
# 本项目的 portable-python 是内嵌发行版, 不会自动把脚本目录加入 sys.path,
# 必须显式插入 bin/ 才能让 cogno_5d 找到 ikaros_monitor (与 voice-ws 一致).

