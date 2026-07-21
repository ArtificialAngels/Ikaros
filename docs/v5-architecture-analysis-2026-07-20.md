# Ikaros V5 架构现状分析 (2026-07-20)

> 基于实测代码梳理：`Ikaros-memory/v5/`、`bin/cloud_chat.py`、`bin/ikaros-voice-ws.py`。
> 注：`docs/v5-architecture-review.md`(2026-07-11) 已偏旧，本文件反映当前真实状态。
> 与旧 review 相比，V5 已新增 `orchestrator`(agent/companion 双模)、`Hermes` 深度集成、主动搭话链路，且统一了思考出口与好奇心真源。

## 一、总体结论

- V5 实际模块 ~30 个，集中布局在 `Ikaros-memory/v5/`(核心) + `bin/cloud_chat.py`(companion 编排中枢) + `bin/ikaros-voice-ws.py`(语音入口)。
- 07-11 review 提的 7 个问题，4 个已实质解决（统一思考出口、LLM 云兜底、好奇心单一真源、主动搭话链路落地）；但**关系/叙事层仍是半接入状态**，且新增结构在旧 review 里完全没出现。
- 架构健康度：分层清晰、无孤儿核心模块；但有 3 个"弱接线/孤儿"模块待收口（`narrative` / `relationship` 后台演化缺失 / `dissonance` 仅事实侧）。

## 二、当前分层（10 层）

| 层 | 核心模块 | 职责 | 落盘 | 接线状态 |
|----|---------|------|------|---------|
| 入口/编排 | voice-ws, cloud_chat, orchestrator | 语音入口 / companion 主链 / agent 双模 | — | 核心已接 |
| 路由/任务 | router, task_runner | 分类 task/conversation；后台委托 Hermes | task_result.json | 已接 |
| 主动意识 | think, proactive, care | 统一后台循环 / 门控主动搭话 / 关怀 | proactive_speech.json, subconscious.json | 已接（新增） |
| 自我认知 | self_model, metacog | 持久自我/信念/探索欲；真LLM内省·哲思 | self_model.json, latest_thought.json | 深度已接 |
| 情感/精力 | affect, vitality, drivers | PAD+TLS 6D；精力；混沌驱动 | affect.json | 已接 |
| 关系/叙事 | relationship, narrative, dissonance | 亲密度；月度叙事；冲突检测 | relationship.json | **半接入** |
| 算法底座 | drivers(Lorenz/ECA/AIS) | 混沌漂移/思维主题/新颖性 | ais_detectors.json | 已接 |
| 记忆/反思 | store/search/memory_retrieval, reflect | 记忆读写/三路检索；consolidate/distill | v4.db | 已接 |
| 预处理工厂 | rhythm/summary/profile/emotional_memory | R2-R6 注入 system prompt | summary_cache.json | 已接（V5.2） |
| 接口/落盘 | mcp_server, hermes_client | MCP 暴露；Hermes WS 集成 | data/v5/*.json + v4.db | 已接 |

## 三、两条主链路

### 实时对话链
```
用户语音/文本 → voice-ws(:7870)
  → cloud_chat.build_system_prompt
      ├─ R2 节奏 / R4 摘要 / R5 画像 / R6 情感对比+回忆
      ├─ affect PAD+TLS 状态块
      ├─ metacog.latest_thought（"你在想什么"钩子 / 相关时轻提醒）
      └─ relationship.track_interaction（写，事件驱动）
  → 云 LLM 出回复
  （或 orchestrator agent 模式：本地LLM选 v5_* 工具 → observe 合成）
```

### 后台自主链（`think.schedule()` 启动）
- **统一 5min 循环**：`metacog.cycle()`(真LLM内省/哲思 → latest_thought.json；:8080 挂则 `_fallback_thought` 占位保持非空) → 好奇心 tick(共享 `self_model.curiosity`) → `care.tick()` → `vitality.track` → `proactive.try_proactive()`(门控通过 → `proactive_speech.json` 供 voice-ws 播报)
- **潜意识流 2-3min**：Hermes/本地LLM 轻量絮语 → `subconscious.json`
- **Hermes worker 常驻**：`think.schedule` monkey-patch `call_llm(_auto)` → 反思优先走 Hermes :9119；`metacog` 默认 `provider="auto"` 走 `hermes_prompt_sync`

## 四、相比 07-11 review 的演进

| 旧问题 | 状态 | 说明 |
|--------|------|------|
| #1 :8080 单点无兜底 | ✅ 已解决 | `call_llm_auto`=本地优先+DeepSeek兜底；`emotional_memory`/`router` 已用 auto；仅 `label_emotion` 仍写死 local(可容忍，有规则兜底) |
| #2 两套思考出口 | ✅ 已解决 | `inner_monologue` 改为单向记录(写V4)；统一出口 `metacog.cycle`→`latest_thought.json`；新增 `_fallback_thought` |
| #3 两套好奇心 | ✅ 已解决 | 全部收敛到 `self_model.curiosity` 单一真源 |
| 主动搭话缺失 | ✅ 已解决(新增) | `proactive.py` + think 循环驱动 + `proactive_speech.json` |
| orchestrator 双模 | 🆕 新增 | agent/companion 切换；agent 用本地LLM选 `v5_*` 工具 + observe 合成 |
| Hermes 深度集成 | 🆕 新增 | think.schedule monkey-patch `call_llm(_auto)`→Hermes；metacog `provider=auto` 走 `hermes_prompt_sync` |

## 五、当前待收口隐患（按优先级）

### 🔴 高
1. **`narrative.py` 基本孤儿**：无 `generate/monthly/run` 调用（仅 self_model 名词提及 + tools 暴露）。月度叙事未回写 self_model，与旧 review#4 一致，仍未打通。
2. **`relationship` 仅事件驱动、无后台演化**：对话轮 `cloud_chat` 调 `track_interaction` 写，但 think 循环/后台无主动更新；且 relationship 状态**未作为"读"块注入 prompt**（只有写）。关系亲密度不会自己生长。
3. **`dissonance` 仅事实写入侧检测**（`cloud_chat:1900` / `memory_tool` docstring），非持续认知协调；认知失调不会主动浮现。

### 🟡 中
4. **`label_emotion` 写死 `provider="local"`**：:8080 挂时情感标注降级规则（有兜底），但缺云兜底一致性。
5. **多入口并发写 `data/v5/*.json`**：`affect.decay` 自动保存 + metacog + think + cloud_chat 多线程；`self_model` 有 `json_lock`，但 affect/care/relationship 部分写无统一锁（旧 review#7 部分缓解，未全解）。
6. **Hermes 集成强依赖 `hermes.exe` 路径**：`task_runner`/`hermes_client` 硬编码 `hermes-agent/venv/Scripts/hermes.exe`；venv 迁移后会断（与 memory 里 venv 符号链接坑同源）。

### 🟢 低
7. **`proactive_speech.json` / `subconscious.json` 等状态文件散落 `data/v5`**，无 schema 版本/统一管理器。
8. **本地LLM模型名滞后**：`llm_client` 用 `local-llm`；实际 qwen3-8b(:8080) 已替换 qwen2.5-7b，注释仍写 Qwen2.5-7B。

## 六、建议下一步（供哥哥定夺）

- **A. 打通 narrative**：让 metacog 月度节拍调用 `narrative.generate()` 并回写 `self_model.self_narrative`（收口旧 review#4）。
- **B. relationship 加低频后台演化**（idle 时小幅涨亲密/信任衰减），并注入 prompt"我们关系"块。
- **C. dissonance 升级为"记忆写入后 + 对话时"双触发**，并让冲突进入 `latest_thought` 让哥哥感知。
- **D. `label_emotion` 改 `call_llm_auto`**（保规则兜底）。
- **E. 统一 `data/v5` 写入加 `json_lock`**（参考 `self_model.json_lock`）。

（分析由 agent 基于实测代码整理，供伊卡洛斯自查。）
