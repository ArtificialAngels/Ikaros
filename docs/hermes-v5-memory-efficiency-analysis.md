# Hermes × V5 记忆效率分析报告（待处理）

> 日期: 2026-08-10 · 状态: **待处理**（哥哥说改的时候一起处理）
> 分析人: Ikaros · 基于实际代码走读 + 实测数据

## 背景

ikaros V5 记忆架构在 Hermes 中效率低，Hermes 无法有效使用记忆，有时连人格都不全。
本报告记录完整链路、六个根因（前两个致命）、人格不全机制与修复方向。

## 实际工作链路

插件位置: `data/hermes-agent/plugins/ikaros_v5/`（context_engine.py + memory_provider.py）
激活配置: `context.engine: ikaros_v5`、`memory.provider: ikaros_v5`、`plugins.enabled: [ikaros_v5]`

只有两个注入点:

1. **每轮 prefetch**（`memory_provider.py:176`）
   - 每轮对话前用 `store.search()` 做 FTS5 关键词检索 top5
   - 结果以 `<memory-context>` 围栏块**追加进当轮 user 消息的 API 副本**
   - 注入点: `turn_context.py:78`（`build_memory_context_block`），不落库、不破坏 prompt cache
   - 外部 provider 走 8 秒超时 + 阻塞 join（`memory_manager.py:47, 580`）

2. **压缩时 on_pre_compress**（`memory_provider.py:318`）
   - 对话逼近 75% token 阈值触发压缩时，注入「affect.json 情绪 + FTS5 top5 + 记忆统计」
   - 走 `conversation_compression.py:2751` → `context_engine.py:82`（`compress()` merge 进 memory_context）

人格（SOUL.md）走 Hermes 自己的 `load_soul_md()`（`prompt_builder.py:1986`），与 V5 插件无关。

## 六个根因

### ① 插件绕过 V5 语义检索，退化成裸关键词搜索（致命）
`prefetch` / `on_pre_compress` 都直接调 `store.search()`（纯 FTS5 bm25）。
V5 的 `unified_retrieve`（向量 0.7 + FTS 0.3 三路融合 + 图扩散 + 时间衰减）**一行都没用**。

实测对比（2026-08-10）:

| 查询 | 插件用的 store.search | V5 的 unified_retrieve |
|---|---|---|
| `模型成本` | **0 命中** | 2 命中 |
| `哥哥喜欢什么` | **0 命中** | 3 命中（user_trait 语义召回成功） |

### ② FTS5 默认分词器不切中文（致命）
`memory_fts` 建表没指定 tokenizer（默认 unicode61），中文按连续 CJK 串整体当 token。
库里明明有「glm 花了 11 美元」的记忆，搜「模型成本」就是 0 —— 非字面匹配全挂。
**每轮 prefetch 大部分返回空串，记忆注入形同虚设。**

### ③ 人格动态状态从不进主 prompt
`system_prompt_block()` 注入的是固定说明文字（「可以调 v5_memory_search」），不是记忆本身。
V5 的 self_model / relationship / emotion_status 只是躺在 52 个 MCP 工具 schema 里，等模型主动调。
deepseek-v4-flash 不会主动调 → 平时模型完全看不到信任度 0.94、当前情绪、身份信念等动态人格。
只有压缩那一刻 affect.json 的情绪出现一次，且是给摘要用的。

### ④ system_prompt_block 的承诺是空头支票
它写着「每 8-12 轮隐式提示自己的身份」，但 `on_turn_start`（`memory_provider.py:267`）
注释明确说「不额外注入，避免破坏 prompt cache」——实际什么都不做。人格漂移无自查机制。

### ⑤ 工程性损耗
- 外部 provider prefetch: 8 秒超时 + 阻塞 join，V5 查询稍慢就被跳过
- `len(query)<4` + `is_trivial_prompt` 双重过滤，短查询直接不查
- sync_turn 每轮对话以 `Q:...\nA:...`（assistant 截 150 字）无差别写入同一个库
  （724 条 conversation、tags=hermes_session，无会话隔离）→ 写入噪音反污染检索

### ⑥ 52 个 v5_* MCP 工具全量挂每轮 schema
token 开销大，稀释模型工具注意力。

## 「人格不全」机制

人格链 = SOUL.md（静态 5KB，UTF-8 正常）→ `load_soul_md()` → system prompt stable tier。

「不全」来自三个叠加:
1. **动态人格零注入**（根因③）——模型只靠静态文字撑人格，V5 的 5 条 identity + 43 条 lesson + 38 条 preference 全等它主动调 MCP 工具，不调就看不见
2. **SOUL.md 被 `_truncate_content` 按模型上下文截断**（`prompt_builder.py:2007`）——模型 context 小/系统提示膨胀时人格尾部丢失
3. **压缩后重建稀释**——`IkarosV5ContextEngine.compress()` 把 V5 上下文 merge 进摘要，摘要再过 `sanitize_memory_context` 6KB 截断（`context_engine.py:34`）

## 修复方向（按性价比排序）

1. **换检索**：`prefetch`/`on_pre_compress` 改调 `unified_retrieve(query, scope='auto')`
   ——一行改动解决根因①②，效率提升约 80% 的关键
2. **动态人格进 system prompt**：会话开始时把 self_model + relationship + emotion_status
   渲染成固定文本注入 `system_prompt_block()`（会话级缓存，不破坏 prompt cache），
   而不是靠模型主动调工具
3. **sync_turn 加会话隔离 + 质量门**：按 session_id 打标签，过滤低信息量对话，控制写入频率
4. **收敛 MCP 工具**：52 个 v5_* 收敛成高频几个（memory_search / store / self_model），降 schema 开销

## 关键证据位置

- 插件: `data/hermes-agent/plugins/ikaros_v5/memory_provider.py`（prefetch:176, on_pre_compress:318, on_turn_start:267）
- 插件: `data/hermes-agent/plugins/ikaros_v5/context_engine.py`（compress:65, _merge_memory_context:92）
- Hermes: `agent/turn_context.py:78`（注入点）、`agent/memory_manager.py:47,580`（8s 超时）、
  `agent/conversation_compression.py:2751`（on_pre_compress 调用点）、`agent/prompt_builder.py:1986`（load_soul_md）
- V5: `core/memory_v5/store.py:606`（纯 FTS5）、`core/memory_v5/memory_retrieval.py`（unified_retrieve 未用）
- DB: `core/memory_v5/data/v5/v5.db`，1707 条；conversation 724 / user_trait 548 / fact 183
