# docs/ — Ikaros 文档中心

本目录集中存放项目的说明文档、规格、研究与脚本注释归档。
脚本自身的注释正文已抽离到 `scripts/`（见 `scripts/README.md`）。

## 目录导航

### 架构与总览（按更新日期排列）

| 文档 | 日期 | 内容 | 规模 |
|------|------|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 2026-08-12 | Ikaros 全栈架构（分层/端口/路径/数据流/规则） | 40KB |
| [naming.md](naming.md) | — | 命名规则（目录/包/端口/变量） | — |
| [herdr-integration-design.md](herdr-integration-design.md) | 2026-08-10 | herdr coding-agent 多路复用器接入设计（含 omp/pi agent §omp） | — |
| [SECURITY.md](SECURITY.md) | — | 安全说明 | — |
| [observability.md](observability.md) | — | 可观测性 / 日志 | — |
| [module-dependency-map.html](module-dependency-map.html) | 2026-07-27 | 模块依赖关系图（交互式 SVG） | — |
| [neko-deep-analysis.md](neko-deep-analysis.md) | 2026-07-27 | N.E.K.O 模块深度分析（**保持独立，合并 V5 建议已作废**） | 32KB |
| [neko-chat-architecture.md](neko-chat-architecture.md) | 2026-07-25 | N.E.K.O 前端聊天系统架构（记忆/会话/主动搭话前端实现） | 13KB |
| [harness-engineering-notes.md](harness-engineering-notes.md) | 2026-07-15 | 运行链 / harness 工程笔记 | 2KB |
| [hermes-agent-full-survey.md](hermes-agent-full-survey.md) | 2026-07-11 | Hermes Agent 全量调研（注：`data/hermes-agent/` 为用户态目录，已迁移至 `runtime/hermes-agent`） | 10KB |
| [thirdspace-integration.md](thirdspace-integration.md) | 2026-07-20 | ThirdSpace Vault 集成指南 | 4KB |
| [evolution-path-2026-08-02.md](evolution-path-2026-08-02.md) | 2026-08-02 | 开发演化史（07-04 → 08-02，供「为什么这么设计」定位） | — |

> **架构图三件套**（交互式 HTML，放 `docs/`）：`module-dependency-map.html`（模块依赖关系）/ `architecture-overview.html`（架构全景）/ `folder-tree.html`（文件夹层级）。全部基于 2026-07-27 真实扫描生成（已过时，仅作历史参考）。

> **清理记录**（2026-07-24）：删除了 6 份过时/重复文档（`CODEBASE_STRUCTURE.md`、`v5-architecture-review.md`、`v5-architecture-analysis-2026-07-20.md`、`ikaros-architecture-decomposition.md`、`ikaros-agent-standalone-architecture-analysis.md`、`ikaros-soul.md`），合并了 `ikaros-neko-integration.md` 到 `neko-deep-analysis.md` 附录 C，删除了 3 份已移除组件的脚本文档（`ikaros-voice-ws.md`、`hermes-studio.md`、`hermes-desktop.md`）。
>
> **清理记录**（2026-08-12）：删除 4 份过时文档（`p2-payload-schema-migration.md`（mem0/Qdrant 旧时代）、`upstream-candidates.md`、`附录-镜像与代理.md`、`16-资源链接.md`），镜像/代理配置以 `config/hermes.yaml` 与 `bin/ikaros-env.sh/.bat` 为准。

### Hermes 集成与更新（Ikaros 套在 Hermes 之上的解耦 / 维护）
- [hermes-bridge-design.md](hermes-bridge-design.md) — studio 式「0 侵入」包装层设计（bridge :8650 → 纯净 gateway :8642，对话树默认通道）
- [hermes-ikaros-patches.md](hermes-ikaros-patches.md) — Hermes 集成补丁全貌（A/B 类 3-way 重放 + ikaros_v5 外置插件机制 `§6b`）
- [hermes-update-integrity.md](hermes-update-integrity.md) — 更新不冲掉配置/插件的两层安全（EXTERNAL_PLUGIN 机制，runtime/hermes-agent 的 git 操作碰不到外置插件）
- [hermes-tools-scoping.md](hermes-tools-scoping.md) — Hermes 工具范围限定（tools 白名单/作用域）
- [hermes-v5-memory-efficiency-analysis.md](hermes-v5-memory-efficiency-analysis.md) — V5 记忆接入 Hermes 的效率分析（52 → 48 工具口径，2026-08-10 FTS5 兜底修复）
- [ikaros-as-hermes-agent-proposal.md](ikaros-as-hermes-agent-proposal.md) — Ikaros 作为「套在 Hermes 之上的智能体」解耦方案（已实施 + 2026-08-05 验证）
- [ref-hermes-studio-chat.md](ref-hermes-studio-chat.md) — Hermes Studio chat 参考

### 对话树 / 对话流
- [conversation-tree-cards.md](conversation-tree-cards.md) — 对话树卡片系统（Artifact Deck 万用工具卡组 :::card DSL）
- [chat-tree-unification-plan.md](chat-tree-unification-plan.md) — 对话树得兼改造计划（ikaros/hermes 双模式统一走 gateway :8642）
- [conversation-flow-test-report.md](conversation-flow-test-report.md) — 对话链路测试报告
- [conversation-flow-fix-report-2026-07-25.md](conversation-flow-fix-report-2026-07-25.md) — 对话链路 6 根因修复记录
- [conversation-flow-upgrade-plan.md](conversation-flow-upgrade-plan.md) — 对话链路升级计划
- [session-context-analysis.md](session-context-analysis.md) — Session 机制与上下文连贯性分析

### V5 规格与迁移
- [v5.2-preprocess-factory-spec.md](v5.2-preprocess-factory-spec.md) — V5.2 预处理工厂规格（节奏/记忆/摘要/画像/情感）
- [v5-memory-evolution-plan.md](v5-memory-evolution-plan.md) — V5 记忆演化计划
- [v5-context-compression.md](v5-context-compression.md) — 上下文压缩与检索增强层（token_compressor / gated_retrieval / temporal_graph）

### 智能体规则 / 身份
- [agent-rules.md](agent-rules.md) — 智能体规则说明
- [agent-rules.yaml](agent-rules.yaml) — 智能体规则配置
- > 身份主文档已迁移到 `.workbuddy/SOUL.md`（BOOTSTRAP 流程管理）

### 研究 / 评审 / 面板
- [research/deep-personality-prompt-engineering.md](research/deep-personality-prompt-engineering.md) — 深度人格提示工程研究
- [research/anti-ai-tone-prompt-research.md](research/anti-ai-tone-prompt-research.md) — 反 AI 腔调提示研究
- [omnipanel-research.md](omnipanel-research.md) — 全功能面板研究
- [omnipanel-inspiration.md](omnipanel-inspiration.md) — 全功能面板灵感
- [pi-ikaros-evaluation.md](pi-ikaros-evaluation.md) — omp/pi agent 接入评估
- [project-review-2026-08-02.md](project-review-2026-08-02.md) — 项目评审（2026-08-02）
- [ui-optimization-log-2026-08-02.md](ui-optimization-log-2026-08-02.md) — UI 优化日志

### 示例
- [examples/skills/README.md](examples/skills/README.md) — 技能示例说明
- `examples/skills/note.py`、`examples/skills/weather.py` — 示例技能实现

### 资产（演示图，已归档）
- `assets/对话演示.png`、`assets/Qwen3.6-35B-A3B-UD-Q6_K.gguf测试.png`

### 脚本注释归档（自动生成）
- [scripts/README.md](scripts/README.md) — 归档约定与批次进度（入口）
- `scripts/bin/` — `bin/*.bat` / `bin/*.py` 运维与控制脚本说明
- `scripts/core-env/` — 环境配置脚本说明（init / ikaros-env / detect-root / validate-paths）
- `scripts/core-v5/` — 记忆核心库说明（根 + v5/*，含内联注释摘录）

## 维护约定
- 新增脚本说明：按 `scripts/README.md` 的映射规则放到 `scripts/<相对路径>.md`。
- 文档均简体中文；脚本内仅保留必要安全提示 + 一行指针（`.bat`/`.ps1` 用纯 ASCII 英文指针）。
- 不放 secrets；路径优先走 `bin/ikaros-env.sh/.bat` 注册的环境变量。
- **架构防漂移规则**：任何触及架构 / 端口 / 组件的 commit，**必须同步** `docs/ARCHITECTURE.md` 与根 `AGENTS.md`，否则其提交信息须带 `docs:` 前缀（如 `docs: 调整 neko_group 端口`），以防文档与实现漂移。可用 `python docs/lint.py` 检查残留的旧路径 / 已删文件与端口。
