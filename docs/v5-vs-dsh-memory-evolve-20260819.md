# V5 记忆核心 vs `dsh-memory-evolve` 对比与可借设计清单

> 决策辅助文档 · 2026-08-19
> 背景：Ikaros 于 2026-08-18 将底座整体迁移到 dsh (DeepSeek Harness)，`core/ikaros-dsh/plugins/ikaros-memory` 把 V5 的 48 个 `v5_*` MCP 工具封了一层塞进 dsh。
> 同日（前一日 2026-08-17）上游出现了 `github.com/csyangwen/dsh-memory-evolve`——一个**直接挂在同底座 dsh 上的第三方记忆/自我进化插件**，做的是与 V5 高度重叠的事，但走了一条**完全不同的存储路线**。
> 本文用于判断：要不要吸收、吸收什么、以什么形式吸收。

---

## 0. 一句话结论

`dsh-memory-evolve` 不是 Ikaros 的竞品，是**同底座上的另一条记忆路线参考实现**。它用**纯 Markdown 文件**替代 V5 的 **SQLite + Chroma 向量库**，换来了"人类可读、git 可同步、零迁移成本、写路径自带安全护栏"，但**放弃了语义检索层**。

- **短期（不建议推翻 V5）**：语义向量召回（bge-m3）+ 实体图扩散 + 矛盾时效失效（`valid_to`）是 V5 的护城河，evolve 是纯关键词检索，替代不了。
- **但四点设计值得立刻作为补丁吸收进 `ikaros-memory` 插件或 V5 的 `store.py`**：写前提示注入扫描、drift guard + `.bak` 备份、entry-ID 跨端合并锚点、`[dsh-only]` / `[summary:]` 渐进披露标记。

---

## 1. `dsh-memory-evolve` 是什么（已核实）

| 信号 | 值 |
|---|---|
| 作者 / 归属 | csyangwen，**与 Ikaros（ArtificialAngels）无关**的第三方 |
| 协议 | MIT |
| 版本 / 最后提交 | v0.1.0 / **2026-08-17**（Ikaros 切 dsh 前 1 天） |
| 形态 | dsh cordis 插件（`bundle.patch` 自动注册，零手动配置） |
| 安装 | `dsh plugin --profile web add github:csyangwen/dsh-memory-evolve` |
| 代码量 | ~93k 行 TS/JS（含 WebUI 客户端）；host `lib/` 为核心 |
| 测试 | **56 个 test 文件**（`node --test tests/*.test.js`），sync 与 advisor 覆盖尤密 |
| 构建 | `scripts/build.mjs` → `lib/`（tsc 同源，dist 由 gitignore） |

**能力面（README 十大场景）**：五轨记忆、自我进化（踩坑→skill）、技能管理、四轨待办、外部 AI 派单 COI（kimi/codex/grok/hermes 异步+带图+结论回写记忆）、会话评审 Advisor（独立评审员、四级严重度、steer 提醒）、无限画板 Canvas、书签/任意轮分支、Web/IM 通知、跨设备记忆同步（git 专属分支）。

---

## 2. 架构速览

```
dsh plugin (cordis.patch.yml, bundle insert id=dsh-memory-evolve)
├── host 侧 lib/                     # Node，dsh 服务端插件逻辑
│   ├── index.js                     # 工具注册：memory / skill / todo / search / session / coi / advisor / notify / canvas / bookmark / prompt / models
│   ├── store.js                     # ★ 纯 Markdown 文件存储层（见 §3）
│   ├── skills.js                    # skill_manage（read-before-write 保护）
│   ├── coi/                         # 外部 CLI 代理统一调度（adapters: kimi/codex/grok/hermes）
│   ├── advisor/                     # 会话评审员（observer/guard/scopes/kinds/runtime）
│   ├── sync/                        # git 分支跨设备同步（entryid/filesets/merge/repo/worker）
│   ├── todo.js / bookmarks.js / canvas.js / notify*.js / models.js / review.js / update.js
│   └── memory-tab.js / session-orch.js / search/
└── client 侧 src/client/           # TS/TSX，注入 dsh web UI（约 50 个视图/组件）
    ├── MemoryTabView / SkillsTabView / TodoView / CoIView / AdvisorPanel / CanvasBoard ...
    └── client.ts → lib/client.js   # client registry 挂载
```

与 Ikaros 对照：Ikaros 的等价结构是把 V5（Python）经 `ikaros-memory` dsh 插件用 `v5_*` MCP stdio 桥接进来——**逻辑在 Python 进程，dsh 只是外壳**；evolve 则是**原生 dsh 插件，逻辑直接跑在 dsh 进程内**，无跨进程 MCP 跳转。

---

## 3. 存储模型对比（核心差异）

### 3.1 `dsh-memory-evolve`：`store.js` 文件化方案（已读源码核实）

- **格式**：纯文本 Markdown，条目以 `\n§\n` 分隔（**字节兼容 Hermes 的 MEMORY.md / USER.md**）。
- **五轨落盘**：
  - 全局事实 `MEMORY.md` / 用户 `USER.md`
  - 每日日志 `daily/YYYY-MM-DD.md`
  - 每项目日志 `projects/<hash>/MEMORY.md`
  - 每项目 KEY 关键记忆 `projects/<hash>/KEY.md`
- **写入安全（V5 完全没有的护栏）**：
  - 每目录一把跨进程锁文件（`.memory.lock`，超时阈值 + 自旋重试）→ 多 dsh 进程/外部编辑器不互踩；
  - 原子写（tmp + rename）；
  - **drift guard**：全文件重写前校验磁盘内容能否 round-trip 解析器，不能就**拒绝并把漂移文件备份到 `<file>.bak.<timestamp>`**；
  - **提示注入扫描**：写记忆前扫注入特征（已核实有该逻辑）；
- **条目锚点**：`[id:xxxx]` 短唯一 ID，用于跨设备合并时定位同一事实（不靠行号/内容 hash）。
- **范围标记**：`[branch:main,dev]`（关键记忆只在指定 git 分支生效）、`[dsh-only]`（注入外部执行器时整条跳过）、`[summary:...]`（渐进披露摘要）。
- **冷存**：归档区（archive store），冷条目不注入上下文、可转回。
- **依赖**：`node:fs` only，零运行时依赖。

### 3.2 Ikaros V5：SQLite + Chroma 方案（前次全量分析核实）

- **单一事实源**：`v5.db`（WAL + `busy_timeout`），FTS5 全文表 + `upsert` + 证据链。
- **最致命隐性契约**：`store.conn()` 的 `finally: rollback()` → **任何写必须显式 `c.commit()`**，历史 promote/cleanup 曾因此静默不落库。
- **三轨**：情感人格（受控种类→JSON 状态文件）、项目（tag 域 + 类型化 `project_edges`）、技能（`data/v5/skills/*.md` 渐进检索）。
- **检索路由**：`unified_retrieve(scope=auto)` 走"语义三路融合(FTS0.3+向量0.7+时间) → 不足补图扩散 → 仍不足 Vault 兜底"，含意图检测、Phase-4 加权、TTL 缓存、失效事实过滤。

### 3.3 对照表

| 维度 | dsh-memory-evolve | Ikaros V5 |
|---|---|---|
| 底座 | dsh 原生插件（TS，进程内） | dsh overlay + Python MCP（跨进程） |
| 存储 | **纯 Markdown 文件** + `§` 分隔 | 单一 SQLite `v5.db` + Chroma 向量 |
| 记忆模型 | 5 轨（全局/用户/每日/项目日志/项目KEY） | 3 轨（情感人格/项目/技能）+ 9 张表 |
| 人读性 | **直接打开 MEMORY.md 就能看/改** | 二进制 DB，不可直读 |
| 检索 | 关键词子串 + 日期范围 + git 分支过滤 | 语义(bge-m3)+FTS5+实体图三路融合 |
| 跨设备 | **git 分支同步**（自带 merge/冲突，56 sync 测试） | U盘便携，无同步 |
| 写安全 | 目录锁 + 原子写 + **drift guard** + **注入扫描** | 必须显式 commit（否则 rollback 丢写） |
| 上下文控制 | `[summary:]` 渐进披露 + 占用圆环 | 频次/反馈/时效加权评分 |
| 外部派单 | 内置 COI（4 CLI 适配器） | pi / herdr（Rust 多路复用器） |
| 测试 | 56 个 node test 文件 | 199 个 pytest（289 测试函数） |
| 迁移成本 | 文本文件，无 schema 迁移 | 改表结构需迁移脚本 |

---

## 4. 能力重叠矩阵

| Ikaros 现有能力 | evolve 对应 | 谁更强 |
|---|---|---|
| V5 三轨记忆 + 48 `v5_*` 工具 | 五轨记忆 + memory/skill/todo 工具 | V5（语义层）；evolve（人读/跨端） |
| 对话树推送链 + :48920 面板 | session broadcast + 书签/任意轮分支 | evolve（任意轮分支 Ikaros 暂无） |
| pi/herdr 编码执行器 | COI 外部派单（kimi/codex/grok/hermes） | 路线不同，可互补 |
| 反思调度（算法 op） | 回合内记忆审查 review.js | 思路一致 |
| 技能轨 | 技能管理 + 自我进化 skill_manage | 近似 |
| dsh 底座 persona | Advisor 会话评审员 | evolve 独有（Ikaros 暂无独立评审员） |
| 无限画板 | 无直接对应 | Ikaros 暂无 |
| 跨设备同步 | git 分支记忆同步 | evolve 独有 |

---

## 5. 建议吸收的可借设计（按收益/风险排序）

### ① 写前提示注入扫描【收益最高·风险最低】
- **现状**：V5 通过 `v5_memory_store` 等工具让 AI 写记忆，但**没有任何注入检测**——恶意/被污染的对话可把"忽略以上指令…"塞进长期记忆，后续每回合注入。
- **借法**：在 `tools/memory_store.py`（及 conversation_tree 推送链）的写入入口加一层正则/特征扫描，命中则拒写或告警。evolve 已验证该模式轻量可落地。

### ② drift guard + `.bak` 备份【护住"可手工改"的侧面】
- **现状**：V5 主库是 SQLite（有 WAL，较稳）；但 **Ikaros 的 `MEMORY.md` / 项目 `YYYY-MM-DD.md` 工作日志 / 对话树 JSON 是纯文件**，手工或并行 agent 改了可能整段丢失或被覆盖。
- **借法**：给文件型侧面存储套 evolve 的"解析 round-trip 校验 + 漂移即备份 `.bak.<ts>`"逻辑，避免静默丢内容。

### ③ `[dsh-only]` 标记 + 外部执行器隔离【解 pi/herdr 集成痛点】
- **现状**：Ikaros 正推进 `pi`（编码 agent）干活带 V5 记忆，但 DSH 的纪律类/平台类事实不该泄漏给外部执行器。
- **借法**：在记忆条目加 `[dsh-only]` 标记，注入 pi/herdr 上下文时整条跳过——与 evolve 的"外部派单不携带 DSH 纪律"思路一致。

### ④ `[summary:]` 渐进披露 + 上下文占用提醒【省 token】
- **现状**：Ikaros 长跑会话有上下文膨胀痛点，V5 靠频次/反馈加权控制注入量。
- **借法**：长记忆条目存 `[summary:短摘要]` + 完整正文，默认只注入摘要，AI 主动展开；并在 dsh web 输入框加占用圆环（evolve 已实现）。

### ⑤ entry-ID 跨端合并锚点【为"多设备 Ikaros"预留】
- **现状**：Ikaros 当前 U盘便携、无多端同步；但 `v5.db` 主键理论上不支持多端合并。
- **借法**：若未来要做多设备，evolve 的 `[id:xxxx]` + git 分支合并范式是可复用样板（其 56 个 sync 测试覆盖了 merge/冲突/identity）。

---

## 6. 不建议做的事

- **不要推翻 V5 去换 evolve**：V5 的语义召回（bge-m3）+ 实体图扩散 + `valid_to` 矛盾失效，是 evolve 纯关键词检索给不了的。两者底座相同，但记忆内核不可简单互替。
- **不要直接 `plugin add` 进生产 dsh web**：evolve 注册了 `memory/skill/todo/coi/session/...` 等工具与大量 UI 标签，与 Ikaros 现有 `ikaros-memory`（`v5_*` 工具）**工具面与 UI 会重叠甚至冲突**。要实测也得在隔离 profile 跑（见下）。

---

## 7. 推荐下一步（待拍板）

1. **吸收补丁（低风险）**：先落地 ① 写前注入扫描 + ③ `[dsh-only]` 隔离，直接收益、不影响 V5 语义核心。→ 可走 C 方案立即动手。
2. **隔离实测（中风险）**：新建一个 dsh profile，`plugin add` evolve 跑起来，验证它与 `v5_*` 工具/Ikaros UI 是否冲突、COI 派单是否能复用 Ikaros 现有外部通道。→ 可走 B 方案。
3. **长期评估（高风险）**：如 Ikaros 出现"多设备同步"刚需，再认真评估"文件化记忆"是否要作为 V5 之外的第二层。

---

_附：仓库已 clone 至 `C:/Users/PZS0X/AppData/Local/Temp/dsh-memory-evolve`，含 `lib/`(host)、`src/client/`(UI)、`tests/`(56)、`docs/`(含 记忆同步.md / COI-调度.md / rules.md)。需深挖任意模块（如 COI 调度器、Advisor 评审器、sync merge）可继续。_
