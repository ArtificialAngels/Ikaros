# Ikaros 独立技术评估报告（只读）

- 评估对象：`E:/Ikaros`（架构 / V5 记忆系统 / Hermes 集成 / 代码质量 / 风险点）
- 评估方式：只读。源码通读 + 全量测试拓扑盘点 + 服务层并发/错误处理核查（3 个并行 scout 佐证，所有结论均锚定到 `file:line`）
- 评估日期：2026-08-11

---

## 一、总评

**一句话：** 一套有明确分层意图、以"V5 记忆"为公共契约的多进程系统；检索融合、乐观并发、降级链、失败兜底等关键路径体现了刻意的工程投入，但服务层/引擎层存在真实并发缺口，Hermes 集成路径存在数据丢失静默化与 schema 错配，且近年修复全部集中在运行时行为、几乎未触碰架构债务——属于"局部优秀、整体尚需收敛"的中上水平。

**总分：80 / 100**

分项参考：架构与分层 16/20；V5 记忆系统 21/25；Hermes 集成 10/15；代码质量与一致性 15/20；风险与工程债 18/20。

---

## 二、架构与分层

### 2.1 端口拓扑（实测）

| 服务 | 端口 | 位置 |
|---|---|---|
| 控制面板（dashboard） | `0.0.0.0:9100`（`IKAROS_CONTROL_PORT` 可覆盖） | `core/dashboard/server.py:56,2389` |
| 本地 LLM（懒加载 agent） | `:8080` | 见 `ARCHITECTURE.md` |
| Embedding 服务 | `:8587` | `core/memory_v5/search.py` 向量路径 |
| Neko 主服务（FastAPI 单 worker） | `127.0.0.1:48911` | `apps/neko/app/main_server/` |
| Neko 记忆服务（单 worker + 每角色锁） | `127.0.0.1:48912` | `apps/neko/app/memory_server/` |
| Neko Agent 服务 | `127.0.0.1:48915` | `apps/neko/app/agent_server/` |
| Hermes 网关/面板 | `:9119` / `:8642` | Hermes 侧 |
| Hermes-Paw | `:8088` | Hermes 侧 |
| 对话树面板（ThreadingHTTPServer + SSE） | `127.0.0.1:48920` | `core/conversation-tree/server.py` |
| V5 MCP（stdio，`--sse` 时 `:9877`） | 48 个 `v5_*` 工具 | `core/memory_v5/mcp_server.py:117-161` |

进程模型：dashboard 负责 spawn 三个 Neko 服务进程（`NEKO_STORAGE_ANCHOR_ROOT` 重定向状态目录，`core/dashboard/server.py:640-717`）；main↔memory 经 HTTP 启动屏障协调（`main_server/__init__.py:365-450`），agent→main 事件走 ZeroMQ（`character_runtime.py:289-290`）；V5 存储由多进程共享（watchdog / cloud_chat / hermes 并发写，`store.py:264-265` 注释明示）。

### 2.2 分层与契约

- L0–L3 逻辑分层明确（`docs/ARCHITECTURE.md`），但**接口是约定不是代码**：无独立 `interfaces` 包，跨进程靠 HTTP/JSON + 文件名约定；跨模块靠"别 import 内部符号"的纪律。
- **公共契约（不可改）**：`data/v5/v5.db` 库表结构、`v5_*` 工具前缀、`memory_v5` 包名（Hermes 插件经 `sys.path` 引入）。
- 检索统一入口收敛成功：`unified_retrieve()`（`memory_retrieval.py:277-390`）是事实上的单一路由，`memory_api.py:151-156`（auto scope）与 `conversation-tree/server.py:1527-1529`（tree scope）均经它走，内部递归降级链（auto→semantic→lexical…，`memory_retrieval.py:344/356/372`）全部 fail-open。
- 48 个 MCP 工具与任务书一致（`mcp_server.py:117-149`）。

---

## 三、优点清单（值得保留/推广的设计）

1. **检索融合设计精细、可配置、fail-open** — `memory_retrieval.py:59-241`：FTS5（0.3）＋向量（0.7）＋时间指代三路融合，权重/衰减/阈值全部来自 `preprocess_config.yaml`（运行时读，无需改码）；时间衰减下限 0.2 不归零（旧偏好仍有效，`memory_retrieval.py:192-195`）；同一记忆多路命中累加分量、按 id 去重（`memory_retrieval.py:109-117`）；`exclude` 子串重叠去重（`memory_retrieval.py:216-221`）。
2. **关键词兜底有实测依据** — `_keyword_fallback()`（`memory_retrieval.py:378-400`）复用 skill_store 中文 2-gram 拆词器，触发条件 2026-08-10 从 `<3` 放宽到 `<top_k`，注释记录真实失败案例（"memU 调研学到了什么" 整句 0 命中）。这是检索正确性最扎实的一处。
3. **SQLite 多进程写入设计正确** — `store.py:230-265`：WAL + `busy_timeout=5000` + 每次操作新建连接显式 close；写路径 4 次重试、指数退避 [1,3,5]s（`store.py:438-460`）——与 `ARCHITECTURE.md` 中"watchdog/cloud_chat 并发写"的既定事实匹配。
4. **向量侧跨进程写锁** — `search.py` `_chroma_write_lock`：Windows `msvcrt` + 进程内线程锁双保险，`.chroma-write.lock` 文件，专门解决 hnsw compactor 冲突；embedding 超 350 CJK 字符自动分块 + mean-pool（规避 `:8587` 512-token 上限）；`get_vector_index()` 单例缓存 + `refresh=True` 重放兜底（消解新记忆 30s 语义不可见窗口，`memory_retrieval.py:93-101` 注释记录）。
5. **自模型持久化是全书最佳范式** — `self_model.py:96-132,250-269`：`json_lock()`（msvcrt/fcntl + 进程内线程锁 + 3s 超时）＋ revision 乐观并发（`.revisions.json`，mismatch 抛 `RuntimeError`）＋ tmp+`os.replace` 原子写 ＋ 顶层未知 key 校验。
6. **反思管线（reflect）工程化完整** — op 工厂注册表（`reflect/registry.py`）＋ `ScheduleState` frozen dataclass + `MappingProxyType` 防篡改 ＋ 调度状态 tmp+replace 原子落盘（`scheduler.py:159-283`）；`run_all()` 逐 op `continue_on_error` 收集错误；各 op 有明确的周期/说明文档。
7. **三级提取兜底链（LLM→规则→安全兜底）** — `reflect/consolidate.py`：DeepSeek 结构化提取 → `rule_based_extract` 正则零 LLM（`consolidate.py:270-380`）→ `_fallback_filter`（按 weight 降序保留 top 50%，注释明言"never gamble on LLM decision"）；`_verify_with_big_llm` / `_verify_with_local` 双验证路径。
8. **对话树引擎的拓扑/内容分离与原子持久化** — `conversation_tree.py`：节点拓扑与内容分离、注入式 store（可换后端）、核心方法（`add_turn:632`、`jump_to:470`、`set_exec_state:388-410`、`serialize:1245`）持 RLock；`persist()` tmp+replace 原子写（`conversation_tree.py:1257-1266`）；trunk_id（S1 主线模型）＋ `session:<persist_key>` 标签隔离多会话。
9. **Hermes 插件为鸭子类型 + 惰性加载 + 质量闸门** — `plugins/ikaros_v5`：`memory_provider.py` 惰性初始化（失败返回 `False` 哨兵、不拖垮 Hermes 启动）、`__deepcopy__` 保证 agent 实例隔离、`_STORE_MIN_CHARS=6` / `_SKIP_PATTERNS`（"继续/然后/好的"跳过）防垃圾记忆、注入块按 context 预算自动截断（`context_engine.py`）。
10. **不和谐检测（dissonance）语义化且保守** — 语义检索 + DeepSeek NLI 判定（3 候选上限），reinforcement 衰减（下限 -2.0 不归零），时间超车（temporal supersede）链路；其测试用真实临时 DB（非全 mock）。
11. **修复史文档化** — 大量注释记录了具体事故与修复（promote 无 commit 导致永不落盘、向量索引 731/1433 丢失、FTS5 多 token 无结果改 OR、min_fused 0.6→0.3 重校准、Windows 文件锁、embedding 截断），这对后续维护者极有价值。
12. **测试体量可观** — `core/memory_v5/tests/` 27 文件 199 测试；`core/conversation-tree/tests/` 5 文件 62 测试（会话/引擎/SSE/导出）；neko 独立生态约 8066 测试。对话树引擎覆盖（37 项）与 sessions 模型（30 项）是全书覆盖最好的两块。

---

## 四、问题清单（按严重度）

### 4.1 严重（高）——影响正确性/数据一致性

**S1. 对话树 fork/merge 族方法完全不持锁，并发下可落盘出孤儿节点**
`conversation_tree.py:756-947`：`fork_branch`/`conclude_branch`/`merge_branch`/`unmerge_branch`/`abandon_branch` 均无 `with self._lock:`，直接读写 `self.nodes`/`children`/`current_id`/`version` 并 `persist()`；而 `add_turn:632`、`serialize:1245`、`jump_to:470` 有锁。后果：`serialize` 快照可能落在"节点已插入 `nodes`、`children.append` 未执行"之间 → 落盘拓扑出现无父链接节点；`add_turn` 的 node_type/trunk_id 判定（`651-668`）可读到 fork 交错中间态。顶层问题：ThreadingHTTPServer 每请求一线程，而 server 层全局 `_lock`（`server.py:951`）只被 `build_demo:1062`/`ensure_tree:1183`/导入建会话等少数路径使用，`do_POST` 全部变更端点（`/api/add_turn:2386`、`/api/fork:2398`、`/api/conclude:2408`、`/api/merge:2432`、`/api/unmerge:2436`、`/api/abandon:2440`、`/api/jump_to:2471`）均不持锁 —— 锁"形同虚设"。

**S2. Hermes 写回路径静默失败（多次证据）**
- 插件 `sync_turn` 为 daemon 线程（`plugins/ikaros_v5/memory_provider.py:287`），失败仅 debug 日志；
- `conversation_tree.py:1261-1262` `persist()` 失败仅 `logger.debug`，且 `conversation_tree.py:82` 注释证实**历史上已发生过 ToolCall TypeError 吞错导致整棵树数据丢失**的事故；
- `store.py:529`（`_sync_vector_best_effort`）、`store.py:553`（事件日志线程）异常全部 `pass`；
- `affect.py:201-203` 衰减路径自动保存吞错。
记忆写入不可观测——用户以为已记住，实际可能从未落盘。

**S3. affect.json 写读 schema 不一致 → Hermes 情绪块恒渲染空/零**
插件 `memory_provider.py:366-375` 读 `mood_label` / `pad` / `trust` keys；而 `AffectState.save()`（`affect.py:310-318`）按 dataclass `asdict` 平铺写 `pleasure/arousal/dominance/tension/...`。写入方与读取方字段对不上，插件侧情绪块 `current_prompt()` 输出恒为默认零值/空——属于"两端都自洽、对接必错"的典型契约错配。

**S4. `sessions.json` 非原子写 + 无锁读改写**
`conversation-tree/server.py:985-987` `_save_sessions` 直接 `write_text`（无 tmp+replace、无锁）；`_touch_active_session`（`1004-1025`）在 HTTP 线程内改 `_sessions` 并落盘；create/switch/delete（`2702-2704,2719-2720,2757-2758`）在锁外整体赋值。并发请求 → 半写文件/丢更新。与引擎 `persist()` 的原子写（`conversation_tree.py:1257-1266`）形成鲜明反差——同一模块两套标准。

**S5. `run_one` 更新调度状态但不持久化**
`scheduler.py:246-253`：`run_one()` 只更新内存 `self._state`，从不 `save_state()`（仅 `run_all():278-281` 落盘）。插件 `on_session_end`（`memory_provider.py:333`）调用 `run_one(force=True)` → `last_consolidate` 永远不落盘 → 每次会话结束都重新全量 consolidate（每次 DeepSeek 调用，成本/延迟双高），且重启后状态回卷。

### 4.2 中等——性能/扩展性/正确性边界

**M1. `store()` 每次写前执行 `PRAGMA wal_checkpoint(TRUNCATE)`**（`store.py:441-444`）——每次 INSERT 都 checkpoint，WAL 批量写优势被清零；高频写（cloud_chat/watchdog）下这是隐含的吞吐杀手，与 `busy_timeout` 设计意图相悖。

**M2. `conn()` 每次连接重放全部 schema + 异常流迁移**（`store.py:230-265`）——每次 store/search/get/delete 都重新 `executescript` 6 段 schema、`PRAGMA journal_mode=WAL`、8+ 个 `ALTER TABLE ADD COLUMN`（靠必然抛出的 duplicate-column `OperationalError` 做幂等判断）、重复的 `table_info` + `CREATE INDEX` 块、外加每次 `logger.info("V5 store: initialized")`。每操作数百毫秒级固定开销 + 日志噪音；该模式本质是把一次性迁移写进了热路径。

**M3. 无界 daemon 线程**——`store.py:529,553` 每次 `store()` 潜在 spawn 2 线程（向量同步 + 事件日志；`store()` 本身在 434 行附近）、插件每轮对话 1 线程 + 每会话 1 线程（`memory_provider.py:287,333`）、`conversation-tree/server.py:2833`、dashboard `run_component_action` 每动作新线程（`server.py:1631-1635`）。高写入量下线程数无上界；dissonance 线程还内含一次 fused_search + DeepSeek NLI（最长 20s）。

**M4. Hermes 跨进程写树无会话上下文**——`hermes_provider.py:346-356`（及插件的同构路径）向 `:48920` POST `/api/add_turn`，body 只有 messages，无 `session_id`/`persist_key` → 落到 UI 当前活动会话（可能错树），失败静默 `pass`；且与 S1 的无锁区叠加。

**M5. 检索器三分，统一入口未完全统一**
- `retrieve()` 三路融合（`memory_retrieval.py:59-241`）
- `search.py fused_search()` 双路硬编码融合（`search.py` 中段）
- `unified_retrieve()` 路由层（`memory_retrieval.py:277-390`）
`tools/memory_tool.py:80` 的 `v5_memory_search` 仍走 `fused_search`，与统一入口行为不同源 → 同一 query 在 MCP 工具与对话树侧可能给出不同结果。

**M6. `memory_api.py:33` `min_score=0.6` 死参数**——声明了但从未生效：`memory_api.py:151-156` 实际传 `min_weight=0.0`，fuse 路径无任何阈值 → 任何垃圾记忆只要融合分 >0 都可能被返回（检索正确性的一个真实漏洞，测试未覆盖）。

**M7. affect/relationship/vitality 持久化裸写，与 self_model 两套标准**——`affect.py:310-318` 直接 `write_text`（且 `affect.py:201-203` 在**读路径** `current_prompt()` 里因 5 分钟衰减到期触发自动保存写盘）；`relationship.py:137-151`、`vitality.py` 同款。无锁无原子 → 多进程（watchdog + Hermes + dashboard）并发写 affect.json 可撕裂。对照：self_model 的 `json_lock` + revision（见优点 5）。

**M8. 检索器集群状态无锁/边界竞态**——dashboard `_UPSTREAM_CACHE` 读-改-写无锁（`server.py:1034,1122,1134,1142`，虽单线程主要路径，但 `/api/events` SSE 线程可并发）；`rules_retriever.py:47-73` mtime 缓存 RMW 无锁；`cogno_5d` `_turn_counter` 无锁自增。均为"现在大概率没事、将来必出问题"级别。

**M9. Neko 同一服务双实现并行**——`agent_server.py`（5745 行）与 `agent_server/` 包、`memory_server.py`（4576 行）与 `memory_server/` 包、`main_server.py` 与 `main_server/` 并存。行为漂移风险高，谁在用哪个取决于 dashboard 的 import 路径，是最大的架构债之一。

**M10. 生产路径存在 stub**——`neko` `monitor.py:264-267` `translate_japanese_to_chinese` 为 stub（原样返回），生产调用方靠自身防御兜底。若调用方不防御即为静默错误输出。

**M11. 魔法数字约 30 处**——`emotional_memory.py:16` `_DELTA_THRESHOLD=0.12`（与 config 的 `diff_threshold=0.3` 不一致）；`hermes_provider.py:222` 情绪融合权重 0.6/0.4；`memory_retrieval.py` 的 `0.05/day`、`max(0.2,…)`、`200 条缓存上限` 等散落各处；截断长度类数字 20+ 处。多处数值重复且无单一事实源。

### 4.3 轻微——代码质量/一致性

**L1. 文档与代码漂移（实测）**
- `AGENTS.md:66`、`ARCHITECTURE.md:85` 引用 `hermes_provider.push_to_conversation_tree()`（标注 `hermes_provider.py:343`）——**该函数在代码中不存在**，`hermes_provider.py:343` 实为 `_do_sync_turn` 第 7 步的 HTTP POST 代码；实际同步机制是推 `:48920`；
- `ARCHITECTURE.md:399-420` 描述 provider_bridge 为 Hermes v5search 桥——grep 全库**无任何 `.py` 引用 `provider_bridge`**；`hermes_provider.py` 与 `provider_bridge.py` 在仓库内零 import（live 路径是外部插件 ikaros_v5）→ 两文件为死代码，文档仍指向它们（"dual implementation" 名存实亡）；
- `folder-tree.html` "40 个工具" 与实测 48 个不符。

**L2. 测试缺口（零覆盖/弱覆盖文件清单）**——`hermes_provider.py`、`mcp_server.py`、`rules_retriever.py`、`preprocess_config.py`、`anti_repeat.py`、`drivers.py`、`metacog.py`、`reflect/consolidate.py`、`reflect/distill.py`（及 `reflect/scheduler.py`、`store.py` 主体）零测试；`entity_graph.py`（仅 ontology_align + mock 路径）、`self_model.py`（仅工具层形状检查）、`proactive.py`（1 条形状断言）、`affect.py`（monkeypatch 场景）、`reflect/llm_client.py`（全 mock）弱覆盖。**检索核心（`memory_retrieval.py`）测试全 mock，真实 FTS5+Chroma+embedding 端到端从未在 CI 执行；并发零测试；中文检索仅测 mock 拆词**。dashboard 无任何测试。

**L3. 测试收集配置漂移**——`core/memory_v5/pyproject.toml` 的 `testpaths=["v5/tests"]` 已过时（实际 `tests/`）；根 `testpaths` 不含 conversation-tree（62 测试默认不跑）；`test_hermes.py` 为 e2e 脚本（0 collected）；`test_ikaros_studio_integration.mjs` 仍 import 已不存在的 `v5.orchestrator.agent_loop`。默认 `pytest` 约 282，全量约 400（不含 neko）。

**L4. 异常吞错总量**——全书 `except Exception` 1176 处，`logger.debug` 级 325 处，`pass`-only 异常 ≥120 处，`print()` 166 处（含 `affect.py:347` 错误路径 print）。fail-open 是美德，但统一到"降级路径必须可观测"的标准还没有。

**L5. 小问题**——`memory_api.py:82-108` structured 路径 `LIKE` 未转义 `%_`（自有数据内注入面）；插件 `_resolve_root` 用 `parents[4]` 猜路径（`memory_provider.py:47-57`）；`on_turn_start` 死代码（`memory_provider.py:302-316` 仅 debug 日志）；`system_prompt_block` 的 handoff 消费 `os.unlink`（`memory_provider.py:209`）多 session 竞态；`conversation_tree.py` 每次 `add_turn` 全树 `serialize` 无防抖（O(tree)）；`relationship.py:177-180` 每轮对话 `store.stats()` 全表统计（O(N)）；`self_model.py:106-107` revision 检查先于锁获取、revisions 文件读改写无锁（轻微竞态）；`sys.path.insert` 引导在 43 处复制（约 15 个文件各自实现）。

---

## 五、改进建议（按性价比排序）

| # | 建议 | 预期收益 | 成本 |
|---|---|---|---|
| R1 | **给 fork/merge 族补 `with self._lock:`，并在 server 层 `do_POST` 统一持 `_lock`**（S1）。最小改动：5 个方法加锁 + 变更端点包锁 | 消除最高危并发缺陷；改动面 <30 行 | 低 |
| R2 | **统一异常观测标准：写路径失败至少 `logger.warning` + 计数器**（S2/S4）。先给 `persist()`、`sync_turn`、`store()` 后台线程补告警 | 让"记忆没写进去"从不可见变可调查 | 低 |
| R3 | **修复 affect schema 错配**（S3）：插件读取侧改读 `pleasure/arousal/...` 或写入侧补 `mood_label/pad/trust` 兼容字段 + 迁移 | 情绪块立即生效（当前恒空） | 低 |
| R4 | **`run_one` 结尾补 `save_state()`**（S5） | 消除"每次会话结束都全量 consolidate"的 DeepSeek 成本 | 极低 |
| R5 | **删掉 `store()` 写前 `wal_checkpoint(TRUNCATE)`，改为仅启动时 checkpoint 一次**（M1） | 高频写吞吐显著提升 | 极低 |
| R6 | **`conn()` 迁移与热路径分离**：把 executescript/ALTER/建索引收敛到一次 `migrate()`（模块级单例或版本号比对），热路径只 `PRAGMA` + 查询；顺带删 `logger.info`（M2） | 每操作数百 ms 固定开销归零；日志干净 | 中（需迁移测试） |
| R7 | **线程池化后台任务**：`store()`/插件的 daemon 线程改为有界 executor（如 `ThreadPoolExecutor(max_workers=4)` + 队列）并观测队列深度（M3） | 线程数上界 + 背压信号 | 中 |
| R8 | **检索入口收敛**：`v5_memory_search` 改走 `unified_retrieve`，废弃 `fused_search` 双路路径；给 `memory_api` 补真实阈值（修 M6 死参数）（M5/M6） | 工具与对话树行为同源；检索质量可预期 | 中 |
| R9 | **补 3 类高价值测试**：(a) 对话树并发测试（多线程 fork+add_turn 竞态回归）；(b) 真实 SQLite FTS5 中文检索端到端（不再 mock）；(c) `reflect/consolidate` 提取链（LLM 层 mock、规则层真实） | 直接守住本报告 4 个高危点中的 3 个 | 中 |
| R10 | **修 `sessions.json` 原子写**（S4）：复用引擎的 tmp+replace 模式 | 会话文件不再半写 | 极低 |
| R11 | **清理死代码 + 修文档**（L1）：删/标注 `hermes_provider.py`、`provider_bridge.py`；更新 `AGENTS.md`/`ARCHITECTURE.md` 的同步机制描述与工具数 | 新维护者不再被误导；消除"双实现"假象 | 低 |
| R12 | **affect/relationship/vitality 统一到 `json_lock` 范式**（M7）：至少给 affect.json 加锁 + 原子写，读路径（decay 自动保存）改由定时/写路径触发 | 多进程并发写 JSON 不再有撕裂风险 | 低-中 |
| R13 | **Neko 双实现收敛**：确认 dashboard 实际 import 路径，删掉另一套（M9） | 消除最大架构债 | 高（需验证启动路径） |
| R14 | **魔法数字收敛到 `preprocess_config.yaml` 或模块常量表**（M11），先处理 `_DELTA_THRESHOLD` 与 config 不一致处 | 调参不再要改码 | 低 |
| R15 | **测试收集修复**（L3）：`testpaths` 指向 `tests/`，把 conversation-tree 纳入根 testpaths，删过时 e2e/集成脚本 | CI 覆盖从 282 → ~400 真实生效 | 低 |

---

## 六、特别关注

### 6.1 检索正确性
- 融合公式正确、可调、有兜底（见优点 1/2），但**实际生效的查询入口不是同一套**（M5），且 `memory_api` 的阈值参数是死的（M6）→ 同一 query 在 MCP 与对话树侧结果不同源，且可能返回垃圾记忆。
- `retrieve()` 对"时间指代命中"给初始分 1.0（`memory_retrieval.py:174-181`），保证过阈值——设计聪明，但语义上是硬编码加分，未来调整阈值时容易被遗忘。
- 向量刷新兜底（`refresh=True`）是一次 850ms 级全量重建（注释自述），在每 query 热路径上由"空结果"触发——低频可接受，但高并发下多个线程同时触发重建会造成瞬时尖峰（无重建互斥）。

### 6.2 多会话并发（最高风险区）
- 三层串起来看：引擎无锁方法（S1）＋ server `_lock` 未覆盖变更端点（S1）＋ Hermes 跨进程写树无 session 上下文（M4）＋ `sessions.json` 非原子（S4）。
- 现状是"单用户日常操作 + 低并发"恰好不触发；一旦 dashboard 与 Hermes 同时操作（插件每轮对话推送 + UI 手动 fork），S1 的中间态落盘即可出现孤儿节点/错树。**这是本报告认为最值得优先修的点（R1）。**
- 对照项：neko memory_server 的每角色 `asyncio.Lock` + 单循环串行（`runtime.py:313-359`）是正确范式，对话树应借鉴其"按持久化 key 串行化"思路。

### 6.3 中文检索
- FTS5 侧：`_sanitize_fts5_query` 转义 + 2026-08-10 起多 token 改 OR（`store.py:486-510`），对中文整句命中率有实测改进；`search_like` 中文 LIKE 兜底带 `%_` 转义（注入安全）。
- 拆词兜底仅 2-gram（`_keyword_fallback` 复用 skill_store tokenizer）——对 4 字词（"伊卡洛斯"）会拆成"伊卡/卡洛/洛斯"三个 2-gram，命中率依赖 FTS 索引的 tokenizer 行为；**该链路全 mock 测试，真实中文端到端行为未验证**（R9 的第 2 项）。
- 建议：补一条真实中文语料端到端测试（写入→检索→断言召回），并验证 `v5.db` FTS5 的 tokenizer 配置与拆词假设一致。

### 6.4 Hermes 插件链路脆弱点（`plugins/ikaros_v5`）
- **惰性初始化失败返回 `False` 哨兵后永不再试**（`context_engine.py`）——embedding 服务或 `:8587` 临时不可用，整个记忆链路本次会话内永久失效（无重试/恢复钩子）。
- `sys.path.insert` 引导核心包（`memory_provider.py` initialize）——对 Hermes 进程内模块名冲突/多实例敏感。
- `__init__.py` 用 `hasattr` 守卫双注册（防止 Hermes 同时加载新旧插件）——正确但脆弱：任何加载顺序变化都会静默退化。
- 写回全异步 + 失败仅 debug（S2）——**用户无从知道"这轮对话没被记住"**。
- 建议：哨兵失败改为"重试 + 告警"；写回失败至少 `logger.warning`；`parents[4]` 路径解析改为显式环境变量/包定位。

---

## 七、结论

V5 记忆系统在检索、并发写入、持久化范式、反思管线四个维度都有教科书级局部实现（尤其 self_model 的锁 + revision、检索的三路融合 + 兜底、consolidate 的三级降级），这是 80 分的根基。扣分集中在：**并发缺陷全部落在"被 ThreadingHTTPServer 放大的对话树热路径"上（S1/S4）、Hermes 写回不可观测（S2）、契约错配（S3/S5）、以及每年新增修复都绕开架构债（M1/M2/M9/L1）**。先做 R1–R5（合计改动 <100 行），再按 R6–R9 补齐迁移与测试，架构健康度可再上一个台阶。

---

## 六、修复状态（2026-08-11）

- **R1-R5 已修复**（pi 实施，Ikaros 验收）：
  - R1 对话树 fork/merge 族加锁（引擎 5 方法 + server 6 变更端点）
  - R2 写路径失败告警升级（persist/sync_turn/vector sync → warning）
  - R3 affect schema 错配修复（插件改走 AffectState API，情绪块实测非空）
  - R4 run_one 单步落盘（reflect_state.json 实测更新）
  - R5 wal_checkpoint 移出写热路径（一次性初始化）
- pytest 295 全绿（+14 新增）；R6-R15 未动，待后续迭代

- **R8/R9 已修复（2026-08-11）**：
  - R8 检索入口收敛：v5_memory_search 切 unified_retrieve(scope=auto)；memory_api min_score 后置过滤生效（M6 死参数修复）；fused_search 保留（dissonance/metacog 仍调用）标注废弃
  - R9 测试缺口：+test_fts_chinese_e2e.py（真实 SQLite/FTS5 中文端到端）、+test_conversation_tree_concurrency.py（fork/add_turn 并发回归）、+test_consolidate_chain.py（LLM mock + 规则层真实提取链）
