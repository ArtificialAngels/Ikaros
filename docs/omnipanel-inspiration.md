# 全能面板灵感清单（Omnipanel Inspiration List）

> 子任务 C3 产物。来源：`E:/Ikaros-something/reference project/` 下全部 21 个参考项目浏览归纳。
> 目标：为 Ikaros `:48920` 对话树面板升级为「全能面板」沉淀可借鉴功能点，按 **功能点 → 落地难度 → 优先级** 排列。
> 对照基线：当前 `core/conversation-tree/index.html`（2164 行）已具备 树画布 / 会话列表 / 工具卡 / 思考块 / 用量条 / 分支 / 模态框 / Toast / 记忆项 / Markdown 渲染 / SSE 流式。难度评估均相对此基线。
> 落地难度图例：🟢 低（前端纯增量，≤200 行 / 或后端已有数据）｜🟡 中（需前后端协同，200–800 行）｜🔴 高（跨服务/新协议/重构）。
> 优先级图例：P0 必做核心体验 ｜ P1 高价值 ｜ P2 锦上添花 ｜ P3 远期/实验。

## 速览总表

| # | 功能点 | 来源 | 难度 | 优先级 |
|---|--------|------|------|--------|
| 1 | 会话侧边栏分组（置顶/最近/归档） | hermes-studio | 🟢 | P0 |
| 2 | 会话右键菜单（重命名/置顶/归档/导出/复制ID） | hermes-studio | 🟢 | P0 |
| 3 | 会话搜索面板（Ctrl+K 唤起，去重节流） | hermes-studio | 🟡 | P0 |
| 4 | 会话批量选择+批量删除 | hermes-studio | 🟢 | P1 |
| 5 | 会话切换淡入动画（1500ms 交叉淡化） | hermes-studio | 🟢 | P2 |
| 6 | 会话标签/Profile 过滤下拉 | hermes-studio | 🟡 | P2 |
| 7 | 虚拟滚动长会话（vue-virtual-scroller / 类似） | hermes-studio | 🟡 | P1 |
| 8 | 工具调用展开/折叠 + 状态色 | hermes-studio / homerail | 🟢 | P0 |
| 9 | 工具调用全局显隐开关（localStorage 持久） | hermes-studio | 🟢 | P1 |
| 10 | 工具文件变更 diff 抽屉 | hermes-studio | 🔴 | P2 |
| 11 | 在飞工具条（最后一条用户消息后实时展示） | hermes-studio | 🟡 | P1 |
| 12 | 工具结果预览截断（180/2000 字符） | hermes-studio / homerail | 🟢 | P1 |
| 13 | 工具结果自动展开（结果到达时 watch） | homerail | 🟢 | P1 |
| 14 | 命令面板（Cmd+K，模糊搜索，分组） | ugc-video / homerail | 🟡 | P0 |
| 15 | 全局键盘快捷键矩阵（Ctrl+N/J/K/Esc） | hermes-studio | 🟢 | P0 |
| 16 | Slash 命令（输入框内 / 触发） | hermes-studio | 🟡 | P1 |
| 17 | Markdown 流式 + KaTeX + Mermaid 占位 | hermes-studio | 🟡 | P0 |
| 18 | 代码块头栏（语言标签 + 复制按钮） | hermes-studio | 🟢 | P0 |
| 19 | 统一 diff 渲染 + 上下文行折叠（>8 行折叠） | hermes-studio | 🟡 | P1 |
| 20 | 流式分批淡入动画（200ms 快照 key 切换） | N.E.K.O | 🟢 | P1 |
| 21 | 思考指示器（gif + 计时秒数） | hermes-studio | 🟢 | P0 |
| 22 | 中断后缀标记（ —（中断）） | Innerlife | 🟢 | P1 |
| 23 | 底部状态栏（token 数 / 模型 / 上下文长度 / 推理档位） | hermes-studio | 🟢 | P0 |
| 24 | 上下文用量微条（10 格色阶阈值） | homerail | 🟢 | P1 |
| 25 | 文件拖拽上传 + 粘贴图片 | hermes-studio | 🟡 | P1 |
| 26 | 附件缩略图条 + 移除按钮 | N.E.K.O / hermes-studio | 🟢 | P1 |
| 27 | 大纲面板（抽取 H1/H2/H3 跳转锚点） | hermes-studio | 🟡 | P1 |
| 28 | 右侧可调整检查器面板（Files/Terminal 标签） | hermes-studio | 🔴 | P2 |
| 29 | 模型切换器（Provider 分组 + 搜索 + 自定义模型） | hermes-studio | 🟡 | P1 |
| 30 | 推理档位选择器（reasoning-effort） | hermes-studio | 🟢 | P1 |
| 31 | 分叉线 lineage 指针 + 跳转父会话 | hermes-studio | 🟡 | P1 |
| 32 | 待审批/澄清浮动卡片（once/session/always/deny） | hermes-studio | 🔴 | P2 |
| 33 | 排队用户消息（带取消按钮） | hermes-studio | 🟡 | P2 |
| 34 | 审批流式压缩状态指示 | hermes-studio | 🟢 | P2 |
| 35 | 主调用卡片（模型/时长/token/stop原因/片段数） | Innerlife | 🟡 | P1 |
| 36 | 观察者抽屉（Main/Memory/Emotion/Relationship 四标签） | Innerlife | 🔴 | P2 |
| 37 | 记忆工具图追踪（实体提及→候选→命中分数） | Innerlife | 🔴 | P2 |
| 38 | 调用类型配色徽章（intent/memory/emotion/rel/compaction） | Innerlife | 🟢 | P2 |
| 39 | 按天分页加载历史（仅今日默认展开） | Innerlife | 🟢 | P1 |
| 40 | 上下文重置双模（clear 新章 / flush 带记忆写回） | Innerlife | 🟡 | P1 |
| 41 | 关系方绑定下拉 | Innerlife | 🔴 | P3 |
| 42 | 哈希渐变头像回退 | Innerlife | 🟢 | P2 |
| 43 | 迭代横幅卡（轮次/已完成/本轮任务/停止条件/ETA） | strict-agent-loop | 🟡 | P1 |
| 44 | 迭代计数器 + 5KB 输出环 | sleepless-agent | 🟢 | P1 |
| 45 | 停滞检测告警徽章（N 分钟无进展） | sleepless-agent | 🟡 | P1 |
| 46 | 文件变更即进展信号（"📁 3 文件已改"） | sleepless-agent | 🟡 | P1 |
| 47 | 后台任务状态机（preparing/running/observing/idle/error） | sleepless-agent / OpenHarness | 🟡 | P1 |
| 48 | 优雅停止语义（"当前步后停止"） | sleepless-agent | 🟢 | P1 |
| 49 | 活动事件分类法（exec_start/output/file_change/stall/done） | sleepless-agent | 🟢 | P1 |
| 50 | 任务 ID 命名（task-YYYYMMDD-HHMMSS-md5[:6]） | sleepless-agent | 🟢 | P2 |
| 51 | 三层记忆架构（L1 指针/L2 主题/L3 流日志） | OpenHarness | 🟡 | P1 |
| 52 | 外部验证循环（不允许自证完成） | OpenHarness | 🔴 | P2 |
| 53 | 断路器（N 次失败熔断 + 手动复位） | OpenHarness | 🔴 | P2 |
| 54 | 心跳断点恢复（"恢复"按钮跳到最后活跃步） | OpenHarness | 🟡 | P2 |
| 55 | 熵控自动归档（>50 次压缩 / >10MB 轮转） | OpenHarness | 🟡 | P2 |
| 56 | Dream 模式离线沉淀（扫描/去重/剪枝/注入 playbook） | OpenHarness | 🔴 | P3 |
| 57 | 任务契约面板（mission/playbook/eval_criteria） | OpenHarness | 🟡 | P2 |
| 58 | 缓存感知读取顺序（静态在前/动态在后） | OpenHarness | 🟢 | P2 |
| 59 | 质量分级徽章（A/B/C/F + 自动重试 C） | OpenHarness | 🟡 | P2 |
| 60 | 力导向知识图谱（react-force-graph，拖拽钉扎/缩放） | cognee | 🔴 | P2 |
| 61 | 图形布局模式循环器（none/td/bu/lr/rl/radial，5s 轮换） | cognee | 🟡 | P3 |
| 62 | 节点类型色键图例 | cognee | 🟢 | P2 |
| 63 | 会话按日期分组（Today/Yesterday/This week/Month/Older） | cognee | 🟢 | P1 |
| 64 | 会话状态点+徽章（completed/running/failed/abandoned） | cognee | 🟢 | P1 |
| 65 | 会话统计卡（观察/工具调用/token/费用/时长） | cognee | 🟡 | P2 |
| 66 | 会话内转录搜索 + thumbs 反馈 + 复制 | cognee | 🟡 | P2 |
| 67 | 工具调用条形图（每会话） | cognee | 🟡 | P3 |
| 68 | Pod 就绪门控（provisioning 菊转/不可达错误卡） | cognee | 🟢 | P1 |
| 69 | 分区图标导航栏（DATA/EXPLORE/CONNECT，未就绪时 dim+lock） | cognee | 🟡 | P2 |
| 70 | 365 天贡献热力图（5 级 + SVG 蛇动画） | thirdspace | 🟡 | P2 |
| 71 | Markdown 内嵌 Todo（✅-时间戳 切换） | thirdspace | 🟢 | P2 |
| 72 | 项目状态解析（🟢🟡🔴 + 里程碑） | thirdspace | 🟢 | P2 |
| 73 | 技能注册表解析器 | thirdspace | 🟡 | P2 |
| 74 | 编号文件夹 vault 约定（00-系统…99-归档 + WORKSPACE.md） | thirdspace | 🟢 | P3 |
| 75 | REPL 统一皮肤（banner/prompt/进度条/表格/帮助渲染器） | CLI-Anything | 🟡 | P2 |
| 76 | 工具注册矩阵（list/search/info/install/update 升级） | CLI-Anything | 🔴 | P3 |
| 77 | 记忆星座画布（pan/zoom/drag 节点，类型化 episodic/semantic/...） | Reverie | 🔴 | P2 |
| 78 | 三层 SVG 波形心象（lerp 状态切换 reflect/explore/pursue/rest） | Reverie | 🟡 | P3 |
| 79 | 意图卡 + 工具结果徽章（ok/error + 摘要 + rawPath） | Reverie | 🟢 | P2 |
| 80 | 实时活动流（time-sorted，kind-colored，REALTIME/MOCK 指示） | Reverie | 🟢 | P1 |
| 81 | 后端视图模型构建器（intent+tools+results 合并时间线） | Reverie | 🟡 | P2 |
| 82 | 工具摘要分形态（digest/key_facts/raw_ref 追加 tool_notes） | Reverie | 🟡 | P2 |
| 83 | 双速轮询（1.5s 主切片 / 30s 记忆切片 + disposed 守卫） | Reverie | 🟢 | P1 |
| 84 | DAG 运行时覆盖层（run_list→dag_graph→dag_detail 三态机） | homerail | 🔴 | P3 |
| 85 | 三栏可折叠 shell（左会话/中聊天/右工作区） | homerail | 🔴 | P2 |
| 86 | 会话子运行子树（session 下的 run_ids 嵌套按钮） | homerail | 🟡 | P2 |
| 87 | 乐观状态覆盖（sessionStatusOverrides 即时反馈） | homerail | 🟢 | P2 |
| 88 | 工作流模板菜单（load/save 会话模板） | ugc-video | 🟡 | P2 |
| 89 | 节点调色板（14 类型可拖拽分类） | ugc-video | 🟡 | P3 |
| 90 | 执行面板（步进式 live streaming idle/running/completed/error） | ugc-video | 🟡 | P2 |
| 91 | 运行历史侧栏（状态图标/时长/删除/召回） | ugc-video | 🟢 | P2 |
| 92 | Socket.IO RPC 控制面板（enable/disable 各子系统） | Neuro | 🔴 | P3 |
| 93 | 反应式信号广播（setter 触发 sio.emit 自动推送） | Neuro | 🟡 | P2 |
| 94 | 反射记忆循环（每 20 条生成 Q&A 向量入库） | Neuro | 🔴 | P3 |
| 95 | SQLite 会话索引 + per-session 目录 + 迭代日志 | agend | 🟡 | P2 |
| 96 | 会话锁 + continue 模式（PID 跟踪/恢复点） | agend | 🟡 | P2 |
| 97 | Todo 列表完成 diff（completed 增量） | agend | 🟢 | P2 |
| 98 | 实时 Popen 流式 + on_output 回调 | agend | 🟡 | P2 |
| 99 | 富控制台彩色状态（=== / ✅ / ⚠️ / 红） | agend | 🟢 | P3 |
| 100 | Glass-morphism 设计语言（backdrop-filter blur/saturate） | N.E.K.O | 🟢 | P2 |
| 101 | Host-Wrapper + 挂载渲染器解耦（mount/unmount API） | N.E.K.O | 🔴 | P3 |
| 102 | 结构化消息块联合（text/image/link/status/buttons/topic-hint） | N.E.K.O | 🟡 | P1 |
| 103 | i18n 宿主桥（renderer 无 i18n 依赖） | N.E.K.O | 🟢 | P2 |
| 104 | 导出选择 + 预览管线（idle→loading→ready→failed） | N.E.K.O | 🟡 | P2 |
| 105 | 主题提示气泡（proactive 开场白） | N.E.K.O | 🟢 | P2 |
| 106 | 选择提示卡（composer 锚定选项按钮） | N.E.K.O | 🟢 | P2 |
| 107 | 技能=目录（frontmatter 卡片 + 树浏览 + 生命周期徽章） | skills-main | 🟡 | P2 |
| 108 | 可组合技能依赖图（"chains with" 链接） | skills-main | 🔴 | P3 |
| 109 | 技能支持文档折叠手风琴 | skills-main | 🟢 | P2 |
| 110 | HITL 脚本模板标签页 | skills-main | 🟢 | P3 |
| 111 | herdr 命令面板覆盖层（TUI launcher） | herdr | 🟡 | P2 |
| 112 | herdr 侧栏/标签/分屏（TUI 多 pane 模型） | herdr | 🟡 | P2 |

---

## 详细说明（按功能域分组）

### A. 会话管理（Session Management）

#### 1. 会话侧边栏分组（置顶/最近/归档） — 🟢 P0
- **来源**：hermes-studio `components/hermes/chat/ChatPanel.vue`（`pinnedSessions`/`unpinnedSessions` computed）
- **现状对照**：当前面板有 `renderSessions()`，但仅平铺列表。
- **落地建议**：前端加 computed 分组 + 置顶/归档标记字段，后端 `_save_sessions` 已可承载。
- **难度依据**：纯前端增量，会话 JSON 已有结构。

#### 2. 会话右键菜单 — 🟢 P0
- **来源**：hermes-studio `ChatPanel.vue`（NDropdown 右键：pin/rename/archive/set-workspace/set-model/export/open-in-new-tab/copy-link/copy-id）
- **现状对照**：当前面板已有 `onNodeContextMenu` + `handleCtxAction`（节点级），会话级右键缺。
- **落地建议**：复用现有 `showModal/toast` 套路，新增会话右键菜单。

#### 3. 会话搜索面板（Ctrl+K） — 🟡 P0
- **来源**：hermes-studio `components/hermes/chat/SessionSearchModal.vue`（160ms 节流，服务端 `searchSessions` 返回 snippet + `matched_message_id`，方向键导航 + Enter 跳转 + 滚动定位）
- **现状对照**：后端已有 `_search_dicts`，但前端无搜索入口。
- **落地建议**：新增搜索模态，调 `/search` 端点，命中后 `switchSession` + 滚动到 `matched_message_id`。
- **难度依据**：需前后端协同（搜索端点 + 高亮定位）。

#### 4. 会话批量选择 + 批量删除 — 🟢 P1
- **来源**：hermes-studio `ChatPanel.vue`（checkbox + 批量删除）
- **落地建议**：会话项加 checkbox 状态 + 工具栏批量按钮。

#### 5. 会话切换淡入动画 — 🟢 P2
- **来源**：hermes-studio（1500ms 交叉淡化）
- **落地建议**：CSS `transition: opacity .4s` + 临时 class。

#### 6. 会话标签/Profile 过滤下拉 — 🟡 P2
- **来源**：hermes-studio（Profile filter dropdown at top）
- **落地建议**：需会话元数据加 `profile` 字段，前端加下拉。

#### 7. 虚拟滚动长会话 — 🟡 P1
- **来源**：hermes-studio `VirtualMessageList.vue`（vue-virtual-scroller DynamicScroller + anchor-scroll + viewport-restore）
- **现状对照**：当前 `renderThread()` 全量 innerHTML，超 200 条会卡。
- **落地建议**：引入虚拟滚动库或自实现窗口化；保持锚点滚动恢复。
- **难度依据**：需重构 `renderThread` 渲染管线。

#### 63. 会话按日期分组 — 🟢 P1
- **来源**：cognee `SearchPage.tsx`（Today/Yesterday/This week/This month/Older）
- **落地建议**：`renderSessions()` 加按 `updated_at` 分组 computed。

#### 64. 会话状态点+徽章 — 🟢 P1
- **来源**：cognee `sessions/page.tsx`（completed/running/failed/abandoned）
- **落地建议**：会话 JSON 加 `status` 字段，前端加色点。

#### 65. 会话统计卡 — 🟡 P2
- **来源**：cognee（observations/tool-calls/tokens/cost/duration）
- **落地建议**：后端聚合每会话指标，前端统计卡。

#### 66. 会话内转录搜索 + thumbs 反馈 — 🟡 P2
- **来源**：cognee（collapsible transcript cards with copy + thumbs）
- **落地建议**：转录折叠 + 点赞/踩反馈（落库 V5）。

#### 86. 会话子运行子树 — 🟡 P2
- **来源**：homerail `AgentSessionSidebar.vue`（session 下的 `run_ids` 嵌套按钮，GitBranch + mono `-8` 后缀）
- **现状对照**：当前面板的「分支」概念已近，但会话与运行子树未分层。
- **落地建议**：会话项下展开 run 列表，跳转到对应节点。

#### 87. 乐观状态覆盖 — 🟢 P2
- **来源**：homerail `sessionStatusOverrides`（即时反馈后再落库）
- **落地建议**：会话操作先改本地 state 再 await 后端。

#### 95. SQLite 会话索引 + per-session 目录 — 🟡 P2
- **来源**：agend `agend/session.py`（`.agend/{uuid}/task.md` + 时间戳迭代日志）
- **现状对照**：当前会话存 `ui_conversation_tree.json`。
- **落地建议**：会话量大时迁移到 SQLite 索引 + per-session 目录；当前 JSON 够用，列远期。

#### 96. 会话锁 + continue 模式 — 🟡 P2
- **来源**：agend（shell-PID 跟踪 + `get_continue_state` 从 `iteration_XXX.json` 恢复）
- **落地建议**：多开同名会话时加锁；继续模式恢复到上次活跃节点。

---

### B. 工具调用展示（Tool Call Display）

#### 8. 工具调用展开/折叠 + 状态色 — 🟢 P0
- **来源**：hermes-studio `MessageItem.vue`（`toolExpanded` ref，toolName/preview/status/duration）；homerail `AgentChatPanel.vue`（绿色 completed / 红色 failed / 琥珀 running）
- **现状对照**：当前 `toolCardHtml(tc)` 已有卡片，但展开折叠/状态色未完善。
- **落地建议**：补 `toolExpanded` 状态 + 状态 pill。

#### 9. 工具调用全局显隐开关 — 🟢 P1
- **来源**：hermes-studio `composables/useToolTraceVisibility.ts`（localStorage `hermes_show_tool_calls`）
- **落地建议**：底部状态栏加切换 + localStorage 持久。

#### 10. 工具文件变更 diff 抽屉 — 🔴 P2
- **来源**：hermes-studio `toolChangeDrawerVisible`
- **落地建议**：需 workspace 文件快照 + diff 引擎；远期。

#### 11. 在飞工具条 — 🟡 P1
- **来源**：hermes-studio `MessageList.vue` `#after` slot（最后一条用户消息后展示 in-flight tools + spinner）
- **现状对照**：当前流式 `_chat_stream_events` 已有事件，但前端未聚合在飞工具条。
- **落地建议**：订阅 `tool.started`/`tool.completed`，在最后用户消息后渲染临时条。

#### 12. 工具结果预览截断 — 🟢 P1
- **来源**：hermes-studio / homerail（180/2000 字符截断）
- **现状对照**：AGENTS.md 已提及「gateway 工具结果经 `_on_tool_complete` 截断 2000」。
- **落地建议**：前端再二次截断到 180 字符预览，展开看全。

#### 13. 工具结果自动展开 — 🟢 P1
- **来源**：homerail `AgentChatPanel.vue` L248（watcher，`toolResult` 到达自动 expand）
- **落地建议**：默认折叠，结果到达时自动展开 3s 再折叠。

#### 35. 主调用卡片 — 🟡 P1
- **来源**：Innerlife `ObserverDrawer.tsx`（pill：model/duration/token/stop reason/fragment count + 锚点滚动到各 section）
- **落地建议**：每个 AI 节点展开时可显示主调用元数据。

#### 36. 观察者抽屉 — 🔴 P2
- **来源**：Innerlife（Main/Memory/Emotion/Relationship 四标签，每 call 子标签 + 完成指示）
- **落地建议**：需 V5 记忆/情感系统暴露；远期。

#### 37. 记忆工具图追踪 — 🔴 P2
- **来源**：Innerlife `observer-ui.tsx` `MemoryToolGraphTrace`（实体提及→候选→激活分数→命中实体链接）
- **落地建议**：需 V5 检索路径透出；远期。

#### 38. 调用类型配色徽章 — 🟢 P2
- **来源**：Innerlife `CALL_ACCENTS`（indigo/turn, green/memory, pink/emotion, blue/relationship, orange/compaction）
- **落地建议**：节点/卡片按 kind 加色徽章。

#### 79. 意图卡 + 工具结果徽章 — 🟢 P2
- **来源**：Reverie `IntentCard.tsx`（ok/error 徽章 + 摘要 + rawPath + "Guidance" + "Model Thinking" markdown + `memory write → path` 行）
- **落地建议**：工具卡加 ok/error pill + 记忆写入路径显示。

#### 82. 工具摘要分形态 — 🟡 P2
- **来源**：Reverie `tool_loop.py`（每结果产出 digest/key_facts/raw_ref 追加 tool_notes）
- **落地建议**：后端 `_on_tool_complete` 增加摘要字段；前端折叠区显示。

#### 67. 工具调用条形图 — 🟡 P3
- **来源**：cognee（每会话工具调用条形图）
- **落地建议**：会话统计卡内加迷你条形图。

---

### C. 流式渲染（Streaming Render）

#### 17. Markdown 流式 + KaTeX + Mermaid 占位 — 🟡 P0
- **来源**：hermes-studio `MarkdownRenderer.vue`（markdown-it + KaTeX latex fences + Mermaid 占位 + highlight.js）
- **现状对照**：当前 `mdRender(src)` 用 marked，无 KaTeX/Mermaid。
- **落地建议**：引入 KaTeX + Mermaid（占位 + 懒渲染）。
- **难度依据**：需加依赖 + 流式占位逻辑。

#### 18. 代码块头栏 — 🟢 P0
- **来源**：hermes-studio `highlight.ts`（语言标签 + 复制按钮）
- **现状对照**：当前代码块无头栏。
- **落地建议**：marked 渲染器 override fence，加 header div。

#### 19. 统一 diff 渲染 + 上下文行折叠 — 🟡 P1
- **来源**：hermes-studio `highlight.ts`（unified-diff 渲染 + >8 行折叠）
- **落地建议**：diff fence 语法识别 + 折叠。

#### 20. 流式分批淡入动画 — 🟢 P1
- **来源**：N.E.K.O `SmartTextBlock.tsx`（`StreamingText` 200ms 快照 + `<span key={settledLen}>` 重触发 CSS fade-in）
- **落地建议**：流式 chunk 用 key 切换触发动画，避免逐字 diff。

#### 21. 思考指示器 — 🟢 P0
- **来源**：hermes-studio `MessageList.vue` `#after`（gif + 计时秒数）
- **现状对照**：当前 `thinkBlockHtml(thinking)` 已有思考块，但无计时器。
- **落地建议**：思考开始时记录 `startedAt`，每秒更新耗时。

#### 22. 中断后缀标记 — 🟢 P1
- **来源**：Innerlife `ChatArea.tsx` L376（` —（中断）` 追加到 aborted 消息）
- **现状对照**：当前中止无视觉标记。
- **落地建议**：SSE `warn`/`aborted` 事件时追加后缀 + 灰样式。

#### 34. 流式压缩状态指示 — 🟢 P2
- **来源**：hermes-studio `MessageList.vue` `#after`（compression-state 指示）
- **落地建议**：上下文压缩时显示提示条。

#### 33. 排队用户消息 — 🟡 P2
- **来源**：hermes-studio（`queuedUserMessages` map + 取消按钮）
- **落地建议**：发送期间排队显示 + 可取消。

#### 102. 结构化消息块联合 — 🟡 P1
- **来源**：N.E.K.O `message-schema.ts`（text/image/link/status/buttons/topic-hint 判别联合 + Zod 校验）
- **现状对照**：当前 AI 卡片 HTML 字符串拼装。
- **落地建议**：节点 extras 结构化为 typed blocks，按类型分发渲染。

---

### D. 命令面板 / 快捷键（Command Palette & Shortcuts）

#### 14. 命令面板 — 🟡 P0
- **来源**：ugc-video `components/ui/command.tsx`（cmdk，模糊搜索 + 分组 + 键盘导航）；homerail DAG 覆盖层
- **现状对照**：当前面板无命令面板。
- **落地建议**：Cmd+K 唤起 cmdk 风格面板，分组（会话/节点/工具/设置/动作）。
- **难度依据**：需命令注册表 + 模态层（已有 `showModal`）。

#### 15. 全局键盘快捷键矩阵 — 🟢 P0
- **来源**：hermes-studio `composables/useKeyboard.ts`（Ctrl+N 新聊/Ctrl+J jobs/Ctrl+K 搜索/Esc 关模态）
- **落地建议**：全局 keydown 监听 + 命令映射。

#### 16. Slash 命令 — 🟡 P1
- **来源**：hermes-studio `ChatInput.vue`（`slashActive`/`filteredBridgeCommands` from `BRIDGE_SESSION_COMMAND_DEFINITIONS`）
- **落地建议**：输入框 `/` 触发命令补全。

#### 111. herdr 命令面板覆盖层 — 🟡 P2
- **来源**：herdr `src/ui/menus.rs` `render_global_launcher_menu`
- **落地建议**：TUI 思路移植，面板内嵌终端命令面板（可选与 herdr 联动）。

#### 15（重复）/ herdr 侧栏分屏 — 🟡 P2
- **来源**：herdr `src/ui/sidebar.rs` / `src/ui/tabs.rs`
- **落地建议**：多 pane 分屏模式（高级用户）。

---

### E. 状态栏 / 活动流（Status Bar & Activity Feed）

#### 23. 底部状态栏 — 🟢 P0
- **来源**：hermes-studio `ChatInput.vue` footer（model label 按钮 + reasoning-effort 选择 + tool-trace toggle + context-length 读数）
- **现状对照**：当前 `usageLineHtml(node)` 有用量，但无固定状态栏。
- **落地建议**：底部固定 bar 显示 模型/上下文长度/token/reasoning 档/工具显隐。

#### 24. 上下文用量微条 — 🟢 P1
- **来源**：homerail `AgentWorkspace.vue` L322-336（10 格色阶阈值 + ctx 标签）
- **现状对照**：当前 `updateUsageBar(usage, model, ctxWin)` 已有用量条。
- **落地建议**：升级为 10 格色阶微条 + 阈值色变。

#### 80. 实时活动流 — 🟢 P1
- **来源**：Reverie `ActivityList.tsx`（time-sorted，kind-colored badges intent/tool/result/scheduler，REALTIME/MOCK 指示）
- **落地建议**：侧栏底部活动 feed，订阅 SSE 事件。

#### 81. 后端视图模型构建器 — 🟡 P2
- **来源**：Reverie `reverie/api/services/dashboard_service.py` `_build_activity`（intent+tools+results+scheduler 合并时间线）+ `_extract_model_thinking`（按 `[tool-result]` 切分模型文本）+ `_tool_summary`（每工具格式化）
- **落地建议**：后端新增 `/activity` 端点返回合并时间线。

#### 83. 双速轮询 — 🟢 P1
- **来源**：Reverie `webui/src/App.tsx`（1.5s 主切片 / 30s 记忆切片 + disposed 守卫）
- **落地建议**：活动流 1.5s 轮询，记忆图 30s 轮询，避免高频拉记忆。

#### 49. 活动事件分类法 — 🟢 P1
- **来源**：sleepless-agent `reporters/base.py`（exec_start/exec_output/file_change/stall_warning/task_done，emoji 前缀 ▶️/🧠/📁/⚠️/✅，永不 raise 永不影响执行）
- **落地建议**：活动流事件类型标准化 + emoji 前缀。

---

### F. 文件上传 / 附件（File Upload）

#### 25. 文件拖拽 + 粘贴图片 — 🟡 P1
- **来源**：hermes-studio `ChatInput.vue`（`addFiles` 暴露给父；文件按钮 + 粘贴图片 blob→File + 拖放）；`ChatPanel.vue`（`handleChatDrop` 转发）
- **落地建议**：输入区加 drag-drop + paste 监听，FormData 上传到 `/upload`。
- **难度依据**：需后端上传端点 + 前端拖放。

#### 26. 附件缩略图条 — 🟢 P1
- **来源**：N.E.K.O `message-schema.ts` `ComposerAttachment`（id/url/alt）；hermes-studio `buildContentBlocks`（image/file 分块）
- **落地建议**：输入框上方缩略图条 + 移除按钮。

#### 104. 导出选择 + 预览管线 — 🟡 P2
- **来源**：N.E.K.O `CompactExportHistoryPanel.tsx`（checkbox 最多 100，格式 image/png/jpeg/webp 样式 neko/original/poster/lyrics + Markdown，预览 idle→loading→ready→failed）
- **落地建议**：导出会话/分支为 PNG/Markdown + 预览。

---

### G. 侧边栏 / 检查器（Sidebar & Inspector）

#### 27. 大纲面板 — 🟡 P1
- **来源**：hermes-studio `OutlinePanel.vue`（抽取 H1/H2/H3 + `scrollToAnchor` 跳转）
- **落地建议**：右侧抽屉抽取当前节点 AI 回复的标题，点击跳转。

#### 28. 右侧可调整检查器 — 🔴 P2
- **来源**：hermes-studio `ChatPanel.vue`（右 tool panel，pointer-drag handle，宽度持久 localStorage，标签 Files/Terminal）
- **落地建议**：需引入文件/终端后端；远期。

#### 85. 三栏可折叠 shell — 🔴 P2
- **来源**：homerail `agent-ui/src/views/agent/index.vue`（左 w-14↔w-292 / 中 / 右 w-10↔min(34vw,620px)，`PanelLeftClose/Open`）
- **现状对照**：当前面板为 树画布 + 右侧栏 双栏。
- **落地建议**：加可折叠左会话栏 → 三栏。
- **难度依据**：需重构布局。

#### 107. 技能=目录卡片 — 🟡 P2
- **来源**：skills-main `skills/engineering/grill-with-docs/SKILL.md`（frontmatter name/description + agents/openai.yaml + scripts/ + 文档）
- **落地建议**：技能侧栏，从 `.claude/skills/` 读取 frontmatter 渲染卡片 + 树浏览 + 生命周期徽章（deprecated/in-progress）。

#### 109. 技能支持文档折叠手风琴 — 🟢 P2
- **来源**：skills-main（codebase-design/ 附带 DEEPENING.md/DESIGN-IT-TWICE.md）
- **落地建议**：技能卡片下展开 "References" 手风琴。

#### 75. REPL 统一皮肤 — 🟡 P2
- **来源**：CLI-Anything `repl_skin.py`（banner/prompt/success/error/warning/info/section/progress-bar/table/help 渲染器 + prompt_toolkit FileHistory/AutoSuggest/completion-menu/bottom-toolbar）
- **落地建议**：终端面板（若有）用此皮肤风格。

---

### H. 记忆 / 知识图谱（Memory & Knowledge Graph）

#### 51. 三层记忆架构 — 🟡 P1
- **来源**：OpenHarness `harness_memory.py`（L1 heartbeat.md <2KB 指针索引 / L2 knowledge/*.md 主题 / L3 logs/execution_stream.log 追加只 grep）
- **现状对照**：V5 已有类似分层（`memory_retrieval.unified_retrieve`）。
- **落地建议**：侧栏「知识索引」L1 指针 + 展开主题 L2 + 可搜事件日志 L3 抽屉。

#### 60. 力导向知识图谱 — 🔴 P2
- **来源**：cognee `GraphVisualization.tsx`（react-force-graph-2d + 自定义 canvas 节点/边 + click-to-select + drag-to-pin fx/fy + zoomToFit + ResizeObserver + 空状态占位图）
- **落地建议**：V5 图谱可视化；远期（V5 已有图扩散，缺可视化）。

#### 61. 图形布局模式循环器 — 🟡 P3
- **来源**：cognee `GraphControls.tsx`（none/td/bu/lr/rl/radialin/radialout 5s 轮换）
- **落地建议**：图布局切换器（演示用）。

#### 62. 节点类型色键图例 — 🟢 P2
- **来源**：cognee `GraphLegend.tsx` + `getColorForNodeType.ts`（Entity/Issue/Commit/File → tailwind 色）
- **落地建议**：图例条。

#### 77. 记忆星座画布 — 🔴 P2
- **来源**：Reverie `MemoryConstellation.tsx`（pan/zoom/drag 节点，SVG `<line>` 边 + 绝对定位节点 div，cluster chips，类型 episodic/semantic/reflective/procedural）
- **落地建议**：V5 记忆可视化备选方案（与 60 互补）。

#### 78. 三层 SVG 波形心象 — 🟡 P3
- **来源**：Reverie `HeroWave.tsx`（3 层 SVG 波形，频率/振幅/呼吸 lerp 状态配置 reflect/explore/pursue_goal/rest，requestAnimationFrame）
- **落地建议**：「agent 存活」指示器（装饰性）。

#### 68. Pod 就绪门控 — 🟢 P1
- **来源**：cognee `CustomAppShell.tsx`（POD_DEPENDENT_PATHS，provisioning 菊转 / 不可达错误卡）
- **现状对照**：依赖 V5/网关可达性。
- **落地建议**：面板启动时检测各服务可达，不可达显示错误卡而非空白。

#### 69. 分区图标导航栏 — 🟡 P2
- **来源**：cognee `CustomAppShellNavbar.tsx`（DATA/EXPLORE/CONNECT 分组，未就绪 dim+lock，底部 feedback/call）
- **落地建议**：左导航分区（对话/记忆/工具/设置）。

---

### I. 后台任务 / 长时运行（Background Tasks & Long-running）

#### 43. 迭代横幅卡 — 🟡 P1
- **来源**：strict-agent-loop `references/protocol.md`（每轮前展示 迭代号/已完成轮数/本轮原子任务/本地 done 条件/全局 stop 条件/停止条件/平均轮时 + ETA）
- **现状对照**：herdr 多路复用器有迭代概念。
- **落地建议**：herdr 任务卡显示迭代横幅。

#### 44. 迭代计数器 + 输出环 — 🟢 P1
- **来源**：sleepless-agent `state.py` L72-77（`update_output` 保留最后 5KB + `iteration_count`）
- **落地建议**：任务卡显示「iteration 3 · last output 5KB」+ 预览截断。

#### 45. 停滞检测告警 — 🟡 P1
- **来源**：sleepless-agent `daemon.py` L283-292（`last_progress_time` + `stall_threshold_minutes` 默认 10 + `stall_warned` 一次性 WARN）
- **落地建议**：后台任务卡显示「无进展 N 分钟」红色徽章。

#### 46. 文件变更即进展 — 🟡 P1
- **来源**：sleepless-agent `daemon.py` L100-129（`_detect_file_changes` mtime 快照 + diff，变更重置 stall 计时）
- **落地建议**：任务卡显示「📁 3 文件已改」实时进展证明。

#### 47. 后台任务状态机 — 🟡 P1
- **来源**：sleepless-agent `core/daemon.py` L18-24（INIT/CHECK_CTX/RUN_CLAUDE/OBSERVE/IDLE 五态）；OpenHarness `harness_boot.py` L181-213（idle/running/completed/failed/blocked/mission_complete 六态 + 熔断）
- **落地建议**：任务状态 chip 离散化（preparing/running/observing/idle/error），非单一 spinner。

#### 48. 优雅停止 — 🟢 P1
- **来源**：sleepless-agent `cli.py` L33-46（`cmd_stop`：running 时设 idle 但让当前迭代跑完；idle 时清 prompt）
- **落地建议**：停止按钮 = 「当前步后停止」非硬杀。

#### 50. 任务 ID 命名 — 🟢 P2
- **来源**：sleepless-agent `daemon.py` L92-98（`task-{YYYYMMDD-HHMMSS}-{md5(prompt)[:6]}`）
- **落地建议**：herdr 任务稳定 ID 便于 grep。

#### 53. 断路器 — 🔴 P2
- **来源**：OpenHarness `harness_boot.py` L181-213（N 次连续失败默认 3 熔断 + 全执行阻塞 + 手动复位，防 runaway API 成本）
- **落地建议**：连续失败时面板红色 banner + 手动复位按钮。

#### 54. 心跳断点恢复 — 🟡 P2
- **来源**：OpenHarness `harness_heartbeat.py`（每会话读 heartbeat.md 找恢复点 + start/done/fail/blocked/mission_complete 更新指针 + 追加流）
- **落地建议**：「恢复」按钮跳到最后活跃步 + 运行历史时间线。

#### 55. 熵控自动归档 — 🟡 P2
- **来源**：OpenHarness `harness_cleanup.py`（>50 次压缩 progress.md + >10MB 轮转 execution_stream.log + 跨层指针一致性检查 + 悬挂引用检测）
- **落地建议**：设置暴露「N 条后自动归档」「最大日志大小」滑块 + 健康检查清单。

#### 57. 任务契约面板 — 🟡 P2
- **来源**：OpenHarness `templates/mission.md`/`playbook.md`/`eval_criteria.md`（mission 不可变宪法 / playbook 步骤 / eval_criteria 机器可检完成条件）
- **落地建议**：会话契约面板显示 目标/当前步进度条/完成清单（自动勾选）。

#### 58. 缓存感知读取顺序 — 🟢 P2
- **来源**：OpenHarness `SKILL.md` L157-163 + `harness_boot.py` L302-303（静态内容 mission/eval 先加载 + 动态 heartbeat 后加载，最大化 prompt cache 命中）
- **落地建议**：组装系统提示时静态块在前、动态块在后。

#### 59. 质量分级徽章 — 🟡 P2
- **来源**：OpenHarness `templates/eval_criteria.md` L57-66（A/B/C/F 分级 + 阈值 + 后果 deliver/retry/escalate）
- **落地建议**：每响应显示分级徽章 + C 级自动重试。

#### 52. 外部验证循环 — 🔴 P2
- **来源**：OpenHarness `harness_eval.py`（不允许 agent 自证完成，独立脚本运行 file-existence/non-emptiness/heartbeat-health 检查，输出 JSON 报告）
- **落地建议**：每工具调用后显示自动验证徽章 + 下钻报告。

#### 56. Dream 模式离线沉淀 — 🔴 P3
- **来源**：OpenHarness `harness_dream.py`（3 AM cron：扫 L3 流找重复错误/恢复模式 + 去重 L2 + 剪枝 L1 + 注入 playbook）
- **落地建议**：「Insights」标签页浮现重复问题与恢复策略 + 「Consolidate Now」按钮。

#### 97. Todo 列表完成 diff — 🟢 P2
- **来源**：agend `supervisor.py`（TodoList/TodoItem content+completed JSON + `SupervisorResult` is_complete/pending_items/newly_completed）
- **落地建议**：任务卡显示 todo 增量完成。

#### 98. 实时 Popen 流式 — 🟡 P2
- **来源**：agend `agent_cli.py`（line-buffered Popen + on_output 回调 + `--resume`）
- **落地建议**：herdr 子进程输出实时流到面板。

---

### J. 画布 / 工件（Canvas & Artifacts）

#### 84. DAG 运行时覆盖层 — 🔴 P3
- **来源**：homerail `DagRuntimeOverlay.vue`（run_list→dag_graph→dag_detail 三态机 + zoom-enter + progressive-back 退出 detail→graph→list→close）
- **落地建议**：DAG 探索模态层；远期。

#### 88. 工作流模板菜单 — 🟡 P2
- **来源**：ugc-video `templates-menu.tsx`（load/save 会话模板）
- **落地建议**：会话模板存取（预设系统提示/工具集）。

#### 89. 节点调色板 — 🟡 P3
- **来源**：ugc-video `node-palette.tsx`（14 类型可拖拽分类）
- **落地建议**：工作流构建器；远期。

#### 90. 执行面板 — 🟡 P2
- **来源**：ugc-video `execution-panel.tsx`（步进式 live streaming idle/running/completed/error + 折叠输出/node）
- **落地建议**：任务执行步进面板。

#### 91. 运行历史侧栏 — 🟢 P2
- **来源**：ugc-video `runs-panel.tsx`（状态图标/时长/删除/召回）
- **落地建议**：运行历史侧栏（与 86 互补）。

---

### K. 其他亮点（Other Highlights）

#### 29. 模型切换器 — 🟡 P1
- **来源**：hermes-studio `ChatPanel.vue` session-model modal（Provider 分组 + 折叠组 + 搜索 + 自定义模型 + MoA-preset + 每会话 reasoning-effort）
- **现状对照**：当前有 `renderHeader` 模型标签。
- **落地建议**：点击模型标签弹出选择器。

#### 30. 推理档位选择器 — 🟢 P1
- **来源**：hermes-studio（per-session reasoning-effort）
- **落地建议**：状态栏下拉（low/medium/high）。

#### 31. 分叉 lineage 指针 — 🟡 P1
- **来源**：hermes-studio `MessageList.vue`（fork-divider 系统消息 + 链接到父会话 + `/fork` 命令创建子会话）
- **现状对照**：当前 `branchFromNode(nid)` 已有分支，但无 lineage 指针 UI。
- **落地建议**：分叉节点显示父节点链接。

#### 32. 待审批/澄清浮动卡片 — 🔴 P2
- **来源**：hermes-studio（`PendingApproval`/`PendingClarify` 浮动卡片，once/session/always/deny 选择）
- **落地建议**：需工具调用审批协议；远期。

#### 39. 按天分页加载历史 — 🟢 P1
- **来源**：Innerlife `chat-history.ts`（按本地 day key 分组 + 仅今日默认 + Show more 加载前一天）
- **落地建议**：线程历史按天折叠加载。

#### 40. 上下文重置双模 — 🟡 P1
- **来源**：Innerlife `context-reset.ts`（clear 新章 + flush 带记忆写回）
- **现状对照**：V5 有 `memory_promote`。
- **落地建议**：会话菜单加「清空（新章）」/「清空并写回记忆」。

#### 41. 关系方绑定 — 🔴 P3
- **来源**：Innerlife `Sidebar.tsx` L224-266（每会话下拉绑定命名关系方 + unbind 停用关系系统）
- **落地建议**：需 persona/关系系统；远期。

#### 42. 哈希渐变头像回退 — 🟢 P2
- **来源**：Innerlife `ChatArea.tsx` L246（agent name hash → HSL 渐变）
- **落地建议**：无头像时 fallback。

#### 70. 365 天贡献热力图 — 🟡 P2
- **来源**：thirdspace `.obsidian/plugins/thirdspace-dashboard/main.js`（GitHub 风格 365 天热力图 + 5 级 + SVG 蛇动画）
- **落地建议**：会话活跃度热力图（侧栏底部）。

#### 71. Markdown 内嵌 Todo — 🟢 P2
- **来源**：thirdspace（`## 今日Todo` + ✅-时间戳 切换）
- **落地建议**：笔记/todo 内嵌面板。

#### 72. 项目状态解析 — 🟢 P2
- **来源**：thirdspace（🟢🟡🔴 + 里程碑 from `product-status.md`）
- **落地建议**：状态徽章解析。

#### 73. 技能注册表解析器 — 🟡 P2
- **来源**：thirdspace（技能注册表解析）
- **落地建议**：与 107 互补。

#### 74. 编号文件夹 vault 约定 — 🟢 P3
- **来源**：thirdspace（`00-系统`…`99-归档` + 每夹 `WORKSPACE.md`）
- **落地建议**：vault 组织约定（文档侧）。

#### 76. 工具注册矩阵 — 🔴 P3
- **来源**：CLI-Anything `cli_hub/matrix.py` + `registry.json`（`kind` harness/public/skill/native/api + list/search/info/install/update 升级 + 1h 缓存远程 JSON）
- **落地建议**：应用商店式工具标签页；远期。

#### 92. Socket.IO RPC 控制面板 — 🔴 P3
- **来源**：Neuro `socketioServer.py`（enable/disable LLM/TTS/STT/movement/multimodal/twitch + cancel_next/abort_current/nuke_history + memory CRUD + custom_prompt priority + signals.sio_queue 后台 drain + 连接时重放所有 status）
- **落地建议**：远程控制各子系统；远期。

#### 93. 反应式信号广播 — 🟡 P2
- **来源**：Neuro `signals.py`（setter 触发 `sio.emit` 状态变更自动推）
- **落地建议**：状态变更自动推送到面板。

#### 94. 反射记忆循环 — 🔴 P3
- **来源**：Neuro `modules/memory.py`（ChromaDB 持久 + 每 20 条生成 Q&A 分割 `{qa}` 向量入库 + `get_memories(query)` 距离排序）
- **落地建议**：V5 已有类似；可选借鉴 Q&A 生成模式。

#### 99. 富控制台彩色状态 — 🟢 P3
- **来源**：agend `cli.py`（Rich console，=== 蓝 / ✅ 绿 / ⚠️ 黄 / 红）
- **落地建议**：终端面板（若有）配色。

#### 100. Glass-morphism 设计语言 — 🟢 P2
- **来源**：N.E.K.O `AppLayout.vue` CSS（`backdrop-filter: blur(32px) saturate(160%)` + 半透明背景 + inset box-shadow + scale/translate/blur 页面过渡）
- **落地建议**：面板视觉升级（可选主题）。

#### 101. Host-Wrapper + 挂载渲染器解耦 — 🔴 P3
- **来源**：N.E.K.O `window.NekoChatWindow = { mount, unmount }`（React 聊天是自包含库挂载到 vanilla JS host + host API `host.pushMessage`）
- **现状对照**：当前面板是单体 HTML。
- **落地建议**：远期重构为挂载 API；当前单体够用。

#### 103. i18n 宿主桥 — 🟢 P2
- **来源**：N.E.K.O `i18n.ts`（`window.safeT`/`window.t` 回退硬编码英文 + `{{var}}` 插值 + renderer 无 i18n 依赖）
- **落地建议**：面板 i18n 桥接（多语言）。

#### 105. 主题提示气泡 — 🟢 P2
- **来源**：N.E.K.O `TopicHintBubble.tsx`（proactive 开场白 topic-hint 块）
- **落地建议**：空会话时显示开场白气泡。

#### 106. 选择提示卡 — 🟢 P2
- **来源**：N.E.K.O `ChoicePrompt`（composer 锚定选项按钮 + accept/decline/later）
- **落地建议**：模型提供选项时输入框锚定按钮。

#### 108. 可组合技能依赖图 — 🔴 P3
- **来源**：skills-main `grill-with-docs/SKILL.md` 调用 `/grilling` + `/domain-modeling`
- **落地建议**：技能依赖图可视化；远期。

#### 110. HITL 脚本模板标签页 — 🟢 P3
- **来源**：skills-main `diagnosing-bugs/scripts/hitl-loop.template.sh`
- **落地建议**：技能详情内可运行脚本标签。

---

## 落地路线建议（按优先级分组）

### P0 — 核心体验必做（先做这批，体感质变）
1. **会话管理三件套**：#1 侧边栏分组 + #2 右键菜单 + #3 搜索面板（Ctrl+K）
2. **命令/快捷键**：#14 命令面板 + #15 全局快捷键矩阵
3. **流式渲染升级**：#17 Markdown+KaTeX+Mermaid + #18 代码块头栏 + #21 思考计时器
4. **工具卡完善**：#8 展开/折叠+状态色
5. **状态栏**：#23 底部固定状态栏

> P0 全部完成 ≈ 面板从「能用」到「好用」的临界点。

### P1 — 高价值（P0 后下一轮）
- 渲染：#19 diff 折叠 + #20 流式分批淡入 + #22 中断标记
- 工具：#9 全局显隐 + #11 在飞工具条 + #12 预览截断 + #13 自动展开
- 长会话：#7 虚拟滚动 + #39 按天分页
- 后台任务：#43 迭代横幅 + #44 计数器 + #45 停滞检测 + #46 文件变更 + #47 状态机 + #48 优雅停止
- 记忆：#51 三层架构 + #68 Pod 就绪门控
- 活动：#80 实时活动流 + #83 双速轮询 + #49 事件分类法
- 其他：#16 Slash + #25 拖拽上传 + #27 大纲 + #29 模型切换 + #30 推理档位 + #31 分叉 lineage + #40 上下文重置双模 + #63/64 会话分组状态 + #102 结构化块

### P2 — 锦上添花
- 工具：#10 diff 抽屉 + #35 主调用卡 + #79 意图卡 + #82 摘要形态
- 检查器：#28 右侧可调 + #85 三栏 shell + #107 技能卡片
- 图谱：#60 力导向图 + #62 色键图例 + #77 星座画布
- 后台：#53 熔断 + #54 断点恢复 + #55 熵控 + #57 契约面板 + #58 缓存顺序 + #59 质量分级 + #52 外部验证
- 其他：#65/66/91 统计/转录/历史 + #70 热力图 + #71 Todo + #100 玻璃态 + #103 i18n + #105/106 提示气泡 + #31/32/33/34 等

### P3 — 远期/实验
- #41 关系方 + #56 Dream + #76 工具矩阵 + #84 DAG + #89 节点调色板 + #92 Socket.IO RPC + #94 反射记忆 + #101 挂载解耦 + #108 技能依赖图 + #110 HITL 脚本 + #61 布局循环器 + #78 波形心象 + #74 vault 约定 + #99 控制台配色

---

## 与现有基线的对照提示

- 当前 `core/conversation-tree/index.html`（2164 行）已实现：树画布（`ConvNode`/`TreeView`/`calcLayout`/`applyTransform`）、会话列表（`renderSessions`/`switchSession`/`createSession`/`deleteSession`）、工具卡（`toolCardHtml`）、思考块（`thinkBlockHtml`）、用量条（`updateUsageBar`/`fmtTok`）、分支（`branchFromNode`）、模态（`showModal`/`hideModal`）、Toast（`toast`）、记忆项（`memItem`）、Markdown（`mdRender`）、SSE（`_stream_events`/`_chat_stream_events`）。
- 后端 `core/conversation-tree/server.py` 已有：会话持久（`_load_sessions`/`_save_sessions`）、树持久（`_make_tree_for`/`_load_tree_for`）、搜索（`_search_dicts`）、流式（`_deepseek_stream`/`_stream_hermes_gateway`/`_chat_stream_events`）、工具执行（`_execute_chat_tool`）。
- 上述已实现项不在本清单新增范围；本清单聚焦「升级」与「补齐」。
- 禁区：`core/conversation-tree/index.html` 由主 agent 独占，本清单仅作设计参考，不直接改其代码。

---

## 参考项目索引（便于深挖）

| 项目 | 类型 | 核心借鉴点 |
|------|------|-----------|
| hermes-studio-main | Vue3+Pinia+Naive UI 聊天面板 | 会话管理/工具卡/搜索/上传/流式/快捷键/模型切换（最全） |
| N.E.K.O-main | React 聊天 + Vue 插件管理 + Electron host | 结构化消息块/玻璃态/挂载解耦/i18n 桥/导出预览 |
| OpenHarness-main | Python CLI 编排框架 | 三层记忆/外部验证/熔断/心跳恢复/Dream 沉淀/契约 |
| sleepless-agent-main | Python CLI 长时守护 | 状态机/迭代计数/停滞检测/文件变更进展/事件分类法 |
| Innerlife-main | React 聊天 + 观察者抽屉 | 主调用卡/记忆图追踪/调用配色/按天分页/上下文重置双模 |
| Reverie-main | React+Vite webui | 记忆星座/波形心象/活动流/双速轮询/工具摘要 |
| cognee-main | React 前端 + Python 后端 | 力导向图/会话按日期分组/统计卡/Pod 门控/分区导航 |
| homerail-main | Vue3 agent-ui | 三栏 shell/会话子运行/工具卡/进度记分卡/persona/Token 面板/DAG 覆盖层 |
| ugc-video-ai-agent-workflow | Next.js v0 生成 | cmdk 命令面板/执行面板/运行历史/节点调色板/模板菜单 |
| agend-main | Python CLI 任务运行 | SQLite 会话/会话锁/Todo diff/Popen 流式 |
| Neuro-master | Socket.IO 控制面 + 向量记忆 | RPC 控制面板/反应式信号/反射记忆循环 |
| skills-main | 技能目录 | 技能=目录卡片/可组合依赖图/支持文档手风琴/HITL 脚本 |
| thirdspace-vault-template-main | Obsidian 插件 | 365 天热力图/Markdown Todo/项目状态解析/编号 vault |
| CLI-Anything-main | Python CLI 集线器 | REPL 皮肤/工具注册矩阵 |
| herdr-master | Rust TUI 多路复用 | 命令面板覆盖层/侧栏分屏 |
| strict-agent-loop-main | Codex skill 协议 | 迭代横幅/磁盘状态 schema/双模/停止条件检查 |
| Live2DPet / OmniVoice / SenseVoice / graphify-8 / tldraw-skill-main | （无相关 UI） | 不借鉴 |

---

*本清单为设计参考，非实施承诺。落地前需对照当前 `index.html` 与 `server.py` 实际状态复核（基线可能已演进）。*
