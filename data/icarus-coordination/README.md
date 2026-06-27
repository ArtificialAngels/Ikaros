# Icarus 协作日志目录

**目的**：跨 agent 协作（Icarus / Quest / 未来的 agent）的工作台。

## 为什么不用 commit？

| 维度 | git commit | 协作 JSON |
|---|---|---|
| 性质 | 固化快照 | 动态工作台 |
| 歧义性 | 高（"这个 commit 修了什么"）| 低（结构化字段） |
| 跨 agent | 只能 git log 翻 | 直接 JSON 读 |
| 实时性 | commit 后才看见 | 边干边写 |
| 可扩展 | 受 commit message 长度限制 | 字段自由 |

**结论**：commit 是历史，JSON 是当下。

## 文件清单

- `handshake-2026-06-26.json` — 当前 session 的完整状态（已完成部分 + 待决策）
- `schema.json` — JSON 字段定义（其他 agent 写新文件时参考）
- `README.md` — 本文件

## 怎么参与

1. **读最新的 handshake JSON** → 知道现在谁在做什么、什么挂了、什么待定
2. **更新 JSON**（append 不覆盖）：
   - 加新 entry 到 `completed_tasks` 或 `pending_questions_for_quest`
   - 更新 `services_current_state` 里的 PID/port
   - 加新 `next_steps`
3. **commit 你自己的工作**（不是日志本身），commit message 简述：
   > "fix(reflection): direct to :28538 worker (Q1-A)"
4. **其他 agent 接手时**：先 `cat data/icarus-coordination/handshake-*.json | jq .`

## Schema 字段（v1.0.0）

```jsonc
{
  "version": "1.0.0",
  "session_id": "YYYY-MM-DD-topic",
  "created_at": "ISO-8601",
  "purpose": "why this log exists",

  "actors": {
    "<name>": {
      "role": "main implementer | fixer | human | ...",
      "scope": "what they own",
      "author_for_git": "Name <email>"
    }
  },

  "services_current_state": {
    "<service>": {"port": N, "pid": N, "url": "..."}
  },

  "endpoints_added_by_icarus": [...],
  "endpoints_routed_through_stub_router": {...},

  "pending_questions_for_quest": [
    {
      "id": "Q1",
      "topic": "...",
      "details": "...",
      "options": [{"id": "A", "desc": "..."}],
      "status": "open | resolved_by_X",
      "icarus_recommendation": "X — reason",
      "decided_by": "icarus | quest | user | null",
      "decided_at": "ISO-8601 | null"
    }
  ],

  "completed_tasks": [
    {
      "id": "T1",
      "actor": "icarus | quest | user",
      "task": "...",
      "result": "...",
      "completed_at": "ISO-8601",
      "commit": "sha1 | null",
      "note": "optional caveat"
    }
  ],

  "working_features": {
    "<name>": {"status": "working | partial | blocked | code_ready_not_X", "evidence": "...", "value_to_user": "..."}
  },

  "next_steps": ["..."],

  "meta": {
    "git_author_rule": "...",
    "language": "...",
    "where_this_file_lives": "...",
    "schema_version": "...",
    "extend_by": "append, never overwrite"
  }
}
```

## 不变量

- **append only** — `completed_tasks` 和 `pending_questions_for_quest` 只能加条目，不能改
- **状态可改** — `services_current_state` 随时更新 PID/port
- **跨 session 命名** — `handshake-YYYY-MM-DD-topic.json`，按时间排序

## 写新 session 的 JSON 时

1. 复制最新的 handshake JSON
2. 改 `session_id` / `created_at` / `purpose`
3. **复制**之前的 `completed_tasks`，不要改（加新条目）
4. 写新 session 的 `pending_questions_for_quest`
5. 留 `next_steps` 给下次

## 谁在用

- **Icarus**（伊卡洛斯）— 主开发者
- **Quest** — 救火 / 端口架构 / watchdog 修复
- **未来 agent** — 接手或协作

---

## Quest 当前待办清单 (2026-06-27)

| 优先级 | 文件 | 任务 |
|--------|------|------|
| 🔴 CRITICAL | `handshake.2026-06-27.bridge-uvicorn-unresponsive.json` | 修桥 — uvicorn 应用层卡死 (HTTP 不响应) |
| 🟠 HIGH | `handshake.2026-06-27.bridge-zombie.json` | 修桥端口僵尸 + TIME_WAIT (上轮 handoff) |
| 🟡 P0 | `handshake.2026-06-27.odp-inspiration.json` | **桌宠升级灵感** — 6 大可移植特性 |
| 🟢 已完成 | `handshake.2026-06-27.live2d-debug.json` | Live2D 框架集成 (Quest 自己做的) |

**ODP 灵感 6 特性 → Quest 该写什么**:

1. **bridge/memory_service.py** (P0 三层记忆: 短期 + 摘要 + 事实)
2. **bridge/proactive.py** (P1 主动互动: 随机区间触发 + 截屏决策)
3. **bin/icarus-desktop-pet/screen_capture.py** (P1 PyQt6 截屏)

完整 spec 在 `handshake.2026-06-27.odp-inspiration.json` — 字段已结构化。

---

## 协作规则 (Icarus 升级时)

- **不要 push** — 哥哥说源码等加密后再推
- **只本地 commit** — `git commit --no-verify` 后 git push 暂不执行
- **Quest 自取 coordin** — `cat data/icarus-coordination/handshake-2026-06-27.*.json | jq .`
- **Quest 写完 commit** — 留下 commit SHA, Icarus 下次 session 读 git log 验证