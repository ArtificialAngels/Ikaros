# Phase 2 P2 — Payload Schema Migration (mem0-compatible)

**Date:** 2026-07-02
**Author:** 伊卡洛斯 (bridge-rs P2)
**Reviewer:** 待 哥哥 review

## 摘要

P2 在 P0 (DNA Memory 借鉴字段) 之上, **纯加性** 地新增 8 个 mem0 兼容字段。Qdrant collection 不重建, 旧 point 完全无感。所有字段 Optional + 缺省兜底。

## Schema 演进对照表

| schema_version | 字段 | 来源 | 备注 |
|---|---|---|---|
| **v1 (P0 ship)** | `text`, `user_id`, `agent_id`, `created_at`, `last_accessed`, `importance` (0-1), `halflife_days`, `mem_type`, `tags` (comma-string), `source` | DNA Memory + Phase 2 P0 | 旧数据 |
| **v2 (P2 ship)** | + `source_kind`, `session_id`, `importance_5` (1-5), `ttl_seconds`, `ttl_expires_at_iso`, `tags_vec`, `created_at_iso`, `last_accessed_at_iso`, `related_memories`, `schema_version` | mem0 兼容 + 跨链 | 新写入 |

## 新字段语义

| 字段 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `source_kind` | string enum | `"unknown"` | `chat` / `voice` / `kanban` / `webui` / `cli` / `unknown`. 跟 `source` 区分: `source` 是写入者 (auto_collector), `source_kind` 是用户接触面 |
| `session_id` | string | `""` | 跨渠道同 session 时填 (例: voice 跟 webui 共享 session 时) |
| `importance_5` | u8 (1-5) | `3` | UI 用的 star rating, 跟 `importance` (0-1, DNA Memory) 共存. UI 显示 ★, 计算仍用 0-1 |
| `ttl_seconds` | u64 (可选) | `null` = 永不过期 | 硬过期时间, 跟 `halflife_days` 软衰减并存. UI 显示 "X 天后过期" |
| `ttl_expires_at_iso` | string | `null` | 预计算 ISO 8601 字符串, = `created_at_iso + ttl_seconds`. 写入时算好, 读时不用算 |
| `tags_vec` | array\<string\> | `[]` | 跟 `tags` (comma-string) 共存. P3 迁移工具会把 `tags` 解析成 vec 写回 |
| `created_at_iso` | string | = `created_at` | alias, 统一前端字段名 |
| `last_accessed_at_iso` | string | = `last_accessed` | alias, 统一前端字段名 |
| `related_memories` | array\<string\> | `[]` | 关联 point id 列表. Phase 3 GraphRAG 雏形 |
| `schema_version` | u8 | `2` | 迁移工具识别用. v1 point = 老字段; v2 point = 含 P2 字段 |

## Qdrant PUT 格式 (P2 写入)

PUT `/collections/mem0/points?wait=true`

```json
{
  "ids": ["<uuid>"],
  "points": [{
    "id": "<uuid>",
    "vector": [/* 768 floats */],
    "payload": {
      "text": "...",
      "user_id": "hermes-user",
      "agent_id": "alpha",
      "created_at": "2026-07-02T...",
      "last_accessed": "2026-07-02T...",
      "importance": 0.85,
      "halflife_days": 30,
      "mem_type": "preference",
      "tags": "pref,ui",
      "source": "auto_collector",
      "source_kind": "chat",
      "session_id": "pet_1751472000000_1234",
      "importance_5": 4,
      "ttl_seconds": 2592000,
      "ttl_expires_at_iso": "2026-08-01T...",
      "tags_vec": ["pref", "ui"],
      "created_at_iso": "2026-07-02T...",
      "last_accessed_at_iso": "2026-07-02T...",
      "related_memories": ["uuid-1", "uuid-2"],
      "schema_version": 2
    }
  }]
}
```

## Qdrant 1.14 兼容性

- ✅ 所有字段为 Qdrant 支持的 scalar 类型 (`string` / `integer` / `float` / `boolean`)
- ⚠️ `tags_vec` / `related_memories` 是 list 类型, Qdrant 1.14 **不支持** payload index (应用层 filter)
- ✅ `schema_version` 是 integer, 可建 index (P3 启用)
- ✅ `source_kind` 是 string, 可建 index (P3 启用)
- ✅ `importance_5` 是 integer, 可建 index (P3 启用)
- 旧 payload 完全无需迁移, v1 → v2 读时缺字段自动 None 兜底

## Chroma 状态

**未启用 Chroma**。bridge-rs 当前只用 Qdrant :6333 (Phase 1 P0 ship)。

如未来启用 Chroma, 对应 schema 等价物:

| Qdrant payload 字段 | Chroma metadata 字段 |
|---|---|
| `text` | `document` |
| `user_id`, `agent_id` | `user_id`, `agent_id` |
| `created_at` / `created_at_iso` | `created_at` |
| `source_kind` / `session_id` | `source_kind` / `session_id` |
| `importance` / `importance_5` | `importance` (保留 0-1) / `importance_5` |
| `ttl_seconds` / `ttl_expires_at_iso` | `ttl_seconds` / `ttl_expires_at_iso` |
| `tags_vec` / `tags` | `tags` (Chroma 支持 list metadata) |
| `related_memories` | `related_memories` (Chroma 支持 list) |
| `schema_version` | `schema_version` |

Chroma `metadata` 支持 list / scalar, 索引能力等价 (无 native list index, 应用层 filter).

## 兼容性矩阵

| 场景 | 旧 client (无 P2 字段) | 新 client (P2 字段) |
|---|---|---|
| 写入 v1 (P0) | ✅ 已有数据 | ✅ AddRequest P2 字段全 Optional, 缺省兜底 |
| 写入 v2 (P2) | ❌ 不可能 (旧 client 无 P2 字段) | ✅ 新 schema, `schema_version=2` |
| 读取 v1 (P0) | ✅ 旧 reader | ✅ SearchResult P2 字段全 `Option<...>`, 旧 point 缺字段 = None |
| 读取 v2 (P2) | ❌ 旧 reader 看不到 P2 字段 | ✅ 新 reader 全字段可见 |

## 验证

- `tests/test_phase1_p2_metadata.py` — 12 个 pytest, 覆盖 P0 + P2 所有新字段
- ad-hoc: `curl http://127.0.0.1:6333/collections/mem0/points/scroll -d '{"limit":5,"with_payload":true}'`
- 完整 schema migration 端到端验证: write → read → 字段一致

## Migration Path (Phase 3 准备)

P3 启用 indexed fields 时的回填脚本 (草案, 不在 P2 ship):

```python
# 草案 — P3 ship 前再写
# 1. scroll 全部 v1 point
# 2. compute ttl_expires_at_iso from created_at + ttl (None if no ttl)
# 3. set_payload 把 v2 字段写回去, schema_version=2
# 4. 创建 payload index: source_kind, mem_type, importance_5, schema_version
# 5. 删 alias (created_at / last_accessed) — 待 verify 所有 consumer 都迁完
```

**P2 期间不做回填**。理由: v2 是纯 additive, v1 数据读时自动 None 兜底, 行为不变。

## Blast Radius

| 类别 | 文件 | 改动 |
|---|---|---|
| Core service | `bridge-rs/src/memory.rs` | + AddRequest 9 fields, + SearchResult 9 fields, + add_memory 写新 payload, + search/scroll 读新字段, + `default_source_kind` helper, + `VALID_SOURCE_KINDS` const |
| Writer | `bridge-rs/src/memory_writer.rs` | + 8 个 P2 字段显式 None (除 source_kind="chat") |
| Docs | `docs/p2-payload-schema-migration.md` | NEW |
| Tests | `tests/test_phase1_p2_metadata.py` | NEW |

**0 process restart** (P2 不需要 binary 重启 — 验证脚本是独立 Python).
**0 main.rs change** (5 步协议 v2 守约).
**0 modules/llm_engine/ touch**.