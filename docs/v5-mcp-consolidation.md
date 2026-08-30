# V5 MCP 工具合并 + 标准记忆循环

> 日期：2026-08-30
> 范围：`core/memory_v5/`（MCP 工具面）+ `core/ikaros-dsh/plugins/ikaros-memory/`（dsh 插件）
> 状态：已实现，双模开关默认 `legacy`（兼容），切 `slim` 只需改一个环境变量

---

## 1. 结论先行

| 项 | 改前 | 改后 |
|---|---|---|
| MCP 工具数（暴露给模型） | 50 个扁平工具 | **legacy 58 / slim 17** |
| 工具清单真相源 | **两份**（`tools/__init__.py` 扫描 + `mcp_server.py` 手写分组表） | **一份**（`tools/registry.py`） |
| 每轮记忆仪式 | 模型自己记得调 N 次（实测从未调过） | **3 个 hook 各调 1 次 loop op** |
| 机器态动作（tick/record） | 4 个 MCP 工具，模型可忘 | 内化进 Loop，不可忘 |
| 检索是否泄漏归档记忆 | **是**（753/1200 向量是 archived） | **否**（入口拦截，见 §5） |

核心思路：**减少模型需要「记得做」的事**。工具面收敛成「热路径独立 + 冷路径门面」，
每轮必做的仪式收敛成声明式 Loop —— 模型不必再凭自觉调用记忆工具。

---

## 2. 为什么工具会膨胀到 50 个

统计每个 `v5_*` 工具在项目里的外部引用（dsh persona / SOUL.md / AGENTS.md / 插件）：

| 被点名的工具 | 引用次数 |
|---|---|
| `v5_memory_store` | 5 |
| `v5_project_retrieve` | 3 |
| `v5_recall` | 2 |
| `v5_memory_search` | 2 |
| `v5_project_note` | 1 |
| **其余 45 个** | **0** |

即 90% 的工具是「一个动词一个工具」的历史堆叠，没有任何外部文档或 persona 依赖它们。
它们存在的唯一原因是「曾经有人想手动调一次」。

---

## 3. 工具面：热路径独立 + 冷路径门面

### 3.1 三层结构（`tools/registry.py` 是唯一真相源）

| tier | 数量 | 说明 | 注册模式 |
|---|---|---|---|
| `core` | 9 | 热路径，独立工具无 `action` 参数，调用最快 | legacy + slim |
| `facade` | 8 | 冷路径门面，一个资源一个工具 + `action` 分发 | legacy + slim |
| `legacy` | 41 | 被门面吸收 / 被 Loop 内化的旧工具 | 仅 legacy |

**core（9）** —— 保留独立形态，因为它们是 persona 与文档点名的入口，加一层 `action`
参数只会增加误调用概率：

```
v5_memory_store / v5_memory_search / v5_memory_get / v5_memory_delete / v5_memory_stats
v5_recall
v5_project_note / v5_project_retrieve / v5_project_stats
```

**facade（8）** —— 一个资源一个工具，内部按 `action` 分发：

| 门面 | action 数 | 吸收的旧工具 |
|---|---|---|
| `v5_self` | 7 | self_model / self_reflect / latest_thought / curiosity_check / subconscious / context_refresh / self_discover |
| `v5_state` | 11 | analyze_emotion / emotion_status / emotion_label / care_check / care_status / vitality / vitality_tick / relationship / relationship_tick / activity_status / context_compression_stats |
| `v5_content` | 3 | narrative_generate / dissonance_check / proactive_check |
| `v5_skill` | 5 | write / list / get / search / remove |
| `v5_reflection` | 5 | synthesize / read / apply_evidence / promote / stats |
| `v5_directive` | 4 | add / list / deactivate / stats |
| `v5_repeat` | 5 | record / check / penalty / clear / stats |
| `v5_loop` | 2 | status / run（标准记忆循环入口） |

门面是**纯委托**（`tools/facade.py`），不含任何新逻辑 —— 测试 `test_tools_facade.py`
逐字节比对门面输出与旧工具输出，保证零行为变更。

### 3.2 为什么必须先做「单一真相源」

改造前工具清单存在两份：

1. `tools/__init__.py` —— 从各子模块 `dir()` 扫出的 `v5_*` 全集
2. `mcp_server.py` —— **手写**的 `_TOOL_GROUPS` 字典（分组过滤用）

2026-08-24 的 P3-25 提交把 (1) 改成自动派生，漏了 (2) → 新增的 `v5_recall` 进了全集
(50) 却不在分组表 (49) → `test_mcp_tool_groups.py` 12 个测试全红。

**这正是「工具太多」难以维护的症结**：清单手抄两份，必然漂移。
所以先建 `tools/registry.py`，再谈合并。它的自检是**启动即炸**式的：

```python
missing = [n for n in found if n not in tier_map]
if missing:
    raise RuntimeError(f"tools/registry.py 未登记以下工具: {sorted(missing)}")
ghost = [n for n in tier_map if n not in found]
if ghost:
    raise RuntimeError(f"tools/registry.py 登记了不存在的工具: {sorted(ghost)}")
```

漏登记或幽灵登记都留不到运行时。加工具 = 只改这一个文件。

### 3.3 分组（`V5_MCP_TOOL_GROUPS`）

| 组 | 工具数 |
|---|---|
| memory | 25 |
| self | 17 |
| skill | 6 |
| project | 3 |
| care | 2 |
| vitality | 2 |
| relationship | 2 |
| **loop** | **1**（新增，`v5_loop`） |

⚠️ **`cordis.patch.yml` 的 `V5_MCP_TOOL_GROUPS` 必须包含 `loop`** —— 漏掉的话 slim
模式下只剩 16 个工具，且没有 Loop 入口（实测踩过）。

---

## 4. 标准记忆循环（`memory_v5/loop.py`）

### 4.1 为什么要把流程做成 Loop

有 4 个工具是**机器态维护动作**，不该由模型决定做不做：

```
v5_vitality_tick        # 精力推进
v5_relationship_tick    # 关系推进
v5_anti_repeat_record   # 反重复语料记录
v5_reflect_run_op       # 反思管线
```

实测这些手工 tick **从未被调用过** —— 模型不会记得在每轮结束时推进精力模型。
结果：状态只进不出，长期停在初始值。

Loop 把它们变成「每轮自动跑」，模型无法忘记。

### 4.2 三阶段

| phase | 触发时机 | steps | 内化的旧工具 |
|---|---|---|---|
| `pre` | 每轮开始前 | identity → recall → project | `v5_context_refresh` / `v5_recall` / `v5_project_retrieve` |
| `post` | 每轮结束后 | vitality → relationship → anti_repeat | `v5_vitality_tick` / `v5_relationship_tick` / `v5_anti_repeat_record` |
| `maintenance` | 周期（默认 6h） | reflect | `v5_reflect_run_op` |

引擎特性：

- **声明式 step 表**：`LoopStep(name, fn, phase, interval_sec, enabled)`，加一个 step
  只改 `_default_steps()`，不改引擎（对比 V3「加第 5 个操作要复制 4 行 if 模板」）。
- **单步失败不上抛**：收集进 `errors` 继续执行。**记忆是增强，不是阻断会话的理由**。
- **冷却与 `force`**：maintenance 的 reflect 带 6h 冷却（对齐 retention/promote 周期），
  `force=True` 可强制全跑（手动补账用）。
- **状态落盘** `data/v5/loop_state.json`（atomic write + 滚动 .bak）。

⚠️ 路径锚定用 `Path(__file__).resolve().parent`（memory_v5 包根）—— 不能用
`__file__.parent`，否则会重演 2026-08-24 的孤儿目录事故（`reflect/scheduler.py` 曾把
状态写到 `core/memory_v5/reflect/data/v5/`）。

### 4.3 插件侧：3 个 hook 各调一次

```
agent/pre-step       -> v5_call loop(phase=pre)           身份 + 召回 + 项目经验
agent/turn-stopping  -> v5_call loop(phase=post)          精力/关系推进 + 反重复语料
ctx.interval (6h)    -> v5_call loop(phase=maintenance)   反思管线
```

`loopEnabled: false` 可整体退回旧路径（pre-step 直调 `search`、定时器直调 `tick`）。

⚠️ **post 阶段与自动沉淀分开注册**：自动沉淀有 5 分钟冷却 + 最短轮长闸（防连续短轮
反复写库），如果把 post 挂在同一个 handler 里，会出现「聊了 50 轮但关系一次没推进」
的欠账。两者触发条件不同，必须分开。

### 4.4 踩坑记录

**vitality 单调抽干（本次引入，同轮修掉）**
`vitality.tick(conversation=True)` 会把**整段经过时间的空闲恢复也一并跳过**
（`vitality.py` 内 `if not conversation: ... recovery ...`）。Loop 每轮都调 →
精力单调下降到 0，persona 永远显示「精疲力竭」。

修法是把两笔账分开结：

```python
v.tick(conversation=False)   # (a) 经过时间: 衰减 + 空闲恢复
v.tick(conversation=True)    # (b) 本轮对话: 一次性消耗 (dt≈0)
```

已知后续项：彻底修要拆 `vitality.tick()` 的两个语义（牵动其他调用方），留作独立改动。

**项目轨 query 透传导致恒 0 命中（本次引入，同轮修掉）**
`v5_project_retrieve` 带 `tags` 时走 `memory_api.search` 的**结构化 SQLite 路径**，
`query` 不是语义查询，而是被拼成 `content LIKE '%<整句话>%'` 的**子串过滤**。
把整句自然语言塞进去 → 恒 0 命中 → 29 条项目记忆一条都浮不出来，且 `count=0` 被插件
当「无内容」跳过注入 —— 静默失效，无任何报错。

修法（见 §5 之后的 `_step_project`）：先用 query 收窄，收窄为空则退回项目全览
（按 weight 降序）。结果里带 `strategy` 字段标明走了哪条路，可观测。

---

## 5. 顺带修掉的检索 bug：archived / 孤儿向量泄漏

> 与本任务相邻，但直接污染 Loop 的召回质量，一并根治。

**实测证据**（2026-08-30，`core/memory_v5/data/v5/v5.db`）：

| 项 | 数量 |
|---|---|
| memory 表总行数 | 1092 |
| `archived=1` | **753（69%）** |
| chroma 向量总数 | 1200 |
| 向量中属于 archived 记忆 | **753（63%）** |
| 向量中 v5.db 已不存在的孤儿 | 110 |

`archived=1` 是 V5 既定的软删语义（retention 淘汰 / dedup 合并 / 过期转存），
`lifecycle` / `freshness` / `project_edges` / `reflect/*` **每条读路径都带
`archived = 0`**，唯独检索层没接：

- 三路融合的 FTS5 走 `store.search`（自带过滤，安全）
- **向量路直接查 chroma，从头到尾不看 `archived`** ← 口子在这
- `memory_api.search` 的结构化 SQL 路径也漏了 `archived` 条件

后果：归档机制在语义检索面前形同虚设 —— 每天被 retention 标死的记忆，语义照捞回候选池
并顶掉 `top_k` 名额；孤儿向量召回的 id 在 v5.db 里查不到，`v5_memory_get` 直接空。

**修法**：

1. `memory_retrieval._live_ids()` —— 存活 id 集合（30s TTL 缓存），
   **查库失败返回 `None` = fail-open 不过滤**，绝不把检索搞挂。
2. 过滤放在 `retrieve()` 的 `_add` **入口**而非结果截断 —— 死记忆若只被截断，
   仍会占 `top_k` 名额并稀释融合排序。
3. `memory_api.search` 结构化路径补 `archived = 0`；老库无该列时去掉条件重试一次。
4. 只拦 id 空间确定是 memory 表主键的路径（fts/vec/time）。graph（`eg_*`）与
   vault（ThirdSpace）的 id 不在 memory 表，用存活集过滤会误杀，故不动 `_finish`。

**未做（需哥哥拍板）**：chroma 里那 753 条 archived 向量 + 110 条孤儿向量**仍在库里**，
现在只是运行时拦掉了。彻底清理要删 chroma 记录（破坏性数据操作），建议做成一次性
脚本 + 备份后执行。

---

## 6. 顺带根治的三个旧 bug

> §4.4 记录的两个回归，事后都证实**不是 Loop 引入的**，而是 Loop 把既有 bug 暴露
> 到了台面上。两者都在本轮做了根治性修复（不是 workaround）。

### 6.1 `vitality.tick()` 的 `conversation` 标志语义不对称 → 精力单调抽干

**现象**：post 阶段冒烟跑几轮后 `vitality: 0.0, label: "精疲力竭"`。

**根因**：`conversation` 一个标志同时管两件**正交**的事：

| | 语义 | 判定 |
|---|---|---|
| (a) | 收 `_CONVERSATION_COST` 一次性成本 + 计数 | 合理，保留 |
| (b) | 抑制**整段经过时间**的空闲恢复 | **bug**，拆出 |

(b) 的后果：任何每轮调 `tick(conversation=True)` 的路径都只减不增，
精力单调抽干到 0。

**受害面不止 Loop** —— `vitality.py` 的 `vitality_prompt()` 走 cloud_chat
**主链路**，同样 `tick(conversation=True)` 后 `save()`。也就是说这个 bug 在
Loop 存在之前就已经在抽干 Ikaros 的精力值，只是没人盯着看。

**修法**：拆出 `conversation_minutes` 参数，恢复改按**真空闲分钟数**计算
（`idle_min = 经过时间 - conversation_minutes`），与 `conversation` 标志彻底脱钩。

```python
# 修前：conversation=True → 整段经过时间都不恢复 → 只减不增
# 修后：恢复只按真空闲的那部分时间算
conv_min = min(max(0.0, conversation_minutes or 0.0), dt_min)
idle_min = dt_min - conv_min
recovery = _RECOVERY_RATE * idle_min * (1.0 - self.vitality)
self.vitality = self.vitality - decay + recovery
```

**实测对比**（模拟每轮间隔 1 分钟）：

| 轮数 | 修前 | 修后 |
|---|---|---|
| 25 | 0.000 | 0.498 |
| 150 | 0.000 | 0.463 |
| 间隔 5min × 30 轮 | 0.000 | 0.862 |

修后曲线稳定收敛在 0.46~0.86 区间，不再归零。

**测试**：`tests/test_vitality_recovery.py`（9 用例），包括
`test_repeated_conversation_ticks_do_not_drain_to_zero`（连打 60 轮后 > 0.05）。

---

### 6.2 `unified_retrieve(scope="lexical")` 契约违背

**现象**：docstring 承诺 `scope="lexical"` 是「仅 FTS5 关键词」，
实际是 lexical + semantic + graph 三路叠加。

**根因**：lexical 分支拿到结果后**不 return**，控制流掉到下面的
`auto / semantic` 块继续跑语义融合，再进 graph fallback。同层的
`tree` / `temporal` 分支都有 `if merged: return _finish(...)`，唯独 lexical 漏了。

**为什么要修而不是将错就错**：调用方显式选 `lexical` 就是为了**只要**词法结果
（可控、可复现、不受 embedding 服务状态影响）。悄悄混入语义结果破坏了这个前提，
而且失败时无法归因 —— 检索质量一波动，分不清是词法路还是语义路的问题。

**影响面核实**：grep 全仓确认**生产代码无 `scope="lexical"` 调用方，仅测试使用**，
修复零风险。

**测试**：`tests/test_unified_retrieve.py::test_u2b_lexical_does_not_leak_semantic`
—— 装一个「被调用即记录」的语义路探针，断言命中 lexical 后探针 hits 为空。

---

### 6.3 `v5_recall` 候选被冷却一空 → 交空纸条

**症状**：`pre` 阶段 `recall.context` 是「(无相关记忆)」，但 `stats.retrieved` 明明是 14。

**根因**：`recall_ledger` 的冷却窗口有个放大器 ——

| 事实 | 后果 |
|---|---|
| ledger **落盘持久化**（`data/v5/recall_log_<sid>.json`） | 跨进程重启存活 |
| `turn` 单调递增、**永不清零** | 新会话接着旧 turn 数 |
| 插件 `session_id` 硬编码 `'dsh'` | 所有会话共用一本账 |

三者叠加，「同一话题连着问几轮」或「重启后紧接着复问上一个话题」时，
top-k 候选会**整批**处于冷却中 → `fresh` 为空 → 装配 0 条 → 返回空上下文。

**影响面实测**（不同 query 各跑一轮，同一 session）：

| query | retrieved | cooled | placed |
|---|---|---|---|
| `MCP 工具合并` | 10 | 0 | 10 |
| `vitality 精力模型` | 10 | 0 | 10 |
| `conversation-tree 端口` | 11 | 2 | 9 |

即：**只有同/近似 query 连续重复才会触发**，兜底极少介入，不抵消去重收益。

**修法**：`fresh` 为空且 `results` 非空时放宽去重，重新用全量候选，
并在 stats 里置 `dedup_relaxed: true` 让行为可观测。

> 设计裁定：**去重是优化（省 token、少点「又讲一遍」的噪声），
> 返回空上下文是功能性失败 —— 宁可重复，不可失忆。**

**测试**：`tests/test_recall_dedup_fallback.py`（6 用例），覆盖
首次无冷却 / 全冷却放宽 / **部分冷却时不放宽**（防止兜底抵消去重收益）/
检索本就为空时不放宽 / 空 query 短路 / 连续重复不逐轮劣化。

**未做（需哥哥拍板）**：插件侧 `session_id` 仍是 `'dsh'`。改成真实 dsh 会话 id
是更彻底的解法，但需要反查 dsh 的 Session API 且**要重启 dsh 才能验证**
（重启会中断 :3080 当前 Web 会话）。当前兜底已消除功能性影响，故先不折腾。

---

## 7. 双模切换

```bash
# cordis.patch.yml
V5_MCP_TOOL_MODE: 'legacy'   # 默认，58 工具，完全兼容旧行为
# V5_MCP_TOOL_MODE: 'slim'   # 17 工具（9 core + 8 facade）
```

- 非法值 **fail-open 到 legacy**（同分组过滤的既有约定）。
- 旧函数全部保留为 Python 函数，只是 slim 模式下不注册为 MCP 工具 ——
  测试、脚本、桥接层 import 不受影响。

### 7.1 切 slim 前的可执行闸门

slim 只注册 17 个工具，另外 41 个**不再存在**。但「哪些地方还引用着会被摘掉的
工具名」原本没有任何静态检查 —— 测试直接 import Python 函数，不走 MCP 注册，
**这类问题在测试里永远不会暴露**，只在真实会话里发作：

| 引用位置 | 切 slim 后的症状 |
|---|---|
| persona（`cordis.patch.yml`） | 模型照指令调用 → `tool-not-found`，每轮浪费一次重试 |
| 插件 TS / `v5_call.py` | 静默失败，连报错都没有 |
| `AGENTS.md` / `CLAUDE.md` | agent 学到的动作做不到 |
| `SOUL.md` / `capabilities.md` | Ikaros 的自我描述与实际能力不符 |

所以闸门是**可执行**的，不是文档里的检查清单（清单会被忘记执行）：

```bash
python core/memory_v5/tools/slim_check.py          # 退出码 0 = 可以切
python core/memory_v5/tools/slim_check.py --all    # 连文档/历史引用也列出来
python core/memory_v5/tools/slim_check.py --json   # 机器可读
```

⚠️ **必须以脚本方式运行**，`python -m memory_v5.tools.slim_check` 起不来
（`-m` 要求 `memory_v5` 在模块执行前就可导入，脚本内的 `sys.path.insert` 救不了自己）。

检查项：

| 项 | 严重度 | 说明 |
|---|---|---|
| 运行期文件引用 legacy-only 工具名 | **阻塞** | `LIVE_FILES` 5 个文件，逐条给出替代方案 |
| slim 工具的 group 不在 `V5_MCP_TOOL_GROUPS` | **阻塞** | 缺组会被**静默过滤**（实测踩过：缺 `loop` → 只剩 16 个且无 Loop 入口） |
| 工具名不在注册表 | 警告 | 可能拼错，也可能是 `mcp__ikaros-v5__v5_x` 的截断残片 |
| 身份文件引用 legacy-only 名 | 提示 | 不影响运行，但切 slim 时应同步 |

消噪设计（假阳性一多，真阻塞项就没人看了 —— 狼来了）：

- **代码文件的注释行不算引用** —— 注释里的工具名是叙事，不产生调用
- **markdown 支持 `<!-- v5-history -->` 哨兵区间** —— AGENTS.md 的变更日志整节包起来，
  里面的「过去修了 v5_xxx」是事实陈述，不是运行期契约
- **白名单** `_NON_TOOL_ALLOWLIST` —— `v5_call`（插件 CLI 脚本名）、`v5_kind`、
  `v5_key`、`v5_memory_id`、`v5_project` 是字段名/参数名，长得像工具名但不是
- **只扫运行期加载的文件，不扫 `docs/`** —— 历史文档里 26 个工具名上百处，
  改它等于篡改历史，而且会把真阻塞项淹掉；`--all` 可看全量（含统计）

替代方案**从 `facade.py` 源码静态解析**（读真源，不复制映射表 —— 复制就会漂移，
这正是 registry 要根治的病），并遵循 **Loop 内化优先于门面**的判定顺序：
被 Loop 内化的工具在 slim 下根本不注册，告诉模型「改用 `v5_repeat(action=...)`」
是错的，正确说法是「由 `v5_loop` 的 post 阶段自动推进，不用手动调」。

---

## 8. 文件清单

| 文件 | 变更 |
|---|---|
| `core/memory_v5/loop.py` | **新增** 标准记忆循环引擎（三阶段 + 声明式 step 表 + 状态落盘） |
| `core/memory_v5/tools/registry.py` | **新增** 工具单一真相源（自检启动即炸） |
| `core/memory_v5/tools/facade.py` | **新增** 8 个冷路径门面（纯委托，零行为变更） |
| `core/memory_v5/tools/__init__.py` | 分组/层级改为从 registry 派生 |
| `core/memory_v5/mcp_server.py` | 删手写分组表；支持 `V5_MCP_TOOL_MODE` 双模注册 |
| `core/memory_v5/memory_retrieval.py` | ① 新增存活 id 过滤（archived/孤儿）② lexical 分支补 return |
| `core/memory_v5/tools/recall_tool.py` | 候选被冷却一空时放宽去重（宁可重复，不可失忆） |
| `core/memory_v5/memory_api.py` | 结构化检索补 `archived = 0` + 无列回落 |
| `core/memory_v5/vitality.py` | `tick()` 拆出 `conversation_minutes`，恢复按真空闲时长算 |
| `core/ikaros-dsh/cordis.patch.yml` | `V5_MCP_TOOL_GROUPS` 加 `loop`；新增 `V5_MCP_TOOL_MODE` |
| `core/ikaros-dsh/plugins/ikaros-memory/src/index.ts` | 3 个 hook 改为调 loop op；新增 `renderLoopPreSnapshot` |
| `core/ikaros-dsh/plugins/ikaros-memory/bin/v5_call.py` | 新增 `loop` op；`tick` 标记 deprecated |
| `core/memory_v5/tools/slim_check.py` | **新增** 切 slim 的可执行闸门（§7.1），退出码 0/1 |
| `data/soul/SOUL.md`、`config/identity/capabilities.md` | 工具引用改为 slim 名（slim 是 legacy 的子集，两模都安全） |
| `AGENTS.md` | 变更日志节加 `<!-- v5-history -->` 哨兵；MCP 双模条目指向闸门；插件重建步骤加同步检查 |
| `core/ikaros-dsh/tools/plugin_sync_check.py` | **新增** 插件源码↔dsh 已装副本的同步检查（§9） |
| `core/ikaros-dsh/cordis.patch.yml` | 插件装配注释指向同步检查 |

**测试**（全量 **535 passed / 0 failed**，历史首次全绿）：

| 文件 | 用例数 | 守什么 |
|---|---|---|
| `tests/test_loop.py` | 20 | 引擎机制（step 表/冷却/失败不上抛/状态持久化），stub step 不碰真实库 |
| `tests/test_tools_facade.py` | 31 | 门面对旧工具纯委托，输出逐字节一致 |
| `tests/test_tool_registry.py` | 15 | 单一来源不变量：分组/层级一一对应、无孤儿工具、slim=17 |
| `tests/test_mcp_tool_groups.py` | 27 | 重写：期望值从 registry 派生（原版硬编码 49 工具，是漂移源） |
| `tests/test_retrieval_archived_filter.py` | 8 | archived/孤儿过滤 + fail-open + 缓存失效 |
| `tests/test_vitality_recovery.py` | 9 | 空闲恢复语义：连打 60 轮不归零、`conversation_minutes` 抑制与封顶 |
| `tests/test_unified_retrieve.py` | +1 | lexical 命中后不得再跑语义/图路（探针断言） |
| `tests/test_recall_dedup_fallback.py` | 6 | 去重兜底：全冷却放宽 / 部分冷却**不**放宽 / 检索空不放宽 |
| `tests/test_slim_check.py` | 7 | 闸门自身：当前必须绿 / 每个 legacy 工具都有替代方案 / 内化工具指向 Loop / **门面 action 表覆盖率 ≥37**（守 §7.1 那个静默失效 bug）/ 缺 group 阻塞 / 注释与哨兵不误报 |

---

## 9. 差点白干：插件 dist 6 天没重装，Loop 在 dsh 里是死的

> 收尾时做「改了的东西到底生效没有」的验证才发现的。**代码、测试、文档全绿，
> 但功能在生产里根本不存在。**

`pnpm add file:<dir>` 是**复制**不是符号链接，改完 `src/` 或 `bin/` 必须
`npm run build` + `pnpm remove` + `pnpm add` 才会进到 dsh 实际加载的那份包里。

2026-08-30 的实测底数：

| | 源码（今天改的） | dsh 实际加载的 | |
|---|---|---|---|
| `dist/index.js` | 08-30 13:49 | **08-24 13:27** | ❌ 差 6 天 |
| `bin/v5_call.py` | 08-30 13:08（6705 B，**含 `loop` op**） | **08-24 19:56**（4898 B，**无 `loop`**） | ❌ 差 6 天 |

**为什么测试没抓到**：冒烟是 `cd plugins/ikaros-memory && python bin/v5_call.py loop ...`
—— 走的是**源码目录**。而 dsh 加载的是 `~/.dsh/profiles/web/node_modules/@ikaros/dsh-ikaros-memory/`
里的副本。两者是不同的文件，测试全绿，生产是死的。

**修法**：做成可执行检查 `core/ikaros-dsh/tools/plugin_sync_check.py`，
按**内容 sha256** 比源码与已装副本（不比时间戳 —— pnpm 复制可能刷新也可能保留 mtime，
tsc 输出确定性强，内容变了大小也可能一致）。只比 `package.json` 的 `files`（dist/bin）。

修复后三个插件全绿：

```
✅ ikaros-conversation-tree   同步, 比对 4 个文件
✅ ikaros-memory              同步, 比对 2 个文件
✅ ikaros-memory-settings     同步, 比对 4 个文件
```

⚠️ 动手前**先备份**已装副本到 `tmp/plugin-backup-20260830/` —— `pnpm remove` + `add`
有把包装没的风险，`add` 失败就比改之前更糟。

⚠️ **装完还需重启 dsh**（`bin/restart-dsh-ikaros.ps1`）才会加载新代码，
重启会中断 :3080 的当前会话。

### 9.1 重启器本身也是坏的（两个致命 bug，同轮修掉）

按上面的步骤调 `bin/restart-dsh-ikaros.ps1` 重启，结果 **dsh 直接起不来**。
翻日志发现这个 .ps1 是老脚本，与 `core/ikarosctl.py` 的现行约定已经脱节：

| # | bug | 症状 |
|---|---|---|
| 1 | web 模式也传 `--patch` | `duplicate loader entry id: memory-ikaros-v5` → dsh 启动即崩 |
| 2 | 不设 `IKAROS_ROOT` | patch 里的 `!!js 'process.env.IKAROS_ROOT + "..."'` 算出 `undefined\runtime\...` → spawn ENOENT |

**bug 1 的机制**（`~/.dsh/profiles/web/cordis.yml` 顶部写着加载顺序）：

```
each bundle in package.json's dsh.profile.bundles, then cordis.patch.yml,
then any --patch overlays
```

profile 里那份 `cordis.patch.yml` **已经**包含 `memory-ikaros-v5`，再叠一份 `--patch`
就撞 id。`core/ikarosctl.py:182-185` 早就写明「web 模式不传 `--patch`，只有 headless 需要」，
但 .ps1 没跟上。

**bug 2 的机制**：`IKAROS_ROOT` 不是注册表持久变量（2026-08-11 起由 `bin/ikaros-env.bat`
注入），.ps1 直接 `Start-Process node` 而没有设它 → 所有 `!!js` 路径表达式算出
`undefined\...`。CT 插件日志留下了完整证据链：

```
[boot-iiFE] start IKAROS_ROOT=<unset> python=undefined/runtime/portable-python/python.exe
[child.on(error)] spawn undefined/runtime/portable-python/python.exe ENOENT
[boot-iiFE] start IKAROS_ROOT=E:\Ikaros python=E:\Ikaros/runtime/portable-python/python.exe
[startServer] spawn returned pid=33764 ... PORT=48920     <- 设了就正常
```

**修法**：委托给 `ikarosctl.py dsh sync` + `dsh restart`（与 `bin/ikaros.bat` 同一实现，
不再在 .ps1 里抄一份启动参数 —— 抄两份必然漂移，这正是本次反复在治的病）。
新脚本显式设 `$env:IKAROS_ROOT`，先 sync 再 restart，末尾跑 `dsh status` 留证。

⚠️ **另一条教训**：从 agent 进程里拉起的长期服务活不下来 —— 后台任务结束时进程树
被清理（实测 dsh 起了 7 分钟后消失，CT 日志停在启动成功处、**无崩溃记录**）。
**服务要哥哥手动从外部拉起**（`bin/start-dsh-ikaros.bat web`，或统一入口 `bin/ikaros.bat dsh restart`）。

---

## 10. 已知遗留

0. **⚠️ dsh 尚未重启**（§9）—— 插件 dist / `v5_call.py` 已在 2026-08-30 重装到
   `~/.dsh/profiles/web/node_modules/`，但 :3080 上跑着的 dsh 进程加载的是**启动时
   读进内存**的旧代码，**Loop 三阶段此刻仍是死的**。要让新代码生效必须重启
   （`powershell -File bin/restart-dsh-ikaros.ps1`），而重启会中断当前 Web 会话。
   验证是否生效：重启后在 :3080 开一轮，看 `data/logs/` 里插件日志有无 `loop` 调用。
1. **chroma 里的 753 条 archived 向量 + 110 条孤儿未物理清理**（§5，需拍板）。
   现在只是运行时拦掉了，彻底清理要删 chroma 记录（破坏性数据操作），
   建议做成一次性脚本 + 备份后执行。
2. **项目轨 top-k by weight 会浮出已退役组件的笔记**（omp/pi 已于 2026-08-23 退役，
   但对应 project 笔记 weight 仍是 0.9/0.8）。要么降权，要么标 `archived=1`
   （V5 既定的退役语义，且现在归档真的会被检索过滤掉了 —— 见 §5）。
3. **§4.4 记录的回归 + §6 的三个旧 bug 均已根治**，不再是遗留项。
4. **插件 `session_id` 仍是硬编码 `'dsh'`**（§6.3），兜底已消除功能性影响，
   彻底改要重启 dsh 才能验证，待哥哥拍板。
