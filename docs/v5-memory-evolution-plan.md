# V5 记忆演进方案：借鉴 cognee 的分层记忆装配蓝图

> 目标读者：workbuddy（实现方）。本文档自包含，所有行号引用为 `E:\Ikaros\core\memory_v5\` 当前真实代码。
> 参考来源：`E:\Ikaros-something\reference project\cognee-main`（topoteretes/cognee 调研，2026-08-01）。
> 日期：2026-08-01

> **执行状态（2026-08-01 已完成）**：阶段 1–5 全部落地并验收（V5 170 测试 + 对话树 31 测试全绿，含新增 unified_retrieve 7 / temporal_supersede_chain 5 / ontology_align 4）。
> 额外修复的隐藏 bug：① `temporal_graph.supersede_memory` 无 commit（`store.conn()` 退出默认 rollback）→ supersede 从未真正生效；② `_valid_to_map` 返回 int 键与检索 str id 失配 → 过期过滤静默失效；③ `memory_promote` op 原顺序（先 promote 后 demote）同事务内刚晋升记忆被立即回收。
> 遗留：`ontology_align` 默认关（`cache.ontology_align_enabled`）；`temporal_extract` op 需 `apply_migration()` 已跑（真实 v5.db 启动时补跑一次）。

---

## 0. 一句话目标

**把 V5 已有的分散记忆零件（三路融合检索 / 实体图扩散 / 分层门控 / 时间戳迁移 / 反射调度）装配成一个完整的分层记忆系统——会话快缓存 + 多路自动路由检索 + 永久库后台演进——而不是再抄一套 cognee。**

cognee 的教训（调研结论）：它本身复杂度极高（30+ API 路由、20 种 SearchType、20+ retriever、多套时间管线），纯抄会把 V5 拖垮。**它的价值是"整合蓝图"**：4 个核心 API（remember/recall/forget/improve）+ 两档记忆分层 + 检索自动路由 + 图空回退。而 V5 的零件比 cognee 还全——缺的是装配层。

---

## 1. 现状盘点：V5 已有 vs 缺口（关键事实，已代码级验证）

| # | cognee 借鉴点 | V5 现状（精确位置） | 差距 |
|---|--------------|-------------------|------|
| 1 | 检索多路路由 + 图空回退 | `memory_retrieval.retrieve` 已三路融合（FTS 0.3 + 向量 0.7 + 时间衰减，`memory_retrieval.py:86-160`）+ Vault 关键词回退（L164）+ TTL 缓存（L174）；`search.fused_search`（`search.py:306`）、`search.entity_graph_search`（`search.py:346`）、`rules_retriever.retrieve_relevant_rules`、`extensions.tree_adapter.tree_scoped_retrieve` 全部**独立存在** | 检索器是散的：无统一路由层，auto 选择语义/词法/图/规则靠调用方自己拼；entity_graph 扩散没进融合路 |
| 2 | 记忆两档分层（快缓存 + 后台桥接永久库） | `memory` 表已有 `short_term/long_term` 字段（`store.py:35-36`）；`reflect/registry.py` 已注册 11 个 ReflectOp（含 `promote` L119、`cleanup` L155、`vector_sync`） | 字段存在但**检索排序未用**；promote op 的桥接语义未与两档分层对齐（无"会话快缓存→后台晋升永久"的显式链路） |
| 3 | 轻量本体对齐（外部词表 + difflib 模糊匹配，零 LLM 成本） | `entity_graph.py` 有 `eg_entities/eg_aliases` 表 + `find_entity_candidates`（L340，仅精确/包含匹配） | 无本体层；候选匹配没用 embedding 列、无模糊对齐 |
| 4 | 反馈/频率权重调检索排序 | `memory` 表已有 `weight / access_count / last_accessed / reinforcement`（`store.py:26-49`） | 排序只用了时间衰减（`memory_retrieval.py:145`），frequency/feedback 全闲置 |
| 5 | 事件+时间戳抽取（Event/Interval）补 temporal_graph，而非完整 graphiti | `extensions/temporal_graph.py` 有 `apply_migration`（L42 加 valid_from/valid_to）、`supersede_memory`（L76）、`resolve_dissonance_supersede`（L118）、`retrieve_temporal`（L163）、`filter_expired_episodic`（L182）——**可运行代码但未接入主链路**；`dissonance.py` 的 `_record_dissonance`（L122）之后无人接 supersede | temporal_graph 与 dissonance 无实接；`eg_edges` 无 `relation_type`（TODO，`temporal_graph.py:96`）；无事件抽取 op |

**核心结论**：V5 不是"缺功能"，是"缺装配"。cognee 给出的蓝图 = 把上面 5 组零件串成一条分层记忆链路。目标 80%+ 功能，不引 graphiti、不迁图库、保持 SQLite 便携。

---

## 2. 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│  V5 分层记忆（v5.db 单库, SQLite 便携）                        │
│                                                             │
│  [写入] store() ──→ memory 表 (short_term=1) ──→ 会话快缓存   │
│                        │ 后台: ReflectScheduler (已有 11 op)  │
│                        ▼                                    │
│  [演进] promote/distill/consolidate ──→ long_term=1 永久记忆  │
│                        │ 冲突: dissonance → temporal_graph   │
│                        │       supersede (valid_to 失效)     │
│                        ▼                                    │
│  [检索] unified_retrieve(query, scope=auto)                  │
│    ├─ auto 路由: 语义(fused) / 词法(FTS) / 图(扩散) / 规则    │
│    ├─ 树域加权: tree_scoped_retrieve (已接对话树)             │
│    ├─ 分层门控: gated_retrieve (高价值先答, 低价值再检索)      │
│    └─ 排序: fused score + 时间衰减 + 频率/反馈权重 + 本体对齐  │
└─────────────────────────────────────────────────────────────┘
```

**原则**：`unified_retrieve` 是唯一入口（对应 cognee 的 `recall`），内部按 scope 自动路由，图/词法空则回退语义——调用方（hermes_provider / memory_api / conversation_tree / orchestrator / router）不再各自拼检索。

---

## 3. 分阶段改动清单

### 阶段 1：统一检索路由层（unified_retrieve）—— 最高性价比

**现状**：5 个检索器独立（见 1#1），调用方各自拼。
**目标**：新增 `memory_retrieval.unified_retrieve(query, *, top_k=5, scope="auto", node_id=None, character="") -> list[dict]`，对应 cognee 的 recall + auto scope：

- `scope="auto"`（默认）：先语义三路融合（现有 `retrieve`）→ 结果 < 3 条时**自动补路**：`entity_graph_search`（扩散激活）→ `rules_retriever`（仅 query 含规则意图词时）→ 仍空才走 Vault 关键词回退（现有逻辑上提）
- `scope="graph"`：优先 `entity_graph_search`，空则回退 `retrieve`
- `scope="lexical"`：仅 FTS（`store.search`），空则回退 `retrieve`
- `scope="tree"`：委托 `tree_scoped_retrieve`（需 `node_id`）
- 所有结果统一归一化：`{id, content, score, source}`（source ∈ `semantic/lexical/graph/rules/vault`），按 fused score 降序截 top_k
- 保持 fail-open：任一路异常静默跳过，不阻塞

**改动文件**：
- `memory_retrieval.py`：新增 `unified_retrieve`（复用现有 `retrieve` 内部逻辑，抽 `_fuse` 辅助）；导出 `SCOPES` 常量
- `search.py`：确认 `entity_graph_search` 返回结构可归一化（补 `source` 字段，纯 additive）
- `rules_retriever.py`：确认 `retrieve_relevant_rules` 返回结构（补 source 归一化包装）
- `preprocess_config.yaml`：`memory_retrieval` 段加 `auto_route` 开关（默认 true）、`graph_min_score`（默认 0.2）

**接线替换**（把散调用换到统一入口）：
- `memory_api.py:153`（V5MemoryAPI fuse 路径）→ `unified_retrieve(scope="auto")`
- `conversation-tree/server.py:782`（memory_search 工具）→ `unified_retrieve(scope="tree", node_id=...)` 保持树域
- `tree_adapter.py:80` 内部仍直接调 `retrieve`（树域加权保持独立，不动）
- `orchestrator.py:208 / router.py:145`（规则检索）保持 `retrieve_relevant_rules` 不动（它是意图专用通道，不进通用路由）

**验收（阶段 1）**：
- 单测：`unified_retrieve` 四 scope 各自命中正确 source；auto 在语义空时能补到图/词法结果；任一路抛异常不阻塞
- 集成：`conversation-tree` 的 memory_search 工具仍返回树域加权结果；memory_api 行为不回退

---

### 阶段 2：记忆两档分层（快缓存 → 永久库显式桥接）

**现状**：`short_term/long_term` 字段存在但检索排序未用；promote op 已存在但语义是"晋升反思到 persona"（`registry.py:160 reflection_promote`），不是记忆分层桥接。
**目标**：显式两档 + 后台桥接：

- **写入**：`store.py` 保持默认 `short_term=1`（现状）
- **桥接 op**：新增 `make_memory_promote_op()`（`reflect/registry.py`，间隔 6h）：
  - 扫描 `short_term=1` 且 `access_count >= 3`（高频访问）或 `reinforcement >= 1` 或 age > 30 天的记忆 → `long_term=1`
  - 反向：`long_term=1` 且 90 天零访问 → 降回 short_term（冷记忆回收）
  - 注册进 `make_default_scheduler`（`registry.py:217-228`）
- **排序接入**：`memory_retrieval.py` 融合排序加 `long_term` 小幅 boost（+0.05，防永久记忆被新记忆挤出）；`preprocess_config.yaml` 加 `long_term_boost`

**改动文件**：`reflect/registry.py`（新 op + 注册）、`memory_retrieval.py`（boost）、`preprocess_config.yaml`、`tests/test_reflect_registry.py`（新增）

**验收（阶段 2）**：
- 构造 high-access 记忆 → 手动跑 `run_all` → long_term=1；90 天冷记忆 → 降回
- 检索排序中 long_term 记忆得分略升；不破坏现有 20 个检索测试

---

### 阶段 3：轻量本体对齐（零 LLM 成本）

**现状**：`eg_aliases` 表存在，`find_entity_candidates` 仅精确/包含匹配（`entity_graph.py:340`），未用 embedding 列。
**目标**：新增 `extensions/ontology_align.py`，借鉴 cognee 的 RDFLib + FuzzyMatchingStrategy（difflib cutoff 0.8）模式，但用 V5 自己的资源：

- `align_entity(surface, threshold=0.82) -> Optional[int]`：`difflib.SequenceMatcher` 对 `eg_entities.name / eg_aliases.alias` 做模糊匹配，返回 entity_id；命中率低于阈值返回 None（不误配）
- `find_entity_candidates_fuzzy(surface, top_k=3)`：包含匹配 + difflib 排序（替代现有 L340 的纯精确/包含）
- `alias_extract(text)`：从文档抽取"X（又称 Y / 也叫 Y）"别名对写入 `eg_aliases`（规则抽取，零 LLM）
- 可选增强（默认关）：`entity_graph_search` 的 `find_entity_candidates` 换用 fuzzy 版，提升实体召回
- 纯 additive：不改变现有函数签名，新增函数 + 一个开关

**改动文件**：`extensions/ontology_align.py`（新）、`entity_graph.py`（L340 旁挂 fuzzy 入口）、`preprocess_config.yaml`（`ontology_align_enabled`）

**验收（阶段 3）**：
- 单测：模糊命中（"伊卡洛斯"→"Ikaros"）、低于阈值拒绝、别名抽取正则
- 现有 `entity_graph` 相关行为不回退（精确匹配仍可用）

---

### 阶段 4：反馈/频率权重进检索排序

**现状**：`access_count / last_accessed / reinforcement` 字段全闲置（只有时间衰减在用）。
**目标**：`memory_retrieval.py` 融合排序加**频率/反馈分量**（借鉴 cognee `apply_feedback_weights.py` 思路，纯 SQL 侧实现，零新依赖）：

```
score_final = score_fused * (1 + frequency_boost)
frequency_boost = min(0.25, log2(access_count+1) * 0.05)          # 高频 → 最高 +0.25
               + min(0.15, reinforcement * 0.10)                    # 强化(用户正反馈) → 最高 +0.15
               + freshness: 最近 7 天访问过 → +0.08 (last_accessed)
```

- 权重进 `preprocess_config.yaml`（`frequency_weight / reinforcement_weight / freshness_weight`，可调可关）
- `dissonance` 触发时对被替代记忆 `reinforcement -= 0.5`（防冲突内容被反复召回）——接在 `_record_dissonance`（`dissonance.py:122`）后，**同时完成阶段 5 的接线点**（见下）

**改动文件**：`memory_retrieval.py`（排序计算）、`preprocess_config.yaml`、`dissonance.py`（减权重一行）

**验收（阶段 4）**：
- 构造高 access_count / reinforcement 记忆 → 排序上升；权重全关时行为与现状一致（回归）
- 现有 `test_memory_retrieval.py` 5 用例全绿（排序逻辑改动需同步断言）

---

### 阶段 5：时间戳抽取 + dissonance→supersede 接线

**现状**：`temporal_graph.py` 骨架可运行但无人调用；`_record_dissonance`（`dissonance.py:122`）写冲突记录后无人 supersede；`eg_edges` 无 `relation_type`。
**目标**：

- **接线**（最小改动）：`dissonance.py` 的 `_record_dissonance` 末尾（写 `type='dissonance'` 记忆后）调用 `resolve_dissonance_supersede(conflict_id, old_id)`（`temporal_graph.py:118`），让被 NLI 判定矛盾的旧记忆 `valid_to` 失效——**这一步把 temporal_graph 从"死代码"变成活链路**
- **事件抽取 op**：新增 `make_temporal_extract_op()`（`reflect/registry.py`，间隔 24h），借鉴 cognee `tasks/temporal_graph` 的 Event/Interval 模型但**不引 graphiti**：
  - 扫描近 24h 新记忆，LLM（DeepSeek，走现有 `hermes_client`）抽 `{event, timestamp/interval}` 二元组，写 `valid_from`（`temporal_graph.apply_migration` 已建列）
  - 抽不出时间的事件跳过（不阻塞）；LLM 失败降级"仅保留记忆、不标时间"（fail-open）
- **检索**：`retrieve_temporal`（L163）包装进 `unified_retrieve` 的 `scope="temporal"`（query 含时间词时 auto 可路由）
- **TODO 修补（可选，后置）**：`eg_edges` 加 `relation_type` 列支持精确 supersede（`temporal_graph.py:96` 注释），列为阶段 5 的子项，可跳过

**改动文件**：`dissonance.py`（接 supersede）、`reflect/registry.py`（temporal_extract op）、`extensions/temporal_graph.py`（retrieve_temporal 返回归一化）、`memory_retrieval.py`（scope="temporal" 路由）、`preprocess_config.yaml`

**验收（阶段 5）**：
- 制造矛盾记忆对 → 触发 dissonance → 旧记忆 `valid_to` 被置 → `retrieve_temporal` 不再返回过期记忆
- 事件抽取 op 对含时间戳的记忆标 valid_from；无时间戳的不误标
- 现有 dissonance 测试（`test_new_modules.py` / `test_tools_extra.py`）不回退

---

## 4. 风险与对策

| 风险 | 对策 |
|------|------|
| `unified_retrieve` 改动破坏现有 7 个调用方 | 阶段 1 只**新增**入口 + 逐个替换（memory_api 先行），每个替换点跑对应测试；`retrieve` 原函数保留为 `scope="semantic"` 等价物，不回退 |
| 频率/反馈权重改变排序破坏既有检索测试 | 权重全部进 `preprocess_config.yaml` 可关；阶段 4 同步更新 `test_memory_retrieval.py` 断言（显式权重下验证排序） |
| temporal_graph 接线后 supersede 误伤仍在使用的记忆 | `resolve_dissonance_supersede` 只在 `_record_dissonance` 的 NLI 判定 contradiction 时触发（现状已如此）；接线前先跑 dissonance 单测锁定行为 |
| 本体模糊匹配误配实体 | cutoff 0.82（对齐 cognee 0.8 并略保守）+ 全角/半角/大小写归一；低于阈值返回 None 不猜测 |
| 后台 op 增加调度负担 | 所有新 op 间隔 ≥6h（temporal_extract 24h），复用 ReflectScheduler 现有机制（`reflect_state.json`），不新增调度线程 |
| "借鉴太多变成抄" | 明确边界：**不引 ladybug/Kuzu/Neo4j、不引 graphiti-core、不引 RDFLib**——本体对齐用 difflib 标准库，时间抽取用现有 hermes_client，全部保持 SQLite + 标准库 |

---

## 5. 测试计划

- **回归**：`pytest tests/ -k "memory_retrieval or search or tree"`（约 20 用例）必须全绿；新增测试不破坏现有
- **新增单测**：
  - `tests/test_unified_retrieve.py`：4 scope 路由 + auto 补路 + 单路异常 fail-open（6-8 用例）
  - `tests/test_memory_promote_op.py`：高频晋升 / 冷回收 / 排序 boost（4 用例）
  - `tests/test_ontology_align.py`：模糊命中 / 阈值拒绝 / 别名抽取（4 用例）
  - `tests/test_temporal_supersede_chain.py`：矛盾对 → supersede → retrieve_temporal 过滤（3 用例）
- **集成（手工）**：
  1. 对话树里问一个"我们之前聊过的 X"→ 走 `scope="tree"` 树域加权不变
  2. 造 3 条高频记忆 → 跑 `run_all` → 观察 promote 到 long_term
  3. 造矛盾记忆 → 触发 dissonance → 查旧记忆 valid_to 被置
  4. 权重全关（config）→ 检索行为与改动前逐条一致

## 6. 涉及文件清单

| 文件 | 改动 |
|------|------|
| `core/memory_v5/memory_retrieval.py` | 阶段 1（unified_retrieve + _fuse 抽取）、阶段 4（频率/反馈权重）、阶段 5（scope="temporal"） |
| `core/memory_v5/search.py` | 阶段 1（entity_graph_search 补 source，additive） |
| `core/memory_v5/rules_retriever.py` | 阶段 1（source 归一化包装） |
| `core/memory_v5/reflect/registry.py` | 阶段 2（memory_promote op）、阶段 5（temporal_extract op） |
| `core/memory_v5/extensions/ontology_align.py` | 阶段 3（新文件） |
| `core/memory_v5/entity_graph.py` | 阶段 3（fuzzy 候选入口） |
| `core/memory_v5/dissonance.py` | 阶段 4（reinforcement 减权）、阶段 5（接 supersede） |
| `core/memory_v5/extensions/temporal_graph.py` | 阶段 5（retrieve_temporal 归一化） |
| `core/memory_v5/memory_api.py` | 阶段 1（换 unified_retrieve） |
| `core/conversation-tree/server.py` | 阶段 1（memory_search 工具换 unified_retrieve(scope="tree")） |
| `core/memory_v5/preprocess_config.yaml` | 各阶段权重/开关 |
| `docs/ARCHITECTURE.md` + `AGENTS.md` | 落地后同步（记忆检索链路描述） |

**建议实施顺序**：阶段 1（路由层，独立可验收）→ 阶段 4（权重，改动最小）→ 阶段 5（时间接线，让 dissonance 闭环）→ 阶段 2（两档分层）→ 阶段 3（本体，纯增量）。每阶段独立可交付、可回滚；阶段 1+4 完成后即达成 cognee 调研的"检索多路路由"主收益。

---

# 附录 A：CortexFS 调研借鉴清单（2026-08-10）

> 参考来源：`E:\Ikaros-something\reference project\cortexfs-main`（LIghtJUNction/cortexfs，Rust FUSE agent runtime，v0.1.7，MIT）。
> 定位：Linux FUSE 文件系统形态的 agent 运行时——把模型/agent/工具/会话挂载成 `/ctx` 普通文件，ls/cat 即可检查。
> 与 Ikaros 关系：形态不同（Linux FUSE vs Windows 桌面+Web），**理念可借鉴，代码不可复用**。以下只收"对 Ikaros 有落地价值"的点。

## A.1 值得借鉴的 4 个理念（按性价比排序）

| # | CortexFS 做法 | Ikaros 现状 | 借鉴价值 / 落地方式 |
|---|---|---|---|
| 1 | **会话即可审计的普通文件**：会话历史是普通 `messages.jsonl` / `events.jsonl`，提示词上下文可从文件重建，任何工具可读 | 对话内容在 v5.db + ui_conversation_tree.json（JSON 指针 + DB 内容分离） | 中。conversation-tree 已部分采用（拓扑 JSON 可审计）。可考虑给 v5.db 会话记录加 JSONL 导出/镜像，让对话可 diff 可 grep |
| 2 | **host-owned execution**：宿主串行化工具调用、每次重查权限、返回规范结果，agent 才能继续 | Hermes gateway 工具循环已是此模式（agent 提议 → gateway 执行 → 结果透出） | 低（已实现）。CortexFS 的增量：工具调用前**每次都重查权限**（我们的 permission 是会话级配置，非调用级） |
| 3 | **性能门控纪律**：性能改动必须先建可复现基线，收益须 > max(3%, 2×噪声)；禁 unsafe、禁 target-cpu=native；由独立 reviewer 审核 | 无正式门控；本次 LongMemEval 评测（8-10）算是补了一次基线 | 高。落地：`bin/eval-longmemeval.py` 留作检索性能回归基线；后续检索改动先跑它再动代码 |
| 4 | **单一工具路径（tsh）+ 工具即文件**：agent 通过文件系统视图发现工具，不暴露庞大原生工具列表 | 48 个 v5_* MCP 工具全量暴露给 gateway | 中。可考虑工具分组/按需暴露（类似 Hermes toolset），但需评估成本；V5 工具已按功能分模块（memory/self/care/...），可先做"按会话只挂相关工具集" |

## A.2 不建议借鉴的（避免过度设计）

- **FUSE 挂载形态本身**：Linux-only，Windows 无对应，且我们已有 9100 面板 + 对话树 48920 两个可视面，再加文件系统面是重复
- **四角色 agent 树（architect/coder/worker/reviewer）**：我们已有 herdr 多路复用 + WorkBuddy 委派，角色已覆盖
- **bubblewrap 沙箱**：桌面伴侣场景无多租户隔离需求

## A.3 落地建议（挂进后续迭代）

1. **P2（低成本高价值）✅ 已落地（2026-08-10）**：检索/性能改动强制先跑基线再改——固化为一键回归工具：
   - 用法：`bin\retrieval-baseline.bat` → 跑 `bin/eval-longmemeval.py --limit 20`（top_k 10 / seed 42 为脚本默认）→ 结果存 `data\eval\longmemeval_baseline.json`
   - 数据：默认 `E:\Ikaros-something\reference project\longmemeval_s_cleaned.json`，可用环境变量 `LONGMEMEVAL_DATA` 覆盖（缺文件时脚本报错并 exit /b 1）
   - 验收：nDCG ∈ 0.3~0.9。2026-08-10 实测基线（n=20）：nDCG **0.748**，recall_any 0.950 / recall_all 0.750；分类型 multi-session 0.455 / temporal-reasoning 0.583 / single-session-user 0.908 / 其余 1.0
   - 2026-08-10 方案 A（中文语义召回）落地后重跑：nDCG **0.862**（+0.114），recall_any **1.000**（+0.05）/ recall_all **0.850**（+0.10）；multi-session 0.678 / temporal-reasoning 0.760——无回归且全面提升
4. **P2 ✅ 已落地（2026-08-10）**：中文语义召回兜底（方案 A）——`store.search_like`（LIKE 子串查询，绕开 FTS5 unicode61 整串分词）+ `count_like`（token 稀有度）+ `_keyword_fallback` 切 LIKE、稀有 token 优先、触发条件放宽到 `<top_k`；`preprocess_config.yaml` conversation type_boost 0.8→1.0。e2e 跨会话测试 5/5（`bin/cross-session-e2e-check.py`），pytest 281 全绿
2. **P3（中成本）✅ 已落地（2026-08-10）**：conversation-tree 会话导出 JSONL 镜像（借鉴 messages.jsonl 可审计思路）——`bin\export-convtree-jsonl.py`（支持 `--help`；每行一个消息：node_id / parent_id / role / content / created / session）→ `data\eval\convtree_export.jsonl`（2026-08-10 实测 9 节点 / 17 条记录，逐行 JSON 校验通过）
3. **P3（中成本，待拍板）**：MCP 工具按会话分组暴露（借鉴 tsh 单一工具路径的"少暴露"原则），减少每轮 API 调用的工具 schema 体积——调研完成，报告见 `docs/hermes-tools-scoping.md`（48 工具 7 分组表、三档方案与改动点、风险评估、决策门）；结论：值得做但当前仅 conversation-tree 一个消费方，收益边界窄，推荐先做 mcp_server.py 分组表（零 hermes 侵入），会话级分组待模式分化成为痛点再上

## A.4 备忘：CortexFS 自评数据（参考，非基准）

- 20/20 请求运行时成功；exact-match 准确率仅 20%（返回散文而非精确答案）
- p50 延迟 6.7s / p95 11.4s（含模型推理）；token 统计仅 1/20 可用
- 教训：**"能跑" 不等于 "答得准"**——我们评测里的 nDCG 0.31→0.87 过程同理，检索召回 ≠ 问答准确，QA 维度（HaluMem）仍需补
