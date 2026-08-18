# V5 架构融汇摘要（2026-08-14，P1-P8）

> 背景：V5 吸收了大量外部思路（cognee / Graphiti / TencentDB / LLMLingua /
> graph-memory / mnemon），但功能散在多个独立模块。本摘要记录三轮收敛后的
> **"收敛后"架构**（唯一入口 / 唯一形状 / 唯一重要性 / 唯一配置源 / 可观测性）。
> 详细变更见 AGENTS.md 的 P1-P8 条目；本文件是快速参考。

## 1. 检索：唯一入口

```
memory_retrieval.unified_retrieve(query, scope=auto|semantic|lexical|graph|tree|temporal)
  ├─ semantic → retrieve()               # 内部语义引擎 (FTS+向量+时间, 外部零直连)
  ├─ graph    → _graph_retrieve()        # 实体图 + 项目知识图, 一致性打分
  ├─ tree     → tree_adapter             # 树域加权
  ├─ temporal → retrieve_temporal()      # 时效过滤 (valid_to)
  └─ auto     → semantic 不足补 _graph_retrieve
```

- 已删并行实现：`fused_search`（旧双路）、`rules_retriever`（孤儿）、`gated_retrieval`（骨架）。
- 所有消费方（MCP 工具 / memory_api / conversation-tree / tree_adapter / dissonance / metacog）都经 `unified_retrieve`。

## 2. 结果形状：唯一归一化

- `_norm(dict|sqlite3.Row|store.Memory) → 统一 19 字段`：id/content/type/weight/tags/created/
  pad_p/a/d/source/score/access_count/reinforcement/last_accessed/long_term/intent/signals/relation/kind。
- `memory_api._row_to_dict` 委托 `_norm`（结构化路径标记 `source="structured"`）。
- **可观测性**：`signals`（fts/vector/time/base_weight/type_decay/type_boost/frequency/situational/ei）
  + `intent` + `relation` + `kind` 全路径透传；`explain_result(item)` 生成"为什么召回这条"的 `why` 字段，
  已接入 `v5_memory_search` / `v5_project_retrieve`（pi 可见召回依据）。

## 3. 重要性：单一口径

- `importance.effective_importance(weight, access, last_accessed, now, reinforcement)`
  = weight × (1+reinforcement×0.5) × log2(access+1) × 0.5^(days/30)。
- 三处共用：`store.upsert`（写时强化 reinforcement → EI↑）、`_score_items`（signals.ei 透出）、
  `lifecycle.retention_pass`（promote/archive 判定）。
- 生命周期：`retention` op 取代 promote/cleanup/memory_promote（单轮 demote→promote→archive）。

## 4. 图：统一 `_graph_retrieve`

- 同一张"V5 图"的两个边类型：`eg_edges`（实体共现 + PPR 排序）+ `project_edges`
  （笔记类型化边 SOLVES/PREVENTS/CAUSED_BY/RELATES_TO）。
- 时效（`valid_to`）在 `_finish` 统一过滤；`graph_min_score` 防噪音。

## 5. 配置：单一权威源

- `preprocess_config.yaml` = 权威源；`preprocess_config.py._DEFAULTS` = 兜底（已全量同步，防漂移）。
- 防漂移测试 `tests/test_config_alignment.py`（键覆盖 / 无陈旧键 / 关键值 / 合并结果）。

## 6. 存储真相源

| 层 | 角色 |
|---|---|
| `v5.db` (SQLite+FTS5) | **唯一真相源**（memory/reflections/events/project_edges/eg_*） |
| `chroma/` | 派生向量索引（bge-m3 1024 维, 可重建; 重建后须校验非零率） |
| JSON 状态 | 灵魂状态（self_model/affect/relationship…, 非记忆, 不做检索融合） |

## 7. 扩展接入状态

| 扩展 | 状态 |
|---|---|
| token_compressor | ✅ 已接入 `on_pre_compress` |
| temporal_graph | ✅ 已接入（supersede + temporal scope） |
| ontology_align | ✅ 已接入 entity_graph_search |
| tree_adapter | ✅ 已接入 tree scope |
| ~~gated_retrieval~~ | 🗑 已删（骨架, 分层思想由 should_recall+type_decay 覆盖） |

## 8. 质量基线

- 测试：**289 passed**（P1-P8 后）。
- 真实嵌入评分（bge-m3 golden-query）：**composite 95.2**（hit@1=0.9 / hit@3=1.0 / MRR=0.95）。
