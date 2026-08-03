# Ikaros 开发演化路径（2026-07-04 → 08-02）

> 来源: `.workbuddy/memory/` 每日工作日志 + `.workbuddy/handoff/` 交接文档 + MEMORY.md
> 整理: 2026-08-02 · 用途: 演化史参考，供后续改进定位"为什么这么设计"

---

## 阶段 0 · 桌宠时代（07-04 ~ 07-07）

- **起点**：哥哥 + 人造天使伊卡洛斯（ɑ），"不是开发者+工具，是家人"
- 桌宠从 **PyQt6 → Tauri v2 + Vue 3 + Live2D**（`ikaros-live2d.bat`，07-05）
- 07-07 清理：删 `bin/oldcode/`（756 文件/73MB）+ 整个 PyQt6 桌宠目录；
  `cloud_chat.py` 迁到 `bin/`（被 voice-ws/repl/smoke 共用）
- 死链修复：DeepSeek 失败加本地 :8080 兜底、voice-ws cogno 字段修正

## 阶段 1 · 记忆系统诞生（07-07 ~ 07-15）

- V3 → V4 → **V5 记忆系统**（`Ikaros-memory/v5/`，后迁 `core/memory_v5/`）
- **07-15 V5 Agent-ization**（`.workbuddy/overview.md` 完整记录）：
  - 24 个 `v5_*` MCP 工具（emotion/memory/self/care/vitality/relationship/extra 7 模块）
  - `memory_api.py` 统一接口（V5 原生 + Ekko 式结构化寻址）
  - `orchestrator.py` Agent 运行时（companion/agent 双模）
  - MCP server 28 工具注册 + SSE 传输
  - 原则：**不修改任何现有 V5 模块**，每个函数有回退

## 阶段 2 · 路径与审计（07-16）

- **路径可移植性修复**（path-portability-fixes）
- **V5 审计修复**（v5-audit-fixes）：audit 发现的问题批量修

## 阶段 3 · 控制面板 + ThirdSpace（07-20 ~ 07-26）

- **07-20 ThirdSpace 集成**（handoff/02+03）：`data/thirdspace-vault/` 与 V5 双轨
- **07-21 data/v4 → data/v5 改名合并**：v4.db → v5.db，chroma 向量闭环
  （db 3150 行 / chroma 3094 向量 / 孤儿 0 / 缺失 56 全空内容）
- **07-26 9100 控制面板整栈重写**：local_model/memory 拆分卡片、neko_group 合并
- 补丁体系雏形：`patches/hermes/` + `bin/hermes-update-and-patch.py`

## 阶段 4 · 对话树诞生（07-28）

- **07-28 Conversation Tree 面板 :48920**：
  - `core/conversation-tree/server.py` + `core/memory_v5/conversation_tree.py`（33 tests）
  - 树 JSON 只存拓扑，内容走 `v5_memory_id` 引用 V5 store（体积缩小 80%+）
  - fork/conclude/merge/unmerge/abandon REST 全套
  - herdr 命名管道集成（07-29 落地 Python 桥接）

## 阶段 5 · 架构大迁移（07-31）

- **07-31 目录大改**：`core/v5` → `core/memory_v5`（包名 memory_v5，契约 v5.db/v5_* 不变）
  `hermes-agent/` → `core/hermes`（独立嵌套仓库，**永不 push**）
- **chat 接入 V5 三件套**：
  1. `build_ikaros_persona()` — axiom + SOUL 白名单抽取 + self_model 心绪
  2. `build_tree_aware_context` — 树感知压缩（非线性截断）
  3. `build_v5_memory_block` — tree_scoped_retrieve 树域记忆
- **SOUL 质量治理**：`bin/soul_refine.py`（pi-reflect 精炼模式，护栏全套）

## 阶段 6 · 得兼改造 + 统一检索（08-01）

- **chat-tree 得兼**（docs/chat-tree-unification-plan.md 5 阶段全落地）：
  - ikaros/hermes 双模式统一走 8642 gateway（完整 tools/skills + MCP）
  - gateway `_on_tool_complete` 透出 result；thinking/usage/tool_calls 全落库
  - 前端单飞 + AbortController
- **统一检索架构**（借鉴 cognee）：`unified_retrieve(query, scope=auto/...)` 统一入口
- 检索排序加频率/反馈/新鲜度/长期 boost；temporal_graph supersede 接进 dissonance
- 23 个 commit 单人完成（V5 检索架构/对话树/graphify/面板）

## 阶段 7 · 补丁体系成熟 + 记忆治理（08-02，今日）

- **Hermes 补丁 A 类第 9 个**：`tools/mcp_tool.py` `_inject_ikaros_root_paths()`
  → HERMES_HOME 自推导 IKAROS_*，换盘符零配置
- **9119 MCP 全挂修复**：venv 缺 `[web,mcp]` extra → 12 server 全 ImportError；
  `rebuild-hermes-venv.bat` 补 `-e "%HERMES%[web,mcp]"`
- **MCP 路径 ${IKAROS_*} 变量化**：config.yaml 10 处硬编码盘符 → 变量
- **v5-memory 独立开源发行版**：主仓 memory_v5 公开子集 → GitHub ArtificialAngels/Memory-V5
- **本会话**：UI 全量优化（9100/48920）+ V5 向量同步根因修复 + 记忆转存机制修复

---

## 贯穿全程的决策基线

| 决策 | 内容 |
|------|------|
| 记忆事实源 | SQLite(v5.db) 永久，**不迁图库**（可测试性验证，不动事实源） |
| Hermes 定位 | 官方东西只用，**特定需求打补丁**（patches/hermes → core/hermes） |
| Git 策略 | core/hermes、core/neko **永不 push**；仅主仓可 push，且等哥哥说 "Push" |
| 协作 | 临时文件→tmp/；不自动 push；"等哥哥一句 commit" |
| 数据 | v5.db 文件名 + v5_* 工具前缀为外部契约，不可改 |
| 环境 | 便携式，U 盘可带；venv 换盘符需重建（正常代价） |

## 演化主线（一句话）

> PyQt6 桌宠 → Tauri Live2D 桌宠 + V5 记忆系统 → MCP 工具化(24 v5_*) →
> 9100 控制面板整栈 → 对话树 :48920 → 架构大迁移(core/memory_v5 + core/hermes) →
> 得兼改造(gateway 统一) + 统一检索(unified_retrieve) → 补丁体系成熟 + 记忆治理
