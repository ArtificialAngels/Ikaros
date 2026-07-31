# N.E.K.O memory_server.py → V5 Proxy 设计方案

> **目标**: 将 `core/neko/app/memory_server.py` 从 226KB 完整服务精简为约 30KB 的
>   V5 HTTP 代理层。
> **前置条件**: V5 已增强（见 `core/v5/v5/store.py` + `reflections.py` + 
>   `anti_repeat.py` + `user_directives.py`），neko 数据已迁移。

---

## 1. 改造前后对比

| 维度 | 当前 (226KB) | 改造后 (~30KB) |
|------|-------------|----------------|
| 存储 | per-character SQLite + JSON + ndjson | 统一 V5 v5.db |
| 嵌入 | 本地 CPU ONNX (EmbeddingService) | 外部 :8587 nomic (V5 共享) |
| 后台循环 | 6 个 (idle/signal/rebuttal/archive/promote/event) | 0 个 (V5 ReflectScheduler 接管) |
| 事实提取 | FactStore (facts.py, 72KB) | V5 reflect/consolidate (共享) |
| 反思综合 | ReflectionEngine (reflection.py, 174KB) | V5 reflections.py + reflect/distill |
| 人格渲染 | PersonaManager (persona.py, 146KB) | V5 cloud_chat runtime |
| 端点 | 20+ | 保持 API 兼容，内部调 V5 |
| 事件溯源 | EventLog + Reconciler | 保留 (精简版) |

## 2. 代理层架构

```
N.E.K.O 前端/主服务                              外部系统
    │                                                │
    │ HTTP (现有 API)                                  │
    ▼                                                │
┌────────────────────────────────────────┐            │
│  memory_server.py (V5 代理版)          │            │
│                                        │            │
│  /cache/{name}    → v5.store()         │            │
│  /process/{name}  → v5.store()         │            │
│  /settle/{name}   → v5.store()         │            │
│  /query_memory    → v5.memory_retrieval│            │
│  /new_dialog      → cloud_chat 构建     │            │
│  /get_persona     → cloud_chat prompt   │            │
│  /reflect         → ReflectScheduler    │            │
│  ...                ...                 │            │
│                                        │            │
│  事件日志 (精简 EventLog)                │            │
│  → events 表 (V5 v5.db)                 │            │
└──────────────┬─────────────────────────┘            │
               │ Python import                        │
               ▼                                       │
┌────────────────────────────────────────┐            │
│  V5 Memory Core (core/v5/v5/)          │            │
│  store / search / memory_retrieval      │            │
│  reflections / anti_repeat / directives │            │
│  reflect/ (scheduler + consolidate)    │            │
└────────────────────────────────────────┘            │
               │                                       │
               ▼                                       │
┌────────────────────────────────────────┐            │
│  :8587 nomic-embed-text                 │            │
│  :8080 Qwen3-1.7B                       │            │
│  DeepSeek Cloud API                     │            │
└────────────────────────────────────────┘            │
```

## 3. 端点映射表

### 3.1 写入类端点

| 原始端点 | V5 代理实现 | 备注 |
|---------|-------------|------|
| `POST /cache/{name}` | `v5.store(content, type="conversation", character=name)` | 写入对话记录 |
| `POST /process/{name}` | `v5.store()` + 标记需 consolidate | 批量处理 |
| `POST /settle/{name}` | `v5.store()` + 完成标记 | 结算缓存 |
| `POST /record_surfaced/{name}` | `v5.reflections.apply_evidence()` | 记录提及 |

### 3.2 读取类端点

| 原始端点 | V5 代理实现 | 备注 |
|---------|-------------|------|
| `GET /get_recent_history/{name}` | `v5.search(character=name, type="conversation", top_k=20)` | 最近对话 |
| `POST /query_memory/{name}` | `v5.memory_retrieval.retrieve(query, character=name)` | 混合检索 |
| `GET /get_persona/{name}` | 调用 `cloud_chat.build_system_prompt()` + `reflections.build_reflections_block()` | 人格+反思 |
| `GET /followup_topics/{name}` | `v5.memory_retrieval.retrieve("", character=name)` | 后续话题 |
| `GET /new_dialog/{name}` | 组合: 人格+反思+最近记忆 | 初始提示构建 |
| `GET /last_conversation_gap/{name}` | `v5.store.search_by_time_range()` | 时间间隔 |

### 3.3 管理类端点

| 原始端点 | V5 代理实现 | 备注 |
|---------|-------------|------|
| `POST /reflect/{name}` | `v5.reflect.registry.make_default_scheduler().run_all(force=True)` | 强制反思 |
| `GET /api/memory/ikaros/search` | `v5.memory_retrieval.retrieve(query, character=?)` | 跨角色搜索 |
| `GET /api/memory/ikaros/stats` | `v5.store.stats()` + `v5.reflections.stats()` | 统一统计 |
| `GET /api/memory/ikaros/browser` | 组合查询 | 浏览器 UI |
| `GET /api/memory/funnel/{name}` | `v5.memory_retrieval.retrieve()` 统计 | 漏斗分析 |
| `GET /api/memory/recent_files` | 不再需要 (V5 统一存储) | 返回空列表 |

### 3.4 删除的端点

以下端点不再需要 (V5 统一管理)：

| 端点 | 替代方案 |
|------|---------|
| `GET /api/memory/recent_file` | 直接 `v5.search(character=name, type="conversation")` |
| `POST /api/memory/recent_file/save` | `v5.store()` 直接写入 |
| `POST /api/memory/update_catgirl_name` | 通过 `character` 列过滤，无需改名 |
| `GET /api/memory/review_config` | V5 ReflectScheduler 接管 |
| `POST /api/memory/review_config` | V5 ReflectScheduler 接管 |
| `GET /api/memory/powerful_memory_config` | V5 证据系统接管 |
| `POST /api/memory/powerful_memory_config` | V5 证据系统接管 |
| `GET /api/memory/legacy/scan` | 不再需要 |
| `POST /api/memory/legacy/purge` | 不再需要 |
| `POST /cancel_correction/{name}` | V5 反思自动处理 |
| `GET /settings/{name}` | V5 配置体系接管 |
| `POST /release_character/{name}` | V5 不再有 per-character 文件 |

## 4. 精简后的 memory_server.py 骨架

```python
"""V5 Proxy: N.E.K.O memory server backed by Ikaros V5 store."""

from fastapi import FastAPI, APIRouter
from v5 import store, memory_retrieval, reflections, anti_repeat

app = FastAPI(title="Ikaros V5 Memory Proxy", version="5.2.0")

@app.post("/cache/{name}")
async def cache_conversation(name: str, payload: dict):
    """Cache a conversation turn → V5 store."""
    content = payload.get("content", "")
    if not content:
        return {"status": "skipped"}
    mid = store.store(content, type="conversation", character=name,
                      pad_p=payload.get("pad_p", 0.0),
                      pad_a=payload.get("pad_a", 0.0),
                      pad_d=payload.get("pad_d", 0.0))
    # Record anti-repeat
    anti_repeat.record_response(name, content)
    return {"status": "ok", "memory_id": mid}

@app.post("/query_memory/{name}")
async def query_memory(name: str, payload: dict):
    """Query memories → V5 fused retrieval."""
    query = payload.get("query", "")
    if not query:
        return {"results": []}
    results = memory_retrieval.retrieve(
        query, character=name,
        top_k=payload.get("top_k", 5),
    )
    return {"results": results}

@app.post("/reflect/{name}")
async def force_reflect(name: str):
    """Force reflection → V5 scheduler."""
    from v5.reflect.registry import make_default_scheduler
    scheduler = make_default_scheduler()
    results = scheduler.run_all(force=True, continue_on_error=True)
    return {"status": "ok", "ops": results}

@app.get("/new_dialog/{name}")
async def build_new_dialog(name: str):
    """Build initial dialog context → V5 memory."""
    from v5 import cloud_chat
    recent = store.list_all(limit=20, character=name, type_filter="conversation")
    refl_block = reflections.build_reflections_block(name)
    prompt = cloud_chat.build_system_prompt(character=name)
    return {
        "prompt": prompt,
        "recent_history": [r.content for r in recent],
        "reflections": refl_block,
    }
```

## 5. 移除清单 (memory_server.py 中可删的部分)

| 文件/功能 | 规模 | 说明 |
|----------|------|------|
| EmbeddingService | ~82KB | 由 :8587 nomic 替代 |
| EmbeddingWarmupWorker | ~22KB | 不再需要 |
| FactStore | ~72KB | 由 V5 store + consolidate 替代 |
| ReflectionEngine | ~174KB | 由 V5 reflections.py + reflect/distill 替代 |
| PersonaManager | ~146KB | 由 V5 self_model + cloud_chat 替代 |
| CompressedRecentHistoryManager | ~63KB | 由 V5 search 替代 |
| MemoryRefineEngine | ~21KB | 由 V5 consolidate+distill 替代 |
| FactDedupResolver | ~36KB | 由 V5 consolidate 三级提取替代 |
| MemoryRecallReranker | ~33KB | 由 V5 三路融合替代 |
| AntiRepeatCorpus | ~21KB | 迁移到 V5 anti_repeat.py |
| UserDirectivesManager | ~18KB | 迁移到 V5 user_directives.py |
| EventLog + Reconciler | ~28KB | 精简后保留为 V5 events 表 |
| 6 个后台循环 | 大量 | V5 ReflectScheduler 接管 |
| Per-character SQLite | N/A | V5 统一 v5.db |
| 存档分片机制 | N/A | V5 cleanup op 接管 |
| 3 个后台循环 (idle/signal/rebuttal) | 大量 | V5 scheduler + store 接管 |

**总计移除**: 约 700KB+ 代码，~5 个模块，6 个后台线程。

## 6. 替换接入步骤

### Phase A: V5 增强部署 (1-2 天)
1. 确认 `v5.db` schema 已扩展 (character 列 + 4 张新表)
2. 验证新模块导入无错误: `python -c "from v5 import reflections, anti_repeat, user_directives"`
3. 运行迁移脚本: `python bin/migrate-neko-to-v5.py`

### Phase B: memory_server.py 代理化 (2-3 天)
1. 逐个替换端点: 从最简单的 `/cache` 开始，到最复杂的 `/new_dialog`
2. 每个端点替换后做功能验证
3. 关闭后台循环，启动 V5 ReflectScheduler
4. 删除已替换的 neko memory/ 子模块

### Phase C: 下线独立组件 (1 天)
1. 停止 EmbeddingService (本地 ONNX)
2. 删除 per-character SQLite 文件管理
3. 如果稳定运行一周，删除 `core/neko/memory/` 已迁移模块
4. memory_server.py 从 226KB → ~30KB

## 7. 回退方案

如果在任一阶段出现问题：
- 保留 `core/neko/memory/` 目录中的 JSON/SQLite 文件作为备份
- V5 侧数据可通过 `bin/migrate-neko-to-v5.py` 重新导入
- memory_server.py 的原始版本保存在 `core/neko/app/memory_server.py.bak`（需先备份）
- 临时切换: 设置环境变量 `IKAROS_V5_MEMORY_PROXY=0` 可切回原版
