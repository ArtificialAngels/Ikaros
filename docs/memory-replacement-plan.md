# 记忆系统替换方案：N.E.K.O → Ikaros V5

> **日期**: 2026-07-24
> **目标**: 将 N.E.K.O neko 的独立记忆系统（`memory_server.py` + `memory/` 模块）替换为 Ikaros V5 记忆系统（`core/v5/v5/store` + `search` + `reflect/`）
> **前置文档**: `neko-deep-analysis.md`（N.E.K.O 改造总纲 Phase 3）

> **⚠️ 2026-07-26 状态校正（本方案已落地，以下现状以 `docs/ARCHITECTURE.md` 第 5 章为准）**
> - 本地 LLM `:8080` 已改为**懒加载**：看门狗只监测端口，模型由 agent 首次调用时热载入，不再随 memory 组件启动。
> - 反思/蒸馏/摘要管线（`consolidate`/`distill`/`reflect`/`summary`）**全部走 DeepSeek 云端**（`deepseek-v4-flash`），本地 `:8080` 不参与认知任务。
> - 三路融合阈值 `min_fused_score` 线上 = **0.3**（yaml 标定，原 0.6 已下调，见 `core/v5/preprocess_config.yaml`）。
> - 新增结构化内容守卫 `validation.py`（`V5-0109`），在 `consolidate/distill/reflect` 落库前拦截 LLM 旁白/裸 JSON/栅栏/超长。
> - 实体图谱 `eg_*` 已启用（抽取 + 传播激活 + 整合），非"未启用"。

---

## 1. 两套记忆架构全景对比

### 1.1 V5 记忆架构（`core/v5/v5/`）

```
┌───────────────────────────────────────────────────────────────────────┐
│                         Ikaros V5 记忆系统                               │
├───────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────────────────────────────────────────────────┐         │
│  │  SQLite (v5.db) — 主存储                                   │         │
│  │  ├─ memory 表 (FTS5 + pad_p/a/d 情感指纹)               │         │
│  │  ├─ eg_entities / eg_aliases / eg_edges (实体图谱)      │         │
│  │  ├─ eg_episodic / eg_episodic_entities (情景记忆)        │         │
│  │  └─ eg_activations (扩散激活)                            │         │
│  ├──────────────────────────────────────────────────────────┤         │
│  │  ChromaDB (data/v5/chroma/) — 向量索引                    │         │
│  │  └─ ikaros_v5 集合 (cosine, HNSW)                       │         │
│  ├──────────────────────────────────────────────────────────┤         │
│  │  JSON (data/v5/) — 状态持久化                              │         │
│  │  └─ self_model.json, affect.json, relationship.json 等   │         │
│  ├──────────────────────────────────────────────────────────┤         │
│  │  检索管线                                                │         │
│  │  ├─ FTS5 全文 (store.search)                             │         │
│  │  ├─ 向量语义 (VectorIndex.search → :8587 nomic)          │         │
│  │  ├─ 实体图谱 (spreading_activation_search)               │         │
│  │  └─ 三路融合 (memory_retrieval.retrieve → 0.3fts+0.7vec) │         │
│  ├──────────────────────────────────────────────────────────┤         │
│  │  反思调度 (ReflectScheduler)                              │         │
│  │  ├─ consolidate (1h) — 对话→事实提取                     │         │
│  │  ├─ distill (24h) — 灵魂蒸馏                              │         │
│  │  ├─ reflect (3h) — 灵魂层反思                             │         │
│  │  ├─ promote (12h) — 短→长时晋升                           │         │
│  │  ├─ narrative (30d) — 自我叙事                             │         │
│  │  ├─ self_discovery (3h) — 自我认知探索                     │         │
│  │  ├─ vector_sync (24h) — 向量回填                           │         │
│  │  └─ cleanup (6h) — 自动清理                               │         │
│  └──────────────────────────────────────────────────────────┘         │
│                                                                       │
│  外部依赖: :8587 (nomic-embed-text), :8080 (Qwen3-1.7B), DeepSeek API  │
└───────────────────────────────────────────────────────────────────────┘
```

### 1.2 N.E.K.O 记忆架构（`core/neko/memory/` + `memory_server.py`）

```
┌───────────────────────────────────────────────────────────────────────┐
│                    N.E.K.O 记忆系统 (per-character)                      │
├───────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌──────────────────────────────────────────────────────────┐         │
│  │  SQLite (memory/{name}/time_indexed.db) — 对话历史        │         │
│  │  └─ time_indexed_original 表 (LangChain SQLChatHistory)  │         │
│  │  └─ facts_fts FTS5 虚拟表 (BM25 全文搜索)                │         │
│  ├──────────────────────────────────────────────────────────┤         │
│  │  JSON 文件 (memory/{name}/) — 每角色独立                   │         │
│  │  ├─ facts.json — Tier 1 事实 (sha256 id, embed base64)  │         │
│  │  ├─ reflections.json — Tier 2 反思 (状态机)              │         │
│  │  ├─ persona.json — Tier 3 人格 (含证据信号)              │         │
│  │  ├─ recent.json — 压缩对话历史                            │         │
│  │  ├─ settings.json / cursors.json / user_directives.json  │         │
│  │  └─ anti_repeat_corpus.json — BM25 反重复                │         │
│  ├──────────────────────────────────────────────────────────┤         │
│  │  ndjson 文件 — 事件溯源                                    │         │
│  │  ├─ outbox.ndjson — 后台任务队列                           │         │
│  │  ├─ events.ndjson — 事件日志 (15种事件类型)               │         │
│  │  └─ events_applied.json — 事件哨兵 (Reconciler)          │         │
│  ├──────────────────────────────────────────────────────────┤         │
│  │  存档分片                                                 │         │
│  │  ├─ reflections_archive/*.json — 每日反思分片             │         │
│  │  └─ persona_archive/*.json — 每日人格分片                 │         │
│  ├──────────────────────────────────────────────────────────┤         │
│  │  嵌入系统                                                 │         │
│  │  └─ EmbeddingService (本地 CPU ONNX, base64 fp16)        │         │
│  │  └─ embedding_worker (后台预热 + 批量回填)                │         │
│  ├──────────────────────────────────────────────────────────┤         │
│  │  检索管线                                                 │         │
│  │  ├─ BM25 (facts_fts FTS5, Okapi BM25)                   │         │
│  │  ├─ Cosine (EmbeddingService, 本地ONNX)                  │         │
│  │  └─ RRF 融合 (k=60) — hybrid_recall                     │         │
│  ├──────────────────────────────────────────────────────────┤         │
│  │  证据系统                                                 │         │
│  │  ├─ evidence_score = effective_reinf - effective_disp     │         │
│  │  ├─ 半衰期衰减 (强化/反驳各有独立半衰期)                  │         │
│  │  └─ 状态机: pending→confirmed→promoted→merged→persona   │         │
│  └──────────────────────────────────────────────────────────┘         │
│                                                                       │
│  外部依赖: ONNX 运行时 (本地CPU), LLM (通过 main_server 共享)          │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 2. 逐模块替换映射

### 2.1 存储层替换

| N.E.K.O 存储 | V5 等价 | 替换策略 | 复杂度 |
|-------------|---------|---------|--------|
| `time_indexed.db` (per-char SQLite) | V5 `v5.db` (全局 SQLite) | **合并**: 在 V5 `memory` 表加 `character` 列隔离角色数据 | ⭐⭐ |
| `facts.json` (per-char JSON) | V5 `memory` 表 `type='fact'` | **迁移**: JSON → SQLite INSERT，加 `character` 字段 | ⭐ |
| `reflections.json` (per-char JSON) | V5 无直接等价 | **新增**: V5 `reflections` 表 (见 §4.1) | ⭐⭐⭐ |
| `persona.json` (per-char JSON) | V5 `self_model.json` | **融合**: neko persona → V5 self_model，加 `character` 支持 | ⭐⭐ |
| `recent.json` (压缩历史) | V5 `cloud_chat.build_system_prompt()` | **替代**: V5 运行时动态构建，无需持久化压缩 | ⭐ |
| `anti_repeat_corpus.json` | V5 无 | **新增**: 迁移到 V5 store (见 §4.2) | ⭐⭐ |
| `user_directives.json` | V5 无 | **新增**: V5 `user_directives` 表 (见 §4.3) | ⭐ |
| `settings.json` / `cursors.json` | V5 无 | **直接删除**: V5 配置集中于 `config/` | ⭐ |
| `outbox.ndjson` / `events.ndjson` | V5 无 | **保留**: 事件溯源是 V5 缺失、应该引入的特性 (见 §4.4) | — |

### 2.2 功能模块替换

| N.E.K.O 功能 | V5 等价 | 替换策略 | 复杂度 |
|-------------|---------|---------|--------|
| **FactStore** (extract_facts) | V5 `reflect/consolidate.consolidate_conversations()` | **替换**: V5 的 LLM 提取 + LLM 验证 > neko 的单 LLM 提取 | ⭐ |
| **ReflectionEngine** (状态机综合) | V5 `reflect/distill.reflect()` + `registry` | **替换**: V5 的 3h 反思 + 24h 蒸馏 + 12h 晋升 > neko 的 evidence 驱动 | ⭐⭐ |
| **PersonaManager** (渲染/抑制) | V5 `cloud_chat` 动态 system prompt | **替代**: V5 运行时实时构建，不含抑制/过时逻辑（需新增） | ⭐⭐ |
| **EmbeddingService** (本地 ONNX) | V5 `:8587 nomic-embed-text` | **替代**: 统一走外部嵌入服务，去掉本地 ONNX 包袱 | ⭐ |
| **hybrid_recall** (BM25+Cos+RRF) | V5 `memory_retrieval.retrieve()` (FTS5+Chroma+时间) | **替换**: V5 三路融合 > neko 双路+RRF | ⭐ |
| **MemoryRecallReranker** (向量+LLM 重排) | V5 无 | **可保留**: V5 没有 LLM 重排；可作为可选增强 | ⭐⭐⭐ |
| **anti_repeat** (BM25 反重复) | V5 无 | **新增**: 迁移到 V5 store，保持 BM25 检测逻辑 | ⭐⭐ |
| **user_directives** (用户指令) | V5 无 | **新增**: 迁移到 V5 (见 §4.3) | ⭐ |
| **CompressedRecentHistory** (压缩) | V5 `cloud_chat` + metacog | **替代**: V5 运行时无需压缩历史 | ⭐ |
| **ImportantSettingsManager** | V5 配置体系 | **删除**: 不再需要 | ⭐ |
| **IdleMaintenanceLoop** | V5 `ReflectScheduler.run_all()` | **替换**: V5 调度器更完善 | ⭐⭐ |
| **EventLog + Reconciler** | V5 无 | **保留引入**: V5 缺乏的崩溃恢复机制 (见 §4.4) | ⭐⭐⭐ |
| **FactDedupResolver** (LLM 去重) | V5 无 | **可引入**: V5 `fact_dedup.py` 已存但未接入；可复用 | ⭐⭐ |
| **MemoryRefineEngine** (余弦聚类) | V5 无 | **不引入**: V5 的 consolidate+distill 管线 > 余弦聚类 | ⭐⭐ |

### 2.3 服务端替换

| N.E.K.O 服务 | V5 等价 | 替换策略 | 复杂度 |
|-------------|---------|---------|--------|
| `memory_server.py` HTTP 端点 (20+) | V5 `mcp_server.py` MCP 工具 (23) + 需新增 HTTP 层 | **代理**: `memory_server.py` 降级为 V5 的 HTTP 代理 | ⭐⭐⭐ |
| 后台循环 (idle/signal/rebuttal/archive/promote/event) | V5 `ReflectScheduler.run_all()` | **替换**: V5 调度器 (9 ops, 独立间隔) > neko 6 个松散循环 | ⭐⭐ |

---

## 3. API 端点映射

### 3.1 `memory_server.py` 端点 → V5 代理

| N.E.K.O 端点 | 代理到 V5 | 备注 |
|-------------|-----------|------|
| `POST /cache/{name}` | `v5.store()` + `v5.affect.maybe_record_emotion()` | 写入对话+情感变化 |
| `POST /process/{name}` | `v5.store()` + 触发 `consolidate()` | 批量处理+异步反射 |
| `POST /settle/{name}` | `v5.store()` + 压缩旗语 | 结算缓存 |
| `GET /get_recent_history/{name}` | `v5.search()` + `cloud_chat` 动态构建 | 无需持久化压缩 |
| `POST /query_memory/{name}` | `v5.memory_retrieval.retrieve()` | 三路融合检索 |
| `GET /get_persona/{name}` | `v5.cloud_chat.build_system_prompt()` | 运行时实时渲染 |
| `POST /reflect/{name}` | `v5.ReflectScheduler.run_all(force=True)` | 触发即时反思 |
| `GET /followup_topics/{name}` | `v5.metacog` / `v5.think` | 后续话题候选 |
| `GET /new_dialog/{name}` | `v5.cloud_chat.build_system_prompt()` | 构建初始提示 |
| `GET /api/memory/ikaros/search` | `v5.memory_retrieval.retrieve()` | 跨角色搜索（V5 新增 `character`） |
| `GET /api/memory/ikaros/stats` | `v5.store.stats()` + `v5.entity_graph_stats()` | 统一统计 |
| `GET /api/memory/ikaros/browser` | 组合查询 | 浏览器 UI 数据源 |

### 3.2 保留/新增的 V5 层

| 需求 | 方案 |
|------|------|
| **角色隔离** | V5 `memory` 表新增 `character TEXT NOT NULL DEFAULT ''` 列 |
| **事件溯源** | 迁移 `memory/{name}/events.ndjson` 到 V5 统一的 `events` 表 |
| **反重复** | V5 新增 `anti_repeat_corpus` 表 |
| **用户指令** | V5 新增 `user_directives` 表 |
| **后台维护** | 全部走 `ReflectScheduler`，不再用松散后台循环 |

---

## 4. 需在 V5 中新增的模块

### 4.1 `v5/reflections.py` — 反思管理

从 neko 的 `ReflectionEngine` 迁移状态机逻辑：

```python
# 反思状态机
class ReflectionStatus(Enum):
    PENDING = "pending"           # 刚由事实综合产生
    CONFIRMED = "confirmed"       # 有证据支持
    PROMOTED = "promoted"         # 升格为类人格特征
    MERGED = "merged"             # 已融合进 self_model
    DENIED = "denied"             # 被反驳
    ARCHIVED = "archived"         # 过期归档

@dataclass
class Reflection:
    id: str                    # sha256
    character: str             # 角色
    content: str               # 反思文本
    entity: str                # master/neko/relationship
    relation_type: str         # preference/habit/identity/opinion/experience/relationship_dynamic
    temporal_scope: str        # pattern/state/episode/past
    status: ReflectionStatus
    importance: int            # 1-10
    source_fact_ids: list[str]  # 来源事实 ID 列表
    reinforcement: float       # 证据强化值
    disputation: float         # 证据反驳值
    created_at: float
    confirmed_at: float | None
```

### 4.2 `v5/anti_repeat.py` — 反重复检测

从 neko `AntiRepeatCorpus` 迁移 BM25 反重复逻辑：

- V5 的 `store()` 写入时同步更新滚动语料库
- 在 `cloud_chat` 生成回复前检查 BM25 相似度
- 超过阈值 → 注入"避免重复"提示词或重生成

### 4.3 `v5/user_directives.py` — 用户指令

- SQLite `user_directives` 表: `(id, character, directive_text, created_at, expires_at)`
- N.E.K.O 原有 3 天 TTL 逻辑保留
- 在 `cloud_chat` system prompt 组装时注入

### 4.4 `v5/event_log.py` — 事件溯源

从 neko `EventLog` + `Reconciler` 迁移：

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,      -- 15 种类型
    payload TEXT NOT NULL,         -- JSON 负载
    created_at REAL NOT NULL,
    applied INTEGER NOT NULL DEFAULT 0
);
```

- 写入 V5 state 文件时同步写 events 表
- `Reconciler` 在 V5 初始化时重放未应用事件
- 实现"写 state → 写 events → events_applied"三步确认

### 4.5 角色隔离 — `character` 列

所有 V5 存储表新增 `character` 列：

```sql
ALTER TABLE memory ADD COLUMN character TEXT NOT NULL DEFAULT '';
ALTER TABLE eg_entities ADD COLUMN character TEXT NOT NULL DEFAULT '';
ALTER TABLE eg_edges ADD COLUMN character TEXT NOT NULL DEFAULT '';
ALTER TABLE eg_episodic ADD COLUMN character TEXT NOT NULL DEFAULT '';
```

`store()` / `search()` 等所有 API 增加 `character` 参数，SQL 查询增加 `WHERE character=?` 过滤。

将 N.E.K.O 的 per-character JSON 文件 (`memory/{name}/facts.json` 等) 全部迁移到 V5 的共用 SQLite。

---

## 5. 数据迁移方案

### 5.1 迁移顺序

```
Phase A: Schema 扩展 + 空表创建
  ├─ V5 v5.db: 新增 character 列
  ├─ V5: 新增 reflections 表
  ├─ V5: 新增 anti_repeat_corpus 表
  ├─ V5: 新增 user_directives 表
  └─ V5: 新增 events 表

Phase B: 数据迁移 (一次性脚本)
  ├─ neko facts.json → V5 memory INSERT (type='fact')
  ├─ neko reflections.json → V5 reflections 表
  ├─ neko persona.json → V5 self_model (character 键)
  ├─ neko user_directives.json → V5 user_directives 表
  └─ neko events.ndjson → V5 events 表

Phase C: 嵌入迁移
  ├─ neko base64 fp16 → V5 ChromaDB upsert
  └─ neko EmbeddingService → V5 :8587 nomic (停用本地ONNX)

Phase D: 服务替换
  ├─ memory_server.py → V5 HTTP 代理层
  ├─ 后台循环 → ReflectScheduler
  └─ 旧 file lock → V5 json_lock
```

### 5.2 迁移脚本示例

```python
# bin/migrate-neko-memory-to-v5.py
# 用法: python bin/migrate-neko-memory-to-v5.py [character_name]

def migrate_facts(character: str):
    """迁移 neko facts.json → V5 store"""
    neko_facts = json.loads(
        (NEKO_MEMORY_DIR / character / "facts.json").read_text()
    )
    for fact in neko_facts:
        store.store(
            content=fact["content"],
            type="fact",
            weight=fact.get("importance", 5) / 10,
            tags=f"character:{character},migrated:neko",
            pad_p=0.0, pad_a=0.0, pad_d=0.0,
            character=character,  # 新增参数
        )

def migrate_reflections(character: str):
    """迁移 neko reflections.json → V5 reflections 表"""
    # ...

def migrate_persona(character: str):
    """迁移 neko persona.json → V5 self_model"""
    # ...
```

---

## 6. `memory_server.py` 降级方案

### 6.1 代理层架构

```
┌───────────────────────────┐
│  N.E.K.O 前端/主服务       │
│  (现有代码, 无需修改)       │
└─────────┬─────────────────┘
          │ HTTP (现有 API)
          ▼
┌───────────────────────────┐
│  memory_server.py         │  ← 保留但大幅精简
│  (V5 代理层)               │  ← 仅做请求转发
│                           │
│  原 20+ 端点 → V5 API 调用  │
│  原 6 后台循环 → 删除       │
│  原 per-character SQLite → 删除
│  原 EmbeddingService → 删除
└─────────┬─────────────────┘
          │ Python import
          ▼
┌───────────────────────────┐
│  V5 Memory Core           │
│  (v5/store + search +     │
│   reflect + entity_graph) │
└───────────────────────────┘
```

### 6.2 精简后的 memory_server.py

```python
# memory_server.py (V5 代理版)
# 原来 226KB → 目标 ~30KB

from v5.store import store, search, stats as v5_stats
from v5.search import fused_search
from v5.memory_retrieval import retrieve
from v5.reflect.registry import make_default_scheduler
from v5.entity_graph import entity_graph_stats

# 每个端点直接调 V5 API，不做缓存/文件管理
# 不再启动后台循环
# 不再管理 per-character SQLite 文件
# 不再管理 EmbeddingService
```

### 6.3 生命周期变化

| 事件 | 当前行为 | 替换后行为 |
|------|---------|-----------|
| 对话写入 | `POST /cache` → fact/reflection/persona 管线 | `POST /cache` → `v5.store()` → `v5.affect()` |
| 检索 | FTS5 + ONNX 余弦 + RRF | `v5.memory_retrieval.retrieve()` (FTS5+Chroma+时间) |
| 反思 | 6 个松散后台循环 | `ReflectScheduler.run_all()` (9 ops, 统一调度) |
| 嵌入 | 本地 CPU ONNX | 外部 `:8587` nomic-embed-text |
| 崩溃恢复 | EventLog + Reconciler | V5 events 表 + Reconciler |
| 人物画像 | persona.json (3 层证据) | `self_model.json` + 运行时 prompt 构建 |
| 启动 | 启动 6 个后台任务 | 启动 ReflectScheduler (1 个) |

---

## 7. 保留的 N.E.K.O 记忆特性

以下 neko 特有功能经过评估，**应迁移到 V5 而非丢弃**：

| 特性 | 保留理由 | 迁移位置 |
|------|---------|---------|
| **事件溯源 + Reconciler** | V5 目前写 state 文件无崩溃恢复；events 表 + 重放机制能解决 `json_lock` 场景下的部分问题 | `v5/event_log.py` |
| **反重复 (AntiRepeatCorpus)** | V5 目前生成回复时无重复检测；BM25 反重复防止 AI 车轱辘话 | `v5/anti_repeat.py` |
| **用户指令 (UserDirectives)** | V5 目前无用户禁止话题机制 | `v5/user_directives.py` |
| **证据评分 (半衰期衰减)** | 比 V5 的 `weight + access_count` 更精细化地建模"记忆随时间淡化" | `v5/evidence.py` |
| **Per-character 隔离** | V5 目前全局单 DB；多角色场景需要角色级隔离 | 加 `character` 列 |
| **定时存档分片** | 防止单文件无限增长 | 复用 `cleanup` op |

### 7.1 不引入的特性

| N.E.K.O 特性 | 不引入理由 |
|-------------|-----------|
| **MemoryRefineEngine (余弦聚类+LLM精炼)** | V5 的 consolidate+distill 管线覆盖了同样功能，且更稳定 (三级提取回退) |
| **MemoryRecallReranker (LLM 重排)** | 性能开销大；V5 的三路融合+时间衰减+类型 boost 已在精度和速度间取得平衡 |
| **ImportantSettingsManager** | 写作 `config/` + 证据系统已覆盖 |
| **CompressedRecentHistory** | V5 的 system prompt 动态构建无需持久化压缩历史 |
| **Tier 3 Persona (抑制/过时/存档)** | V5 self_model + `cloud_chat` 动态构建更灵活；抑制逻辑不迁移 |

---

## 8. 改造阶段与里程碑

### Phase 1: Schema 扩展 + 数据迁移 (1-2 周)

- [ ] V5 `memory` 表加 `character` 列
- [ ] V5 新增 `reflections`、`anti_repeat_corpus`、`user_directives`、`events` 表
- [ ] 编写 `bin/migrate-neko-memory-to-v5.py`
- [ ] 执行迁移，做双向验证（对比 V5 vs neko 查询结果）

### Phase 2: 核心功能代理 (2-3 周)

- [ ] 实现 `v5/event_log.py` (事件溯源)
- [ ] 实现 `v5/anti_repeat.py` (反重复)
- [ ] 实现 `v5/user_directives.py` (用户指令)
- [ ] `v5/store.py` 加 `character` 参数
- [ ] `v5/search.py` etc. 加 `character` 过滤
- [ ] memory_server.py 端点逐个替换为 V5 代理

### Phase 3: 服务精简 + 后台调度统一 (1-2 周)

- [ ] 删除 memory_server.py 的所有后台循环
- [ ] 接入 V5 ReflectScheduler 统一管理
- [ ] 删除 EmbeddingService (停用本地 ONNX)
- [ ] 删除 per-character SQLite / JSON 文件管理
- [ ] memory_server.py 从 226KB 精简到 30KB
- [ ] 完善 V5 `call_llm` 流式支持（对接 neko TTS/WS 管线）

### Phase 4: 验证 + 回退方案 (1 周)

- [ ] 功能一致性测试（每种 neko 响应 vs V5 代理响应）
- [ ] 性能对比（检索延迟、写入延迟、反射延迟）
- [ ] 回退方案：若 V5 不可用，`memory_server.py` 能快速切回自主模式
- [ ] 删除 `core/neko/memory/` 中已迁移的模块（保留 `memory/` 仅作为旧数据备份）

---

## 9. 技术风险与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| **`character` 列兼容性** | 🟡 | V5 现有数据 character='' 兼容旧查询；渐进式迁移 |
| **Embedding 模型差异** | 🟡 | neko 本地 ONNX vs V5 nomic:8587；迁移后需 QA 对比 top-5 检索结果 |
| **Reflection 状态机差异** | 🟡 | neko 的 evidence 驱动 vs V5 的调度驱动；过渡期两套并行运行 |
| **启动依赖** | 🟡 | memory_server 不再依赖 ONNX 模型加载 (减少 30s 冷启动延迟) |
| **崩溃恢复缺失** | 🟡 | V5 新增 events 表 + Reconciler 前，过渡期保留 neko EventLog |
| **反重复缺位** | 🟡 | V5 anti_repeat 实现前，neko AntiRepeatCorpus 保持独立运行 |

---

*本方案是 `neko-deep-analysis.md` 中定义的 Phase 3 (记忆融合) 的详细执行计划。*
