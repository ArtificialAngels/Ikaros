# Ikaros 项目全量梳理报告（MCP 工具驱动）

> 日期: 2026-08-02 · 方法: gitnexus（符号级）+ graphify（图谱级）+ everything（文件级）
> 目的: 为后续改进提供全景视图 + 风险清单

---

## 一、项目规模（索引实测）

| 维度 | gitnexus | graphify |
|------|----------|----------|
| 节点 | 6,202 | 5,974 |
| 边 | 11,715 | 11,048 |
| 聚类/社区 | 268 | 347 |
| 执行流 | 300 | — |
| 索引提取 | — | 95% EXTRACTED |

**注**: gitnexus FTS 索引曾缺失（`analyze --repair-fts` + 完整 `analyze` 已修复，39.5s）。

## 二、核心架构（graphify 图谱确认）

```
┌─────────────────────────────────────────────────────┐
│  9100 控制面板 (core/dashboard/server.py, 2198行)     │
│  · 组件生命周期: component_start/stop × 12 组件       │
│  · spawn_hidden + kill_port + wait_for_port          │
│  · hermes 启动三重修复 (CREATE_NO_WINDOW/stamp 对齐)   │
└──────────────┬──────────────────────────────────────┘
               │ 端口表
┌──────────────▼──────────────────────────────────────┐
│ 服务层                                            │
│  :48920 对话树 (conversation-tree/server.py)        │
│  :8642  Hermes gateway (hermes_cli gateway run)     │
│  :9119  Hermes dashboard · :8088 paw                │
│  :8080  本地 LLM (懒加载) · :8587 embedding         │
│  :48911-15 neko 三服务 · herdr 命名管道              │
└──────────────┬──────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────┐
│ 记忆核心 (core/memory_v5/)                          │
│  · store.py: SQLite + FTS5 + WAL + 写操作需显式commit │
│  · search.py: VectorIndex (Chroma, 跨进程写锁)       │
│  · memory_retrieval.py: 三路融合检索 + 20s TTL 缓存   │
│  · reflect/: 10+ 反思 op (consolidate/promote/...)   │
│  · conversation_tree.py: 树引擎 (HIGH 风险核心)      │
└─────────────────────────────────────────────────────┘
```

## 三、关键枢纽与风险面（gitnexus impact 实测）

| 符号 | 风险 | 直接调用者 | 受影响进程 | 说明 |
|------|------|-----------|-----------|------|
| **ConversationTree** (conversation_tree.py) | 🔴 HIGH | 2 | 4 | 改它波及 conversation-tree/server.py 的 ensure_tree/do_POST/build_demo，9 个符号 |
| **retrieve** (memory_retrieval.py) | 🟠 MEDIUM | 20 | 多 | 三路融合检索核心，20 个符号受影响 |
| **V5MemoryAPI** (memory_api.py) | 🟢 LOW | 2 | 0 | 记忆单一入口，被 memory_tool.py/project_tool.py 引用 |
| **HerdrClient** (client.py) | 🟢 LOW | 4 | 0 | 命名管道客户端，API 面完整 (pane/agent/workspace × 20+) |

**执行流重点**:
- `run_conversation` (patches/hermes/agent/conversation_loop.py:1083-7047) — Hermes 对话主循环（5900+ 行庞然大物）
- `consolidate_conversations` → `_fallback_filter` / `_parse_json_array` — 记忆蒸馏链路
- `build_chat_messages_v5` → `build_v5_memory_block` / `build_tree_aware_context` — 树域上下文组装

## 四、七大模块速览（graphify 社区聚类）

1. **memory_v5** (最大社区群): store/search/retrieval/reflect/conversation_tree/entity_graph/vitality/dissonance/anti_repeat 全套
2. **dashboard/server.py**: 12 组件生命周期 (neko_group/local_model/memory/hermes_dashboard/herdr/conversation_tree...)
3. **herdr** (core/herdr/): HerdrClient(243行) + SessionRegistry + CodingAgentSupervisor + _selftest — 提案已落地
4. **hermes 补丁体系**: patches/hermes/ 7 A 类补丁 + 3 B 类插件/技能; hermes-update-and-patch.py 三步法重打
5. **对话树**: conversation_tree.py 引擎 + conversation-tree/server.py 面板 + tree_adapter 树域记忆
6. **neko 前端**: 独立于 memory_v5 (不引入依赖), 三服务架构
7. **环境层**: core/env/ (PATH-LAYER, ikaros-paths.json, llama_resolver.py)

## 五、近期修复记录（2026-08-02，本会话）

- UI: 9100 注释 `*/` 致命 bug / 响应式 8 视口全绿; 48920 文本溢出 17→0
- V5: 731 条缺向量回填 (845→1556); 跨进程 Chroma 写锁; vector_sync 24h→5min 增量
- V5: 转存机制修复 (promote 从未 commit 的隐蔽 bug); cleanup 改归档不删除
- V5: sqlite3.Cursor 不支持 `with` 的 TypeError 迁移 bug

## 六、改进建议清单（供后续）

### 高风险区（改前必查 impact）
1. **ConversationTree** — 任何树引擎改动先跑 `impact ConversationTree upstream`
2. **run_conversation** — 5900 行巨型函数，考虑拆分（参照 hermes AGENTS.md 的 god-file 拆分解法）

### 已知技术债
3. **MCP gitnexus server 持旧索引** — analyze 后需重启 MCP server 才生效（本次 FTS 缺失即因此）
4. **裸 ThreadingHTTPServer 手写路由** — route_map 无法自动识别（/api/* 无框架注解）
5. **dashboard/server.py 2198 行** — component_start/stop 家族大量重复模式，可提炼基类
6. **tmp/ 下调试残留** — ui-*.js / test_chroma_lock.py / mcp_*_debug.py 等，可清理归档

### 数据层
7. **vector_sync 5min 兜底** — 若写时同步稳定可考虑回退更长间隔省 embedding 开销
8. **archived 记忆无恢复工具** — 只有 SQL 层面可查，可加 v5_memory_unarchive 工具
9. **检索 TTL 缓存 20s** — 新记忆入库后 20s 内同 query 查不到，可考虑 store 时主动失效对应 cache_key

### 工具链
10. **gitnexus/graphify 双索引** — 定期 `analyze` + `graphify update --force` 保持新鲜（gitnexus 约 40s，graphify 约 1min）
11. **everything 搜索** — 已可用，Windows 全盘文件检索利器

## 七、验证状态
- pytest 242 passed ✅
- gitnexus 索引重建完成 (6,202 nodes) ✅
- graphify 图完整 (5,974 nodes) ✅
