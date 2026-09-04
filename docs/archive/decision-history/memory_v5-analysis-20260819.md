# V5 记忆核心（core/memory_v5）全量分析报告

> 分析日期：2026-08-19 ｜ 范围：`E:\Ikaros\core\memory_v5` 整个包
> 数据依据：直接读取源码（store.py / memory_retrieval.py / conversation_tree.py / mcp_server.py / reflect/registry.py / tools/* / extensions/*）+ 目录清点 + 数据目录实测

---

## 0. 一句话结论

V5 是 Ikaros 最成熟、最自洽的组件：以 **一个 SQLite（`v5.db`）为唯一事实源**，向上封装了 **48 个 `v5_*` MCP 工具**、**统一检索路由 `unified_retrieve`**、**树形对话引擎**、**三条记忆轨（情感人格 / 项目 / 技能）** 与 **纯算法反思调度**。架构纪律严明（fail-open、受控种类、显式 commit、配置外置），但历史上几个"静默不落库 / 空转"的坑都源于同一个根因——`conn()` 的 `finally: rollback()` 约定，**任何写操作漏 commit 就永久丢失**。

---

## 1. 规模与分层

| 维度 | 数值 |
|---|---|
| Python 文件 | 108（顶层 65 + 子包 43） |
| 代码总行数 | **24,174** 行（不含 `.pyc`） |
| 测试文件 / 测试函数 | 37 文件 / **289 个** `test_*` |
| 对外 MCP 工具 | 48 个 `v5_*` |
| 数据文件 | `v5.db` 4.8MB（WAL）+ chroma 5 集合 + 5 个状态 JSON |
| 子包 | `extensions` `models` `reflect` `scripts` `tools` `tests`（含 `services` 空目录，0 py 文件） |

**分层结构**（自底向上）：

```
┌─────────────────────────────────────────────────────────────┐
│ 对外层     mcp_server.py (FastMCP, 48 v5_* 工具, 7 组)        │
│            tools/*.py (11 个工具子模块)                        │
├─────────────────────────────────────────────────────────────┤
│ 门面层     memory_api.py (V5MemoryAPI: store/search/get/delete)│
├─────────────────────────────────────────────────────────────┤
│ 检索层     memory_retrieval.unified_retrieve (auto 路由)       │
│            search.VectorIndex (Chroma 向量)                   │
│            entity_graph (图扩散 / PPR)                        │
├─────────────────────────────────────────────────────────────┤
│ 引擎层     conversation_tree.ConversationTree / MemoryRetriever│
│            reflect/registry (反思调度 op)                     │
├─────────────────────────────────────────────────────────────┤
│ 领域层     affect/emotion/relationship/vitality/care/         │
│            narrative/reflections/self_model/metacog/...       │
├─────────────────────────────────────────────────────────────┤
│ 存储层     store.py (SQLite WAL, FTS5, upsert, 证据)          │
│            data/v5/v5.db + chroma/ + *.json 状态文件          │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 数据模型（唯一事实源 = `v5.db`）

`store.py` 在 `conn()` 内幂等执行全部 `CREATE TABLE`，旧库自动 `ALTER` 补齐列。表清单：

| 表 | 用途 | 关键列 |
|---|---|---|
| `memory` | 主记忆表（人格/情感/项目/技能共用） | `id, content, type, tags, weight, pad_p/a/d, character, reinforcement, disputation, source_memory_id, archived, valid_to, valid_from` |
| `memory_fts` | FTS5 全文索引（触发器同步） | content/type/tags |
| `reflections` | 反思状态机（pending→confirmed→promoted→merged） | `entity, relation_type, status, evidence_version` |
| `anti_repeat` | 反重复语料（n-gram） | ngram, weight |
| `user_directives` | 用户指令（禁话题/偏好/规则，可 TTL） | `directive_type, is_active, expires_at` |
| `events` | 事件溯源日志 | `event_type, entity_id, applied` |
| `eg_entities / eg_aliases / eg_edges` | 实体图谱（Innerlife 架构） | 共现边 / 别名 |
| `eg_episodic / eg_episodic_entities / eg_activations` | 情景记忆 + 激活传播 | |
| `project_edges` | 项目知识图（类型化边 SOLVES/PREVENTS/CAUSED_BY/RELATES_TO） | source/target/relation/weight |

**受控种类（Ekko 启发）**：`__init__.py` 的 `CONTROLLED_KINDS` 限定模型可写的键空间，落盘为 `data/v5/*.json` 状态文件（实测存在 `self_model.json / affect.json / care.json / relationship.json / vitality.json`）。`validate_state_key()` 拒绝未注册键，防 state 污染。

---

## 3. 存储层关键机制（`store.py`，1140 行）

1. **`conn()` 上下文管理器 = 写操作的生死线**
   - 每次操作开新连接（避免隐式读事务挂死写事务 → "database is locked"），`busy_timeout=5000`、`journal_mode=WAL`。
   - `finally` 块执行 `c.rollback()`——**任何写操作必须显式 `c.commit()`，否则退出即回滚**。
   - 这是全包最关键的隐性契约，也是历史 bug 重灾区（见 §9）。

2. **`upsert()` 相似合并（2026-08-14 Phase 1）**
   - 带 `v5_key:` 标签的结构化记录**跳过合并**（key 即身份）。
   - 其余：LIKE 子句探针召回同类型候选 → `difflib` ratio ≥ 0.75 **或** 子串包含 → `_merge_into`（内容取长、权重取高、tags 并集、access+1、reinforcement +0.05）。根治"永远 INSERT"导致的雷同膨胀（user_trait 曾 579 条）。

3. **证据半衰期评分**：`reinforcement`/`disputation` 按 14d/7d 半衰期衰减，`evidence_score = rein − disp`。

4. **向量同步 best-effort**：写入后后台线程调 `get_vector_index().add()`，10s 超时守护——`:8587` 不可用时静默跳过，由 `vector_sync` 反思 op 后续补录。

5. **中文检索三件套**：FTS5（OR-join 多 token）→ LIKE 子串兜底（unicode61 对中文整串无效）→ 关键词稀有度排序（特异性 token 优先）。

---

## 4. 检索路由架构（`memory_retrieval.py`，929 行）

`unified_retrieve(query, scope=auto|semantic|lexical|graph|tree|temporal)` 是唯一入口（借鉴 cognee recall），**全部 fail-open**：

```
query
  │
  ├─ scope=lexical      → 仅 FTS5（空则回退 semantic）
  ├─ scope=graph        → 实体图 + 项目图扩散（空则回退 semantic）
  ├─ scope=tree         → tree_adapter.tree_scoped_retrieve（缺 tree→降级 auto）
  ├─ scope=temporal     → retrieve_temporal（过滤 valid_to 失效事实）
  └─ auto/semantic ──► retrieve() 三路融合:
                        ① FTS5 关键词 (fts_weight=0.3)
                        ② Chroma 向量  (vector_weight=0.7, 失败强制 refresh 重查)
                        ③ 时间范围    (time_range 命中给强分)
                        │
                        └─ _score_items 融合分:
                           fused = raw
                                 × 基础权重因子(base_weight_factor)
                                 × 类型化衰减(type_decay: 对话快衰/人格决策保值)
                                 × 类型 boost(type_boost)
                                 × (1 + 频率/强化/新鲜度/长期)
                                 × 情境(situational: 写代码→project 加分/时段联想)
              │
              ├─ 结果 < top_k ──► 关键词兜底(拆词逐词 LIKE 重查)
              ├─ auto 且 < 3 条 ──► 补图扩散(实体图+项目图, graph_min=0.2)
              ├─ 仍 < 3 条 ──► ThirdSpace Vault 兜底(03-知识/02-日记 关键词)
              └─ _finish: 过滤 valid_to 已失效事实 → 排序截断
```

- **意图检测**（纯规则，零 LLM）：`WHY/WHEN/ENTITY/GENERAL` → 调类型 boost（问"为什么"加权 decision/lesson）。
- **可观测性**：每条结果带 `signals`（各路径分量）与 `explain_result()` 生成"为什么召回这条"，供 pi/dsh 自主重排。
- **TTL 缓存**：同 query 20s 内直接返回，削 embedding 尖峰。
- **P6 归一化**：`_norm()` 统一结果形状（dict/Row/Memory 三种输入兼容），结构化/语义/图检索输出同一字段集。

---

## 5. 对话树引擎（`conversation_tree.py`，2181 行）

**最大的单文件**，含 `ConversationTree`（树操作）与 `MemoryRetriever`（跨库检索）两个类 + HTTP 服务在外部 `core/conversation-tree/server.py`（`:48920`）。

- **`add_turn()` 推送链**：每条对话 → `json.dumps` 内容 → `_store_fn(type="conversation", tags="… session:<persist_key>")` 写入 V5 库（`session:` 标签保证多会话不串台，H1 修复）；同时建 `ConvNode` 入树，按 `trunk_id` 唯一判定 `trunk/branch`。
- **树操作**：`branch_from / jump_to / merge_branch / unmerge / abandon / prune / set_trunk / build_cards / link_cards`（卡片画布 + 连线）。带 `threading.Lock` 串行化 + `_emit()` + `persist()`（写 `ui_conversation_tree.json`）。
- **`MemoryRetriever.retrieve()`**：路径内记忆（节点映射精确查）+ 跨分支记忆（三路融合向量检索，`label_match` 分支加分）。

---

## 6. 三条记忆"轨"

| 轨 | 工具 | 落地 | 状态 |
|---|---|---|---|
| **情感 / 人格** | `v5_*` emotion/self/care/vitality/relationship/narrative/… | `memory` 表 + `data/v5/*.json` 受控状态 | 成熟（~90%） |
| **项目（V5.4）** | `v5_project_note / _retrieve / _stats` | 同库 `v5_domain:project` + `v5_kind:{decision,pitfall,convention,idea}`；自动建 `project_edges` 类型化边 | 已接通 |
| **技能（V5.5）** | `v5_skill_write / _list / _get / _search / _remove` | `skill_store.py` → `data/v5/skills/*.md`（kebab-case，人类可读可 diff） | 渐进检索（窄命中→全文） |

设计哲学一致：**不新增数据库、不污染人格轨、复用 `V5MemoryAPI` 结构化 tag 精确匹配**；判断权交给 agent（skill 工具"什么都不做"是正当输出）。

---

## 7. 反思调度管线（`reflect/registry.py`）

`make_default_scheduler()` **默认只跑算法类 op**（决策 A，2026-08-14 停用 LLM 生成类防白烧 API）：

| op | 间隔 | 作用 |
|---|---|---|
| `dedup` | 6h | 同类型高相似归档（ratio≥0.92），保留最强 |
| `retention` | 6h | **统一生命周期**（取代 promote/cleanup/memory_promote 三 op，消除阈值打架） |
| `vector_sync` | 1h | 增量补 Chroma 向量（幂等，缺环境静默 0） |
| `rule_entity_extract` | 6h | **2026-08-19 新增**：纯规则建实体/边，激活此前一直空的实体图 |
| `reflection_promote` | 6h | 证据充分的反思晋升 persona |
| `expire_directives` | 6h | 过期用户指令置 inactive |
| `temporal_extract` | 24h | LLM 抽时间戳写 `valid_from`（fail-open） |

> ⚠️ `make_consolidate_op / make_distill_op / make_reflect_op / make_narrative_op / make_self_discovery_op` 虽已实现但**未注册进默认调度器**（保留可手动调用）。`make_promote_op / make_cleanup_op / make_memory_promote_op` 已被 `retention` 取代。

---

## 8. 扩展骨架（`extensions/`，experimental，默认不启用）

| 模块 | 作用 | 约束 |
|---|---|---|
| `graph_export.py` | 只读导出 graphify 兼容 `graph.json`（SQLite 唯一源，图只是视图） | 不引图数据库后端 |
| `ontology_align.py` | difflib 模糊对齐 eg_entities/aliases | 不引 RDFLib |
| `rule_entity_extract.py` | **2026-08-19 新增**，纯规则激活实体图 | 无 LLM |
| `temporal_graph.py` | 矛盾检测 + 时效图谱（`valid_to` 失效） | |
| `token_compressor.py` | 上下文缩减（LLM 摘要 / 硬 top_k 的替代） | |
| `tree_adapter.py` | 记忆/压缩对树形对话做定向适配（非侵入包裹） | |

---

## 9. 关键风险与历史坑（务必知悉）

1. **`conn()` 的 `finally: rollback()` 约定** —— 最致命的隐性契约。
   - 历史上 `promote_op` / `cleanup_op` / `memory_promote_op` 都曾**写后不 commit → 短期记忆从未真正转存/归档**（2026-08-02 修）。
   - 现所有 op 已补 `c.commit()`，但**新增写路径时仍必须显式 commit**（含 `executescript` 后的 DDL 在某些路径下也需注意）。

2. **实体图长期空转**：`entity_graph.run_episodic_consolidation` 依赖 LLM Stage A/B 且**从未被调度**，导致 `eg_entities/eg_edges` 全空、图扩散检索空转 —— **今日（2026-08-19）由 `rule_entity_op` 以纯规则激活**。需观察后续实体图是否真正填充、auto 补路是否提升召回。

3. **嵌入模型事故（2026-08-14）**：`nomic-embed-text-v2-moe` 在 llama.cpp 下输出**全零向量**（chroma 1220 条全零，语义检索静默死）。换成 `bge-m3-q8_0`（cls pooling + 检索指令前缀）后真实 cos 0.93 vs 0.33，融合尺度重标定 `min_fused_score` 0.6→0.3。

4. **FTS5 中文分词**：unicode61 把连续中文当单 token，`MATCH '主力'` 0 命中 → 必须用 LIKE 子串兜底；长句多 token 改 AND→OR 召回优先。

5. **`store.conn()` 每操作开新连接**：高并发下 WAL + busy_timeout 兜底，但进程级 WAL checkpoint 仅在首次连接执行一次（R5 优化，原每次写都 TRUNCATE 抵消批量写优势）。

---

## 10. 测试覆盖概览

- 289 个测试函数 / 37 文件，远超 AGENTS.md 记录的 199（持续增长中）。
- 重点覆盖：**统一检索**（`test_unified_retrieve` 211 行）、**对话树**（926 行，含并发 `test_conversation_tree_concurrency`）、**FTS 中文端到端**、**时效 supersede 链**、**检索信号**（`test_retrieval_signals`）、**upsert 合并**（`test_upsert`）、**V5.7 推荐**等。
- 缺位观察：`rule_entity_extract`（今日新增）尚未见对应测试文件；`graph_export` / `token_compressor` / `tree_adapter` 等 experimental 模块测试稀疏（符合"默认不启用"定位）。

---

## 11. 改进建议（按优先级）

| 优先级 | 建议 | 理由 |
|---|---|---|
| 🔴 高 | 为今日新增的 `rule_entity_extract` / `rule_entity_op` 补单测 + 跑一次观察 `eg_*` 填充量 | 修复了长期空转缺陷，需验证真实生效 |
| 🔴 高 | 在 `store.py` 写路径统一封装 `with conn() as c: … c.commit()` 为 `@commit` 装饰器/helper，彻底消除"漏 commit"类回归 | 根因层面消除 §9.1 隐患 |
| 🟡 中 | 给 `services/` 空目录补 `README` 或删除，避免误导 | 当前 0 py 文件 |
| 🟡 中 | 评估 `conversation_tree.py` (2181 行) 拆分为 `engine.py` + `cards.py` + `retriever.py` | 单文件过大，维护成本高 |
| 🟢 低 | `extensions/` 三件套（graph_export/token_compressor/tree_adapter）若确认长期不用，归档到 `tmp/` 或显式标注状态 | 减少认知负担 |
| 🟢 低 | Vault fallback 依赖 `THIRDSPACE_VAULT` 环境变量，文档化默认回退路径 | 可发现性 |

---

## 12. 总结

`memory_v5` 是一个**工程纪律极强、演进痕迹清晰**的记忆引擎：单一 SQLite 事实源 + 48 工具 MCP 面 + 自动路由的统一检索 + 树形对话 + 三轨记忆 + 纯算法反思。它的成熟度来自对一系列真实事故（全零向量、静默不落库、空转图、中文检索失效）的逐一定位与根治。当前最值得关注的是**今日上线的规则实体图抽取**是否真正激活了沉寂的图检索路径，以及**写路径 commit 契约**是否能在后续重构中被结构性地保护起来。

---

*附：数据目录实测 `data/v5/` 含 `v5.db`(4.8M, WAL)、`chroma/`(5 集合 + sqlite3)、`ui_conversation_tree.json`(13KB)、`self_model/affect/care/relationship/vitality.json` 状态文件、`conversation_tree.json`、`sessions.json`、`latest_thought.json`。*
