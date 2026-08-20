# docs/ — Ikaros 文档中心

本目录集中存放项目的说明文档、规格、研究与脚本注释归档。
脚本自身的注释正文已抽离到 `scripts/`（见 `scripts/README.md`）。

## 目录导航

### 架构与总览（按更新日期排列）

| 文档 | 日期 | 内容 | 规模 |
|------|------|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 2026-08-19 | Ikaros 全栈架构（分层/端口/路径/数据流/规则，dsh 底座） | 40KB |
| [naming.md](naming.md) | — | 命名规则（目录/包/端口/变量） | — |
| [herdr-integration-design.md](herdr-integration-design.md) | 2026-08-10 | herdr coding-agent 多路复用器接入设计（含 omp/pi agent §omp） | — |
| [SECURITY.md](SECURITY.md) | — | 安全说明 | — |
| [observability.md](observability.md) | — | 可观测性 / 日志 | — |
| [module-dependency-map.html](module-dependency-map.html) | 2026-07-27 | 模块依赖关系图（交互式 SVG） | — |
| [thirdspace-integration.md](thirdspace-integration.md) | 2026-07-20 | ThirdSpace Vault 集成指南 | 4KB |

> **架构图三件套**（交互式 HTML，放 `docs/`）：`module-dependency-map.html`（模块依赖关系）/ `architecture-overview.html`（架构全景）/ `folder-tree.html`（文件夹层级）。全部基于 2026-07-27 真实扫描生成（已过时，仅作历史参考）。
>
> **清理记录**（2026-08-12）：删除 4 份过时文档（`p2-payload-schema-migration.md`（mem0/Qdrant 旧时代）、`upstream-candidates.md`、`附录-镜像与代理.md`、`16-资源链接.md`），镜像/代理配置以 `bin/ikaros-env.sh/.bat` 为准。
>
> **归档记录**（2026-08-19）：13 份历史文档移入 `archive/`（N.E.K.O / Hermes 退役组件文档、对话链路历史报告、演化史、项目评审、UI 优化日志、Session 机制分析、架构清理方案）。工作引擎 = dsh :3080（2026-08-18 起）。

### 对话树 / 对话流
- [conversation-tree-cards.md](conversation-tree-cards.md) — 对话树卡片系统（Artifact Deck 万用工具卡组 :::card DSL）

### V5 规格与迁移
- [v5.2-preprocess-factory-spec.md](v5.2-preprocess-factory-spec.md) — V5.2 预处理工厂规格（节奏/记忆/摘要/画像/情感）
- [v5-memory-evolution-plan.md](v5-memory-evolution-plan.md) — V5 记忆演化计划
- [v5-context-compression.md](v5-context-compression.md) — 上下文压缩与检索增强层（token_compressor / temporal_graph）
- [v5-architecture-convergence.md](v5-architecture-convergence.md) — V5 架构收敛
- [v5-memory-evolve / dsh-memory-evolve 对比](v5-vs-dsh-memory-evolve-20260819.md) — 2026-08-19
- [memory_v5-analysis-20260819.md](memory_v5-analysis-20260819.md) — memory_v5 全量分析（2026-08-19）

### 智能体规则 / 身份
- [agent-rules.md](agent-rules.md) — 智能体规则说明
- [agent-rules.yaml](agent-rules.yaml) — 智能体规则配置
- > 身份主文档已迁移到 `.workbuddy/SOUL.md`（BOOTSTRAP 流程管理）

### 研究 / 面板
- [research/deep-personality-prompt-engineering.md](research/deep-personality-prompt-engineering.md) — 深度人格提示工程研究
- [research/anti-ai-tone-prompt-research.md](research/anti-ai-tone-prompt-research.md) — 反 AI 腔调提示研究
- [omnipanel-research.md](omnipanel-research.md) — 全功能面板研究
- [omnipanel-inspiration.md](omnipanel-inspiration.md) — 全功能面板灵感

### 底座与集成（dsh 时代）
- [ikaros-dsh-plugin-architecture.md](ikaros-dsh-plugin-architecture.md) — dsh 插件架构（overlay + ikaros-memory 插件）
- [hermes-retirement-inventory.md](hermes-retirement-inventory.md) — hermes / N.E.K.O / 9100 面板退役清单（2026-08-18）
- [herdr-integration-design.md](herdr-integration-design.md) — herdr 多路复用器（见架构总览）

### 示例
- [examples/skills/README.md](examples/skills/README.md) — 技能示例说明
- `examples/skills/note.py`、`examples/skills/weather.py` — 示例技能实现

### 资产（演示图，已归档）
- `assets/对话演示.png`、`assets/Qwen3.6-35B-A3B-UD-Q6_K.gguf测试.png`

### 脚本注释归档（自动生成）
- [scripts/README.md](scripts/README.md) — 归档约定与批次进度（入口）
- `scripts/core-env/` — 环境配置脚本说明（init / ikaros-env / detect-root / validate-paths）

### 历史归档
- `archive/` — 24 份历史文档（N.E.K.O / Hermes 退役组件、对话链路报告、演化史等）

## 维护约定
- 新增脚本说明：按 `scripts/README.md` 的映射规则放到 `scripts/<相对路径>.md`。
- 文档均简体中文；脚本内仅保留必要安全提示 + 一行指针（`.bat`/`.ps1` 用纯 ASCII 英文指针）。
- 不放 secrets；路径优先走 `bin/ikaros-env.sh/.bat` 注册的环境变量。
- **架构防漂移规则**：任何触及架构 / 端口 / 组件的 commit，**必须同步** `docs/ARCHITECTURE.md` 与根 `AGENTS.md`，否则其提交信息须带 `docs:` 前缀（如 `docs: 调整 dsh overlay`），以防文档与实现漂移。可用 `python docs/lint.py` 检查残留的旧路径 / 已删文件与端口。
