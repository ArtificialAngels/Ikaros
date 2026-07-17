# docs/ — Ikaros 文档中心

本目录集中存放项目的说明文档、规格、研究与脚本注释归档。
脚本自身的注释正文已抽离到 `scripts/`（见 `scripts/README.md`）。

## 目录导航

### 架构与总览
- [v5-architecture-review.md](v5-architecture-review.md) — V5 记忆系统架构评审
- [hermes-agent-full-survey.md](hermes-agent-full-survey.md) — Hermes Agent 全量调研
- [harness-engineering-notes.md](harness-engineering-notes.md) — 运行链 / harness 工程笔记

### V5 规格与迁移
- [v5.2-preprocess-factory-spec.md](v5.2-preprocess-factory-spec.md) — V5.2 预处理工厂规格（节奏/记忆/摘要/画像/情感）
- [p2-payload-schema-migration.md](p2-payload-schema-migration.md) — P2 载荷 schema 迁移说明

### 智能体规则 / 身份
- [agent-rules.md](agent-rules.md) — 智能体规则说明
- [agent-rules.yaml](agent-rules.yaml) — 智能体规则配置
- [ikaros-soul.md](ikaros-soul.md) — Ikaros 人格 / soul 文档

### 研究
- [research/deep-personality-prompt-engineering.md](research/deep-personality-prompt-engineering.md) — 深度人格提示工程研究
- [research/anti-ai-tone-prompt-research.md](research/anti-ai-tone-prompt-research.md) — 反 AI 腔调提示研究

### 资源 / 镜像 / 上游
- [16-资源链接.md](16-资源链接.md) — 资源链接汇总
- [附录-镜像与代理.md](附录-镜像与代理.md) — 镜像与代理附录
- [upstream-candidates.md](upstream-candidates.md) — 上游候选方案

### 示例
- [examples/skills/README.md](examples/skills/README.md) — 技能示例说明
- `examples/skills/note.py`、`examples/skills/weather.py` — 示例技能实现

### 资产（演示图，已归档）
- `assets/对话演示.png`、`assets/Qwen3.6-35B-A3B-UD-Q6_K.gguf测试.png`

### 脚本注释归档（自动生成）
- [scripts/README.md](scripts/README.md) — 归档约定与批次进度（入口）
- `scripts/bin/` — `bin/*.bat` / `bin/*.py` 运维与控制脚本说明
- `scripts/Ikaros-environment/` — 环境配置脚本说明（init / ikaros-env / detect-root / validate-paths）
- `scripts/Ikaros-memory/` — 记忆核心库说明（根 + v5/*，含内联注释摘录）

## 维护约定
- 新增脚本说明：按 `scripts/README.md` 的映射规则放到 `scripts/<相对路径>.md`。
- 文档均简体中文；脚本内仅保留必要安全提示 + 一行指针（`.bat`/`.ps1` 用纯 ASCII 英文指针）。
- 不放 secrets；路径优先走 `Ikaros-environment` 注册的环境变量。
