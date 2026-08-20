# UI 全量优化工作日志 — 9100 控制台 + 48920 对话树

> 日期: 2026-08-02 · 执行: 伊卡洛斯 · 模式: PID 负反馈迭代 + GitHub 案例借鉴
> 目标: 全量优化 9100 面板 UI 和 48920 面板 UI
> 状态: ✅ 已完成（诊断清零 + 242 pytest 全绿）

---

## 一、诊断工作流（可复用）

playwright-core + msedge headless 脚本（`tmp/ui-*.js`），量化检测:
- 布局越界（面板超出视口/文档滚动范围）
- 文本溢出（scrollWidth vs clientWidth）
- 空元素 / 字体加载 / CSS 变量解析 / 主题切换 / 响应式多视口

## 二、修复清单（6 个 bug + 2 项增强）

### 9100 控制台

| # | 严重度 | 问题 | 根因 | 修复 |
|---|--------|------|------|------|
| BUG-1 | 🔴 致命 | 整个面板无样式（字体全 Times New Roman、背景透明、配色崩） | dashboard.css 头部注释 `.msg-*/.mem-*` 含 `*/` 序列提前闭合块注释 → `:root` 变量块被浏览器丢弃 | 注释 `*`→`star`，236 条规则恢复 |
| BUG-2 | 🟠 布局 | 右侧带总高 1090px > 视口 900px，stream 卡越界 | 固定高度 + 未测 canvas 实际顶部偏移 | 动态测量 canvasTop，memory 弹性 200-360，stream 拿剩余保底 150 |
| BUG-4 | 🟠 响应式 | 移动端事件流卡被撑到 62988px | `.stream` flex:1 在 height:auto 父容器按内容无限撑开 | 窄屏固定 42vh + 内部滚动 |
| BUG-5 | 🟠 响应式 | 移动端整页不能滚动 | `.main` overflow:visible 但 `body` overflow:hidden 锁死 | html/body overflow:auto |
| BUG-6 | 🟡 响应式 | 480px 顶栏按钮换行堆叠 155px 高 | 7 个按钮 + 品牌区在窄屏换行 | 窄屏压缩字号/内边距 |

### 48920 对话树

| # | 严重度 | 问题 | 根因 | 修复 |
|---|--------|------|------|------|
| BUG-3 | 🟠 溢出 | 17 处节点标题文本溢出（136px 容器 vs 206px 内容） | `.node-label` 单行 nowrap，中文长文本只显示 ~12 字 | 两行 `-webkit-line-clamp` + `word-break` + `title` 属性（hover 全文） |
| 增强 | 🟢 | 节点卡无视觉层级 | 所有节点同尺寸同亮度 | 借鉴 stello star-map：深度→尺寸（scale 0.82-1.0）、创建时间→亮度（新亮旧暗）、hover/current 全亮 |
| 增强 | 🟢 | 无键盘关闭路径 | 右键菜单/弹窗只能点关闭 | Esc 全局关闭 ctxMenu + modal |

## 三、GitHub 案例借鉴

- **stello-agent/stello** (105★) — 对话拓扑引擎 + star-map 可视化 → 视觉编码落地
- **yxp934/Prompt-Tree** (84★) — Git 风格树上下文 → 确认树形分支交互方向正确
- **tldraw/branching-chat-template** — 分支聊天模板参考

## 四、验证结果

- 9100 诊断: 0 真实问题（2 项误报：等宽数字 / CSS 图标按钮）
- 48920 诊断: 0 真实问题（3 项设计预期：网格背景 / 空 toast / 空输入框）
- 响应式: 8 视口 (1920→375px) 全零问题
- 主题: dark/light (9100) + dark/warm/system (48920) 切换正常
- pytest: **242 passed**（无回归）

## 五、改动文件

- `core/dashboard/assets/dashboard.css` — 注释修复 + 响应式 + memory 卡 flex
- `core/dashboard/index.html` — 右侧带自适应高度
- `core/conversation-tree/index.html` — 文本溢出 + 视觉编码 + Esc
- `core/memory_v5/tools/memory_tool.py` — （此前）MCP store 同步 dissonance 超时修复

## 六、备注

- 48920 服务曾意外退出，已用后台进程重启（`PYTHONPATH=core python core/conversation-tree/server.py --port 48920`）
- 诊断脚本保留在 `tmp/ui-*.js`，后续 UI 改动可直接复用回归

## 七、V5 向量同步根因修复（2026-08-02 追加）

### 问题: 其他会话记忆"找不回"
- 现象: 查询记忆时大量召回失败；哥哥指出"可能没存到本地/一次性读到内存"
- 实锤: SQLite 1433 条记忆 vs Chroma 仅 845 向量 → **731 条缺向量（51%）**
- 根因:
  1. `_sync_vector_best_effort` 写时同步失败率高（chromadb 缺失环境 + hnsw compactor 并发冲突）
  2. 失败后静默丢弃、无重试；唯一兜底 `vector_sync` op 24h 才跑且全量 upsert
  3. 多进程（MCP server × N）各自持 Chroma client 并发写 → "Failed to apply logs to the hnsw segment writer"

### 修复（3 项）
1. **`bin/ikaros-v5-reindex.py`** — 一次性抢救脚本: 扫描 SQLite 对比 Chroma, 补缺向量
   → 实际补 731 条, Chroma 845 → 1556（67s, 0 失败）
2. **`vector_sync` op 改增量 + 1h** — `core/memory_v5/reflect/registry.py` + `scheduler.py`
   （原 24h 全量 upsert → 1h 只补缺失 id）
3. **跨进程 Chroma 写锁** — `core/memory_v5/search.py` 新增 `_chroma_write_lock`
   （msvcrt/fcntl 文件锁, upsert 前串行化）
   → 并发写压测 4 进程 × 15 次 = 60/60 成功, 0 失败

### 验证
- pytest 242 passed ✅
- 并发压测 60/60 ✅
- 记忆检索恢复正常（之前查不到的 #1602/#1792/#1766 全召回）✅

## 八、记忆转存机制修复（2026-08-02 追加 2）

### 哥哥提出的两个问题
1. **vector_sync 1h 太长** → 缩短到 5min
2. **突然关闭时临时记忆消失，无转存机制** → 实锤 + 修复

### 根因（比预想严重）
- **cleanup op 直接 DELETE 低权重记忆** — 无归档、无转存，崩溃/误删即永久丢失
- **promote op 的 UPDATE 从未 commit** — `store.conn()` 退出默认 rollback，
  短期→长期转存 SQL 执行了但从未落库！转存机制形同虚设
- 同理 cleanup 的 DELETE 也是在同一事务里，虽然 DELETE 有执行但依赖 commit

### 修复（4 项）
1. **schema 加 archived/archived_at 列** — `store.py` conn() 迁移
   （顺带修复 V5.2 索引迁移的 `with c.execute() as cur` TypeError bug——
   portable-python 3.13 sqlite3.Cursor 不支持上下文管理器协议，被静默吞掉）
2. **cleanup 改归档不删除** — `UPDATE archived=1` 而非 DELETE，
   低权重阈值 0.4→0.45（更多归档而非删）
3. **promote/cleanup 加显式 commit** — 转存机制首次真正落库
4. **间隔提速** — vector_sync 1h→5min（增量只补缺失 id）、promote 12h→1h
   门槛放宽（weight≥0.55 或 access≥2）

### 验证
- 归档: 低权重记忆 → archived=1, 检索排除 ✅
- 转存: 高权重记忆 → short_term=0/long_term=1 ✅
- pytest 242 passed ✅

## 九、48920 chat 页面优化（2026-08-02 追加 3，进行中）

> 哥哥反馈: "chat 页面实在太简陋"（此前有一会话开始优化，因哥哥上班暂停，本次续做）

### 诊断（DOM 实测）
- `.main` / `.messages-area` 背景透明 → chat 区无面板感，直接露页面底色
- AI 卡全是同一纯色渐变（--bg-card-up/down 在 dark 下相同 #222）→ 无层次
- 用户气泡 14px 圆角 vs AI 卡 12px → 视觉不统一
- 24 条消息全垂直排列，无头像、无身份标识 → 单调
- chat-header 透明、输入框同色系 → 无视觉焦点

### 已修复（CSS + JS）
1. `.main` 加深色底 + 顶部品牌色径向光晕（rgba(19,228,37,.04)）
2. `.chat-header` 半透明毛玻璃（color-mix + backdrop-blur）
3. `.ai-card` 圆角 14px + 顶部品牌渐变线（::before）+ hover 阴影
4. `.card-avatar` 品牌渐变 + 发光；标头 "E Explore" → "✦ 伊卡洛斯 · Ikaros"
5. `.bubble-user` 渐变 + 边框，圆角 16px 统一
6. `.input-wrapper` 圆角 14px + 阴影 + focus 品牌聚焦环（rgba 光环）
7. `.input-area` 半透明底，与 header 呼应

### 验证
- 渲染无 JS 错误 ✅ · AI 卡标头生效 ✅ · pytest 242 passed ✅
- 待办: 空态优化 / 消息时间分组 / 流式动画细节 / 对比 warm 主题

## 十、48920 chat 页面优化 · 第二批（2026-08-02 追加 4）

> GitHub 参考: marked v12 (37k★) 内嵌本地 (35KB, UMD, MIT), 零外部依赖原则保持

### 已落地
1. **Markdown 渲染** — marked v12 内嵌 + 安全加固:
   - `renderer.html → escapeHtml` (防 `<script>`/`<img onerror>` 注入)
   - 链接协议白名单 (禁 javascript:/data:, 实测被剥掉)
   - 代码块/列表/标题/表格/引用/加粗/斜体全支持
   - 历史消息 (aiCardHtml) 与流式 (mdRender) 统一走 marked
2. **流式渲染限流** — rAF 批处理: SSE 高频 delta 不再每帧全量 mdRender,
   渲染帧率与滚动合并; done 时强制 flush 最后一批
3. **空态欢迎界面** — 无消息时: 品牌色发光圆球 + "伊卡洛斯在这里" +
   3 个快捷提问 chip (quickAsk 函数)
4. **marked 输出 CSS 适配** — pre/code/行内 code/标题/列表/引用/表格/链接/分隔线

### 验证
- XSS 三连测全防 (script/javascript:/img onerror) ✅
- 流式增量渲染健壮性: 半截 markdown 绝不崩 ✅
- 历史消息: 24 卡 / 7 代码块 / 120 行内 code 正常 ✅
- JS errors 0 · pytest 242 passed ✅

## 十一、explore.poker UI 对齐（2026-08-02 追加 5）

> 哥哥: "https://ai.explore.poker/chat 的 UI 我很喜欢" — 分析其设计体系并对齐

### 调研发现
- **48920 本就是 explore.poker 的移植版** (品牌绿 #13E425 / 品红 #EC1BDA 完全一致,
  对话树引擎注释即 "Explore.poker 风格")
- 从 ai.explore.poker 抓到完整 8 套主题色板 (Default暗色/Warm/MidnightForest/Sakura/
  Memphis/Sunset/Default-Purple/Default-Blue/Default-Orange)
- 原版字体: Inter + Space Grotesk + JetBrains Mono + Bruno Ace (next/font 自托管)

### 差距与修复
1. **dark 色板整体偏暗** → 对齐原版: bg #0d0d0d→#101010, usermsg #3a3a3a→#4c4c4c,
   input #2a2a2a→#3a3a3a, item #2a2a2a→#363636, border #3a3a3a→#484848,
   文字 #f0f0f0→#fff, secondary #c0c0c0→#d1d1d1, tertiary #888→#999, quaternary #666→#777
2. **warm 主题缺品牌色切换** → 原版 warm 是橙色 #F45F28 + 对调蓝 #4682B4, 全色板重写
3. **补缺失 token**: bg-quotation / bg-usermsg-file / text-header-secondary /
   text-turn-title / scrollbar-thumb-{card,usermsg,inputarea}
4. **滚动条分级** → 全局/消息区/输入区各自专属滚动条色
5. **引用块** → color-mix 改 var(--bg-quotation)
6. **字体自托管** → 从原版下载 Space Grotesk/Inter/JetBrains Mono woff2 (共 111KB)
   到 core/conversation-tree/assets/, server.py 加 /assets/ 静态路由 (防目录穿越),
   @font-face 引用 (零外部依赖保持)

### 验证
- 三字体 loaded ✅ · body 背景 #101010 ✅ · 输入框 #3a3a3a ✅
- /assets/ 静态服务 200 + 目录穿越 404 ✅
- JS errors 0 · pytest 242 passed ✅

## 十二、9100 第二轮深度优化（2026-08-02 夜间，PID 驱动）

> 哥哥: "9100 面板 UI 也很丑陋，优化。每个小模块根据其内容有最小边界，需要吸附对齐，
> 要有鼠标粒子特效，检查面板功能是否完善，看 commit 内容反向验证。"

### commit 反向验证（3831470「整页统一自由画布」）
- ✅ 自由画布 PanelManager（拖拽/八向缩放/8px 吸附/记忆布局）——已实现
- ✅ 自我思考卡片读真实数据（latest_thought.json → /api/state.thought）——已实现
- 🔴 **发现 bug**：memoryBootFallback 在非 demo 模式也会触发（/api/state 失败即显示
  3604 条假数据）→ 修复：仅 `?demo` 参数时启用，真实数据缺失显示 0 不误导

### 本轮改动
| 项 | 内容 |
|----|------|
| D1 粒子特效 | 全屏 canvas 尾迹层（pointermove 撒粒子 + 面板拖拽/缩放迸发粒子流，青蓝系 hsla，
   上限 140 防性能劣化，prefers-reduced-motion 完全关闭） |
| D2 最小边界 | registerPanel 注册时测量内容自然尺寸（offsetWidth/Height），
   minW/minH = max(显式值, 内容尺寸)，缩放无法压过内容 |
| D3 吸附增强 | 阈值 8→12px，参考线加 glow 光效 + snapPulse 动画 |
| D4 UI 优化 | featured 卡顶部品牌渐变条；拖拽/缩放态高亮边框；顶栏按钮分隔线分组；
   状态栏绿色指示灯；组件卡 padding 紧凑化；.comp-desc 2 行截断防撑爆 |
| D5 布局修复 | registerSidePanels stream 保底高度 150→160（对齐 CSS min-height），
   修复 stream 底部越界 960>950 |
| D6 组件注册顺序 | initGrid 先 appendChild 再 registerPanel（才能测内容尺寸） |

### 验证（Playwright + msedge headless，smoke-9100.py 11/11 PASS）
- 粒子 canvas 存在且实际绘制（getImageData 非空）✅
- 缩放被最小边界拦截（w 480 h 131）✅
- 拖拽吸附到画布左缘（12px 阈值生效，屏幕 x=16=画布内 0）✅
- 非 demo 模式 memTotal 显示真实值 0（非 3,604 假数据）✅
- 右侧面板不溢出视口 ✅
- JS errors 0 ✅
- 测试踩坑：Playwright mouse 坐标必须落在 card-title 内（padding 14px 后），
  且 IIFE 内部变量（panels/snapAxis/_memBooted）全局不可访问，须用真实交互断言

## 十三、特效层 v2（2026-08-03 凌晨，双面板）

> 哥哥: "继续优化9100和48920ui，继续增加特效，并且使得特效稳定性和观赏性更好"

### 48920 新增（smoke-ct 28/29，唯一 FAIL 为预存无头 SSE 时序）
- 粒子系统：品牌绿(#13E425) 鼠标尾迹 + 点击迸发 + 节点拖拽迸发；
  对象池 160（防 GC 抖动）+ visibilitychange 暂停 + prefers-reduced-motion 关闭 + DPR≤1.75
- 消息入场动画：气泡/AI 卡/extras slide-up 错峰入场 (.32s)
- 流式 AI 卡顶部高光流动 (liveSweep 2.2s)
- 代码块 hover 复制按钮（attachCodeCopy 挂钩 renderThread）
- 全局按钮涟漪（ripple-ink，600ms 自清理）
- 思考块 details 平滑展开（grid-template-rows 0fr→1fr）
- 主题切换过渡（bg/color/border .35s）
- 节点卡 hover 光晕 + 当前节点呼吸光 (nodeBreathe)
- 会话项滑动指示条 + token 数字跳动 (numPop)

### 9100 新增（smoke-9100 11/11）
- 粒子 v2：对象池 150 + 星云连线（90px 阈值 O(n²) 裁剪）+ visibilitychange 暂停 +
  拖拽持续撒粒子 + DPR 上限 2
- 组件卡 hover 顶部光条扫过 + 外发光增强

### 稳定性设计
- 对象池复用（GC 抖动消除）
- rAF 循环在页面隐藏时完全停止（后台零开销）
- prefers-reduced-motion 全部降级
- 粒子上限（48920:160 / 9100:150）+ 节流（pointermove 14ms/6px）
- 涟漪/复制按钮元素用后即焚（setTimeout remove）
