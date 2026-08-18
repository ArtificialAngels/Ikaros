# Hermes × V5 MCP 工具分组暴露调研（待拍板）

> 日期: 2026-08-10 · 状态: **调研完成，待哥哥拍板**（CortexFS 借鉴落地项 P3，仅调研不改代码）
> 调研人: Ikaros · 基于实际代码走读 + 实测数据

## 结论先行

1. **值得做，但当前收益边界很窄**：48 个 `v5_*` 工具全部平铺注册、对每个 api_server 会话无差别全量暴露（实测 schema 26,767 B ≈ 每请求约 7K token 常驻开销）。但今天唯一的 api_server 消费方是 conversation-tree（ikaros / hermes 双模式），**模式分化是真实存在的分组需求**；若没有多模式消费方，分组 ≈ 零收益 + 纯配置复杂度。
2. **已有两套过滤机制，但都是「静态 × 全局」**：config 级 per-server include/exclude glob（`mcp_tool.py`）+ 平台级 toolset 门控（`platform_toolsets`）。**不存在按会话动态分组**的能力，要做必须改 gateway。
3. **关键风险（实测）**：`registry.dispatch()` 按名执行任意已注册工具，**不校验会话可见列表**（`_last_resolved_tool_names` 仅用于 tool_search bridge 目录）。分组只砍 schema 可见性 ≠ 阻断执行——幻觉调用未列出工具仍会执行。任何「收紧」方案都必须带 dispatch 侧闸门，否则是纸面收紧。

## 现状链路（代码走读）

### 工具注册（V5 侧，零分组元数据）

`core/memory_v5/mcp_server.py`：

- `mcp = FastMCP("Ikaros V5 Memory", instructions=...)`，instructions 长文案本身已按 memory / emotion / self / care / vitality / relationship 分组描述（这是模型唯一的分组提示，无结构元数据）。
- 48 个工具定义在 `_NEW_V5_TOOLS` 列表，`for _tool_fn in _NEW_V5_TOOLS: mcp.add_tool(_tool_fn)` 平铺注册，**无分组、无命名空间、无过滤开关**。
- transport 默认 stdio；`sse` 参数 → :9877。

### 注入链路（Hermes 侧）

1. `data/hermes-agent/config.yaml` → `mcp_servers.ikaros-v5-memory`（stdio 子进程 `mcp_server.py`，env: `IKAROS_ROOT/HERMES_ROOT/HERMES_HOME`，`enabled: true`）。当前**启用 12 个 MCP server**：codebase-memory / context7 / gitnexus / playwright / ikaros-v5-memory / best-cad-mcp / blender-mcp / everything / graphify / graphify-memory / comfyui / ikaros-rescue。
2. `tools/mcp_tool.py:_register_server_tools()`（约 :5813）：每 server 支持 `tools.include` / `tools.exclude`（fnmatch glob，include 优先，两者皆无 = 全量）；工具以 `mcp__{server}__{tool}` 注册进 toolset `mcp-{server}`，并 `register_toolset_alias(server, toolset)`。
3. `hermes_cli/tools_config.py:_get_platform_tools()`：**默认把全部 enabled MCP server 并入每个平台**（`include_default_mcp_servers=True` → `enabled_toolsets.update(enabled_mcp_servers)`）；平台列表显式含 MCP 名 = allowlist；`no_mcp` 哨兵 = 全关。`agent.disabled_toolsets` 最后全局减扣。
4. **会话组装**：`gateway/platforms/api_server.py:_run_agent()`（约 :2807）`enabled_toolsets = sorted(_get_platform_tools(user_config, "api_server"))` —— **平台级固定值，所有会话完全相同**。当前 `platform_toolsets` 只配了 `cli: [file, terminal]`，api_server 走默认复合 `hermes-api-server` + 全部 12 个 MCP server。
5. 后置注入：`agent_init.py:1430` `agent.tools = get_tool_definitions(enabled_toolsets, ...)`；`:1764` `inject_memory_provider_tools(agent)`（ikaros_v5 插件的 memory provider 工具，按 `memory` toolset 门控）；context-engine 工具同模式（`agent._context_engine_tool_names`）。**这三类互不相干**：48 个 MCP 工具 / 插件 memory provider 工具 / context engine 工具。

### 实测开销

| 项 | 值 |
|---|---|
| v5 工具数 | 48 |
| 全部 schema JSON 体积 | 26,767 B（平均 557 B/工具） |
| 估算 token/请求（×4） | ≈ 6.7K（另有 FastMCP instructions 文案 + 其余 11 个 MCP server + hermes-api-server 复合工具） |

每个 api_server 请求（conversation-tree ikaros/hermes 双模式同一端点）都携带全量工具 schema。

## 48 工具 → 7 组分表

按任务给定分组（memory/self/care/vitality/relationship/skill/project），emotion / narrative / proactive / activity 归入 self（与 FastMCP instructions 的分组语义一致）：

| 组 | 工具（48） | 数量 |
|---|---|---|
| **memory** | `v5_memory_store/search/get/delete/stats`、`v5_dissonance_check`、`v5_context_compression_stats`、`v5_directive_add/list/deactivate/stats`、`v5_anti_repeat_record/check/penalty/clear/stats`、`v5_reflection_synthesize/read/apply_evidence/promote/stats` | 21 |
| **self** | `v5_analyze_emotion`、`v5_emotion_status`、`v5_emotion_label`、`v5_self_model`、`v5_self_reflect`、`v5_self_discover`、`v5_latest_thought`、`v5_curiosity_check`、`v5_subconscious`、`v5_reflect_run_op`、`v5_narrative_generate`、`v5_proactive_check`、`v5_activity_status` | 13 |
| **care** | `v5_care_check`、`v5_care_status` | 2 |
| **vitality** | `v5_vitality`、`v5_vitality_tick` | 2 |
| **relationship** | `v5_relationship`、`v5_relationship_tick` | 2 |
| **skill** | `v5_skill_write/list/get/search/remove` | 5 |
| **project** | `v5_project_note/retrieve/stats` | 3 |
| 合计 | | 48 |

## 方案与改动点

### Option 0 — 维持现状
全量 48 工具进每个会话。已有缓解：tool_search bridge（工具面 > 上下文 10% 时把非核心工具延迟到 `tool_search/tool_describe/tool_call`）——**token 驱动而非域驱动**，不解决「模型面对 48 个无关 schema 的选择噪音」。

### Option 1 — config-only 静态收紧（零代码）
- 1a：单 server 加 `tools.exclude` 全局剔除不用的组（例如 companion 场景剔 skill/project）——**全局生效，无法区分 ikaros/hermes 模式**。
- 1b：拆 7 个 server 条目（同一 `mcp_server.py` 路径 + 各自 include glob）→ 7 个 toolset `mcp-ikaros-v5-{group}` → `platform_toolsets.api_server` allowlist 按平台启用。
  - 改动点：仅 `data/hermes-agent/config.yaml`。
  - 代价：**7 个子进程**，每个都加载完整 FastMCP + V5 store 栈（内存 ×7、gateway 启动拉起 7 次），为同一个 DB 开 7 份连接——浪费，不建议 7 拆；最多 2–3 拆。
  - 仍然**静态全局/平台级**，非会话级。

### Option 2 — server 侧分组过滤（Ikaros 自有代码，推荐的第一步）
- 在 `mcp_server.py` 加分组元数据表（工具名 → 组）＋ env `V5_MCP_TOOL_GROUPS=memory,self,...` 过滤 `_NEW_V5_TOOLS` 注册循环。
- 改动点：`core/memory_v5/mcp_server.py`（自有代码，无 hermes 侵入）＋ config（如需拆 server）。
- 价值：把「分组」沉淀为可复用的结构事实，为 Option 3 铺路；本身不提供会话级能力。

### Option 3 — 真正的会话级分组（动 gateway）
- **3a（推荐路径）**：api_server 请求接受白名单化的会话工具集提示（如 `X-Hermes-Toolsets` 头或 body 字段），`_run_agent()` 用它替换固定的平台解析；服务端校验「请求集 ⊆ 平台允许集」，只收窄不扩权。改动点集中在 `gateway/platforms/api_server.py`（~:2807 附近）+ 请求解析 + 校验。
- 3b（更重）：SessionDB 行带 `toolsets`，`/api/sessions` 创建/续聊读取——面更大（session.py + 会话 handler + db 迁移），除非需要持久化到跨会话，否则不选。
- **落地形态**：conversation-tree 客户端已知自身模式——ikaros 模式发 `memory,self,care,vitality,relationship`，hermes 模式发 `memory,project,skill` 或全量。这正好把「模式分化」落到会话级。
- 注意：hermes 是 upstream 代码，**任何 hermes 侧修改必须走现有补丁管线**（`patches/hermes/` + `bin/hermes-update-and-patch.py`，见 `docs/hermes-ikaros-patches.md`），不能直接改源码。

## 风险评估

| 风险 | 等级 | 说明 |
|---|---|---|
| **可见性收紧 ≠ 执行阻断** | 高 | `registry.dispatch()` 只查注册表，不校验会话可见列表；幻觉调用未列出工具仍会执行。方案必须配套 dispatch 侧闸门（或 `agent.disabled_toolsets` 级全局减扣），否则分组只省 token 不省行为面 |
| 模式选错组 → 工具缺失 | 中 | 如 hermes 模式漏掉 `memory` 组，会话内 `v5_memory_search` 不可见，模型退化成无记忆对话；需在 conversation-tree 侧对两组模式各做一次冒烟（见验证项） |
| MCP 子进程膨胀（Option 1b/2 多 server） | 中 | 每拆一个 server = 一个 python + FastMCP + V5 store 栈子进程；建议上限 2–3 个 |
| 新工具静默不可见 | 低 | include glob 按前缀匹配：新组（如未来 `v5_family_*`）不在任何 glob 内 → 默认不可见；需约定「新工具默认全组可见」或 glob 评审 |
| schema 缓存陈旧 | 低 | gateway 懒注册走 schema 指纹缓存（`_lazy_server_configs`）；改 `mcp_server.py` 后需重启 gateway 或用 TUI `reload_mcp` 刷新 |
| toolset 缓存膨胀 | 低 | `_tool_defs_cache` 按 `frozenset(enabled_toolsets)` 键控、上限 8 条 LRU 淘汰；会话级组合多时命中率下降但不会泄漏 |
| hermes 升级冲突（Option 3） | 中 | api_server 是 upstream 高频改动区；靠补丁管线缓解，升级后需回归 `/v1/chat/completions` 与 `/api/sessions/*/chat` |

## 推荐（待拍板）

1. **先做 Option 2**（mcp_server.py 分组表 + env 过滤，纯 Ikaros 代码，零 hermes 侵入，成本半天内）——把分组事实沉淀下来。
2. **再做 Option 1b 的 2–3 拆**（如 `ikaros-v5-core`〔memory+self〕 / `ikaros-v5-persona`〔care+vitality+relationship〕 / `ikaros-v5-work`〔skill+project〕），用 `platform_toolsets.api_server` allowlist 静态收紧——**如果**当前 token 开销实测（gateway 请求日志）值得。
3. **只有「ikaros/hermes 模式分化」确实是痛点时**才上 Option 3a（会话级工具集提示 + dispatch 闸门），且必须走补丁管线。预估：api_server 请求解析 + 白名单校验 + dispatch 闸门 + 一份补丁 spec，中量工时。

**决策门**：先确认 conversation-tree 之外是否还有 api_server 消费方、以及 hermes 模式下模型实际调用 `v5_*` 的频率分布（可从 gateway 工具调用日志统计）。若 90% 调用集中在 memory 组，分组收益 = 砍掉其余 ~27 个 schema（约 15K B/请求），值得；若调用均匀分布，收益趋零。

## 验证项（若拍板实施）

- 每组工具集下 `get_tool_definitions()` 输出与组表一致（单测）
- dispatch 闸门：模型对未列出工具发调用 → 返回明确错误而非执行
- conversation-tree ikaros/hermes 双模式各跑一轮冒烟：人格注入 + `v5_memory_search` 可用 + skill/project 按模式正确出现/消失
- 回归：`/v1/chat/completions`（流式+非流式）、`/api/sessions/*/chat`、降级链（`_READONLY_TOOLS` 不受影响）
- gateway 重启后 schema 缓存刷新、`pytest` 无新增失败

---

## 实施状态（2026-08-11）

- **Option 2 已落地**：`core/memory_v5/mcp_server.py` 加 `_TOOL_GROUPS` 分组表（48 工具 7 组全覆盖）+ `V5_MCP_TOOL_GROUPS` env 过滤（未设置/非法 → fail-open 全量）+ 13 个单测（分组完整性/env 过滤/fail-open）
- **决策门统计完成**（gateway 日志 234 次调用）：memory 65.8% + self 21.8% = 87.6%；+care/vitality/relationship = 96.2%；skill/project 仅 3.8%
- **默认组已配置**：`mcp_servers.ikaros-v5-memory.env.V5_MCP_TOOL_GROUPS = memory,self,care,vitality,relationship`（40 工具，schema 减 ~17%）
- 生效需重启 gateway（schema 指纹缓存）
- Option 3a（会话级分组 + dispatch 闸门）未做，待模式分化成为痛点
