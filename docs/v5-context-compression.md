# V5 上下文压缩算法（extensions/ 层）

> 配套架构索引：`docs/ARCHITECTURE.md` §5.2.5。
> 代码骨架：`core/memory_v5/extensions/`（含 `EXTENSIONS.md`）。
> 状态：**`token_compressor` 已接入主链路并验证**（2026-07-30，guard + 异常回退，替换 hermes 插件 `on_pre_compress` 的 `text[:150]` 硬截断，测试见 `tests/test_token_compressor_*`）；`gated_retrieval` / `temporal_graph` 仍为骨架，未并入主链路。

---

## 1. 为什么需要这一层

V5（5.1.0）的上下文缩减手段只有三类，且都存在短板：

| 现有手段 | 问题 |
|----------|------|
| `summary.py` 的 LLM 摘要旧轮 | 语义有损；**每 20 轮才触发**，爆发式对话里窗口照样爆 |
| 委托 Hermes `ContextCompressor` 压 transcript 中段 | 只在超阈值时跑，且是 LLM 摘要，不是「token 级」压缩 |
| `token_budget`（min 800 / max 1200 / `char_x`） | **配置了却从未被主检索代码消费** —— 真空白 |

对照 GitHub 上成熟的四条路线（Letta 分层 / Zep·Graphiti 时序图谱 / TencentDB L0-L3 蒸馏 / LLMLingua token 压缩），V5 缺三块硬能力：

1. **token 级压缩**（LLMLingua 的强项，20x 近零损失、专门对抗 lost-in-the-middle）；
2. **分层检索门控**（TencentDB 的强项，默认注高层、按需下钻低层，token 效率最高）；
3. **事实时效窗口 + 矛盾即更替**（Graphiti 的强项，LongMemEval 63.8% vs Mem0 49.0% 的差距来源）。

> Letta 式「模型自管内存」对模型「记忆 literate」要求极高，本地 1.7B 小模型扛不住 —— 所以 Ikaros 把检索外置给 V5、模型只答当前上下文，这条路线**不采用** Letta 自管范式。

---

## 2. 三层增强骨架

```
extensions/
├── token_compressor.py   # ① token 级压缩（LLMLingua 委派 + 规则回退）
├── gated_retrieval.py    # ② 分层检索门控（TencentDB 模式借鉴）
├── temporal_graph.py     # ③ 时效图谱（Graphiti 模式借鉴，SQLite 原生）
├── EXTENSIONS.md         # 接入点 / 风险 / 未做项
└── __init__.py
```

三者正交、互不依赖，可单独接入：

| 模块 | 补的缺口 | 灵感来源 | 是否用现成库 |
|------|----------|----------|--------------|
| `token_compressor.py` | LLMLingua 硬缺口 | 微软 LLMLingua | ✅ 委派 `llmlingua.PromptCompressor` |
| `gated_retrieval.py` | TencentDB 缺口 | TencentDB Agent Memory | ⚠️ 仅借鉴模式（非干净 pip 包） |
| `temporal_graph.py` | Graphiti 缺口 | Zep·Graphiti | ⚠️ 仅借鉴模式（不换图库） |

---

## 3. ① token_compressor.py（核心压缩算法）

### 设计原则
- **优先用现成库**：`llmlingua` 是真实可装的开源压缩器（README 实测 11x+ 压缩）。
- **U 盘离线便携**：不能硬依赖 HF 模型权重下载。因此做成**导入守护 + 离线规则回退** —— 没装 `llmlingua` 就全程规则压缩，功能不降级；装了且联网下过一次模型，之后离线可用缓存。

### 核心 API

```python
def compress_text(text: str, *, quality: str = "auto", target_ratio: float = 0.5) -> str
    # quality="auto" → 有 llmlingua 用 llmlingua_compress，否则 rule_compress
    # quality="llm"  → 强制 llmlingua（未装则回退规则）
    # quality="rule" → 强制规则

def rule_compress(text: str, ratio: float = 0.5) -> str
    # 确定性零 LLM：折叠空白/重复标点、删重复行、删语气 filler、超目标保头尾截中段

def llmlingua_compress(text: str, *, target_token: int | None = None, rate: float = 0.5) -> str
    # 委派 llmlingua.PromptCompressor().compress_prompt(text, ...)
    # 导入失败/异常 → 自动回退 rule_compress（debug 日志）

def compress_old_rounds(rounds: list[dict], tail_keep: int = 6, budget_tokens: int | None = None) -> list[dict]
    # 保最近 tail_keep 轮原样，压其余旧轮（命中 token_budget 时）

def compress_retrieval_block(results: list[dict], budget_tokens: int | None = None,
                             max_chars_per_item: int = 400) -> list[dict]
    # 高相关（score≥0.6）原样；低相关先 compress_text 再裁到 max_chars_per_item
    # 避免「要么全要要么全弃」式截断

def enforce_budget(blocks: list[str], budget_tokens: int) -> list[str]
    # 按 score/顺序截到预算内（est_tokens 用 char_x 估算）
```

### 接入点（尚未启用）
- Hermes 插件 `on_pre_compress`（`core/hermes/plugins/memory/ikaros_v5/__init__.py` 的检索结果）跑 `compress_retrieval_block`，替换当前 `text[:150]` 硬截断。
- 消费 `preprocess_config.yaml` 的 `token_budget`（min 800 / max 1200 / `char_x`）—— 这是之前一直空置的配置。

---

## 4. ② gated_retrieval.py（分层门控）

```python
def gated_retrieve(query_text: str | None, *, budget_tokens: int = 1200,
                   high_layer_budget: int = 400) -> str
    # 永远注入高层：self_model.get_self_prompt() + distill/reflect 层记忆
    # 仅当 query_text 实质化（非 None / 够长）且预算剩余 → 下钻 memory_retrieval.retrieve()
```

**动机**：V5 现有 `retrieve()` 是「vector 0.7 / fts 0.3 / 时间衰减」直接相似注入，缺 TencentDB 那种「默认注高层、按需下钻低层」的严格门控，默认 token 效率不如它。门控后，日常对话只带画像/反思层（几百 token），长程细节才按查询下钻。

**风险（上线前须知）**：高层记忆没并入总预算去重，可能和 `self_model` 重叠；需沙箱验证预算口径。

---

## 5. ③ temporal_graph.py（时效图谱，SQLite 原生）

```python
def apply_migration(conn) -> None
    # 幂等 ALTER：eg_entities / eg_edges 加 valid_from / valid_to（默认 NULL=永久有效）

def supersede_memory(mem_id: int, now: float) -> None
    # 旧事实 valid_to = now（Graphiti 式「矛盾即更替」）

def resolve_dissonance_supersede(dissonance_event: dict) -> None
    # 接在 dissonance.py 检测矛盾之后调用

def retrieve_temporal(query, ...) -> list[dict]
    # 优先有效事实（valid_to IS NULL OR valid_to > now），降权/排除已过期

# 更高效路径：直接给 entity_graph.spreading_activation_search 的 SQL 加
#   WHERE (eg_entities.valid_to IS NULL OR eg_entities.valid_to > ?)
#   并按 valid_to 排序优先有效实体（见文件末尾补丁）
```

**动机**：`dissonance.py` 目前发现矛盾只 `_record_dissonance`（存一条 `type=dissonance` 事件），旧事实仍 `valid_to IS NULL` **共存**。若「用户住 X」后来变成「用户住 Y」，两条会共存、检索可能捞旧值 —— 这正是 Graphiti 在 LongMemEval 领先的主因。

**已知限制（生产化前）**：`eg_edges` **无 `relation_type`** 列，因此 `supersede_entity_attribute` 只能粗粒度失效整实体出边；要精确到「某属性」需先加列、并让 `run_episodic_consolidation` 填关系类型。

---

## 6. 架构决策（已固化，2026-07-30）

> **V5 永久留在 SQLite（`v5.db`），不迁移任何图数据库后端**（Neo4j / FalkorDB / Kuzu / Neptune）。

- `temporal_graph` 仅**借鉴 Graphiti 模式**（时效窗口 + 自动失效）在 SQLite 上复刻，目标拿下 **80%+ 功能、0 架构迁移**；精确 relation_type 级 supersede、双时间追踪历史视图可放弃。
- `token_compressor` 继续使用 `llmlingua` 现成库（导入守护 + 离线回退），与此决策不冲突。
- 不引入 graphiti-core：它要换掉整个存储层 + 自带 OpenAI 抽取管线，对 U 盘离线便携的 Ikaros 不划算。

---

## 7. 接入优先级建议

1. **`token_compressor` —— 已完成接入并验证（2026-07-30）**：已接进 hermes 插件 `on_pre_compress`（guard + 异常回退），且修掉 3 个规则压缩 bug（换行折叠致 filler 删失效 / 短文本被截残 / 省略号边界 151>150）。**现状态：已上线、受集成测试守护**。
2. **再接 `gated_retrieval`** —— 复用 V5 已有的 `self_model` + distill/reflect，预算口径验证后可显著降低默认注入量。
3. **最后接 `temporal_graph`** —— 仅在确遇「偏好变了模型记旧值」痛点时启用；先跑 `apply_migration()`，再在 `dissonance.py` 后挂 `resolve_dissonance_supersede`。

`gated_retrieval` / `temporal_graph` **仍未在主链路启用**，接入时按 EXTENSIONS.md 的接入点逐模块灰度、保留回滚。
