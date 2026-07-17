# memory_retrieval.py

> 源文件：`Ikaros-memory/v5/memory_retrieval.py`

v5.memory_retrieval — 记忆多路检索 (R3, P1)

三路融合 (spec 2.3):
  ① FTS5 关键词搜索  (权重 fts_weight=0.3)
  ② ChromaDB 向量语义 (权重 vector_weight=0.7)
  ③ 时间范围过滤      (可选, 由调用方解析时间指代后传入)

融合分 = 向量分×0.7 + FTS5分×0.3
  → 时间衰减: ×(1 - time_decay_per_day × days_since_creation)
  → 类型 boost: 情感×1.2 / 事实×1.1 / 对话×0.8
  → 截取 top_k, 过滤 min_fused_score
  → exclude: 跳过与已知文本(用户原话/历史)重叠的记忆

纯规则融合, 不调 LLM. 所有阈值见 preprocess_config.yaml.
失败隔离: 任意一路异常静默跳过, 不抛给调用方.
