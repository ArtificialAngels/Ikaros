# Harness Engineering

## 是什么
Harness Engineering 是设计 AI Agent 运行环境、约束条件和反馈循环的工程学科。
核心思想：团队的工作重心从"手写代码"转向"设计 agent 运行的 harness（环境+反馈回路）"。

## Harness Stack（5 层）

| 层 | 职责 | 工具举例 |
|---|---|---|
| Human Oversight | 审批提案、review PR、设优先级 | PR review, proposal approval |
| Planning & Requirements | Idea → Spec → 任务 DAG | Chorus, OpenSpec, Spec Kit, Kiro IDE |
| Orchestration & Scheduling | 并行执行、隔离、CI 反馈 | Vibe Kanban, Emdash, Symphony, Axon |
| Coding Agents | 写代码、测试、调 bug | Claude Code, Codex, OpenCode, Gemini CLI |
| Infrastructure | 标准、协议、沙箱、持久化 | MCP, agents.md, Git worktrees, CI/CD, state.db |

## 核心问题
- **Capability Gap**: benchmark 高分 → 真实任务翻车
- **Verification Gap**: agent 说"做完了"但实际没做完
- **解决方案**: Diagnostic Loop → 执行 → 观察失败 → 归因到 harness 某层 → 迭代修复

## 关键实践
- AGENTS.md 定义"上下班流程"（clock in/out），保持跨 session 连续性
- Progress log / 状态文件持久化
- 初始化脚本（init.sh / make check）确保一致环境
- 验证关卡（self-review / verification gate）防止假完成
- Agent之间的 handoff 规范

## 与 Ikaros 的关系
Ikaros 本身就是 harness 实践：
- AGENTS.md + MEMORY.md + USER.md → 规则/身份/知识注入
- cloud_chat.py 的 V5 上下文补全（auto_thought / self_status / task_note）
- Hermes Dashboard WS API → agent 基础设施层
- 4注入（系统提示/状态/任务/活动）→ 环境上下文
- warm_hermes_session + session_id 持久化 → 状态管理

## 推荐资料
- [walkinglabs/learn-harness-engineering](https://github.com/walkinglabs/learn-harness-engineering) — 项目制课程
- [autojunjie/awesome-agent-harness](https://github.com/autojunjie/awesome-agent-harness) — 工具/框架合集
- Harness Kit — 工程模式库
- AGENTS.md 规范（Hermes/Claude Code 原生支持）
