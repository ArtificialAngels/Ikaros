# docs/ — Ikaros 文档中心

> **目标读者**：AI Agent（onboarding）+ 人类（少量保留项）
> **维护原则**：本目录只放 agent **必须立刻看**的骨架文档；专题深度在
> `archive/decision-history/`，按需下钻；git commit log 是时间线真相源。
> **不要**：把历史决策报告 / 选型过程 / 灵感清单留在本目录——它们应进 archive。

---

## Agent onboarding 5 步

1. 读根目录 `AGENTS.md`（契约入口，**保持 ≤200 行**）
2. 读 `docs/ARCHITECTURE.md`（架构骨架 ~600 行，8 章）
3. 读 `docs/architecture-post-dsh.md`（dsh 时代 5 分钟总览，**最有价值**）
4. 按 `git log --oneline -30` 看最近 30 commit 了解时间线
5. 涉及专题细节 → 下钻 `archive/decision-history/<name>.md`

> **Agent 私有笔记**：`tmp/ARCHITECTURE.local.md`（gitignored，不推送）。
> 每个 agent 进来**首次**应主动扫描 git log + 代码生成/更新本文件。
> 维护原则见 `tmp/ARCHITECTURE.local.md` 头部说明。

---

## 目录导航

### Agent onboarding 必读（4 份）

| 文档 | 行数 | 角色 |
|------|-----|------|
| [`../AGENTS.md`](../AGENTS.md) | ≤200 | 契约入口（端口/启动/DO NOT/指针） |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | ~600 | 架构骨架（8 章） |
| [`architecture-post-dsh.md`](architecture-post-dsh.md) | 379 | dsh 时代 5 分钟总览 |
| [`README.md`](README.md) | 本文件 | 目录索引 |

### 长期保留（5 份）

| 文档 | 行数 | 角色 |
|------|-----|------|
| [`observability.md`](observability.md) | 91 | 日志/可观测性 |
| [`naming.md`](naming.md) | — | （已 archive，见下） |

### 历史归档（按需下钻）

- [`archive/decision-history/`](archive/decision-history/) — 20 份专题深度
  （启动器 / 组件契约 / dsh 接入 / V5 架构 / 命名 / 安全 / 对话树 DSL /
   MCP 合并 / 退役组件 / 灵感清单）
- [`archive/`](archive/) — 25 份 8-14 之前的更老报告（hermes/neko/pi/herdr/
  omp 退役 + 9100/9119 控制面板 + dsh-base 审计 + bridge 设计等）

### 子目录（脚本注释归档）

- [`scripts/`](scripts/) — 7 份脚本自身的注释归档（按 README 映射）
- [`hermes-plans/`](hermes-plans/) — 5 份 2026-08-19 4 线并行方案历史
- [`examples/skills/`](examples/skills/) — 1 份 skill 示例说明
- [`research/`](research/) — 2 份人格提示工程研究

---

## 维护规则（agent 必读）

### DO

- 改代码时**主动**评估"是否影响 ARCHITECTURE.md §1-§6 / AGENTS.md 端口表"
- 改完后**手动**修订（不自动同步——见 §6.6）
- 专题深度评审先放 `archive/decision-history/`，不放根目录

### DO NOT

- 不要新增 markdown 到 `docs/` 根目录——除非它属于 onboarding 必读
- 不要重写 `architecture-post-dsh.md`（它已稳定，5 分钟总览）
- 不要 commit **未**附 `docs:` 前缀的架构变更（commit message 守门）
- 不要把灵感清单 / 选型过程 / 退役组件报告留根目录（进 archive/）

---

## 历史变更记录

- **2026-09-04**（本轮）：docs/ 根目录 24 份 → 4 份；20 份进
  `archive/decision-history/`；ARCHITECTURE.md 加 §7 §8 索引与 onboarding 路径；
  docs/README.md 重写。
- **2026-08-27**：docs/scripts/ 陈旧脚本文档归档（51 文件 / 1286 行）。
- **2026-08-19**：13 份历史报告进 archive/（N.E.K.O / Hermes / 对话链路 / 演化史等）。
- **2026-08-12**：删 4 份过时文档（mem0/Qdrant 旧时代 / 镜像 / 16 资源链接等）。
