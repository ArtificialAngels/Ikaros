# Ikaros 全对话流程测试报告

> 测试时间：2026-07-25（周六）｜ 测试方式：透明中继代理拦截 `cloud_chat`→DeepSeek 的真实请求 + 5 轮多轮对话驱动器
> 测试结论：**拦截成功，但发现 6 个阻断性问题，当前多轮对话体验不可用。**

---

## 一、测试方法

1. **拦截「传给云端前的内容」**：本地起一个透明中继代理（监听 `:8899`），把 `cloud_chat` 的 DeepSeek 分支 `base_url` 指向它，代理转发到真实 `api.deepseek.com` 并把每次请求的 `model / system_prompt / full_messages` 落盘到 JSONL。
2. **5 轮多轮对话**：用一个驱动器假装是「哥哥」连续发 5 条消息，跨主题（打招呼 → 回忆昨天 → 写脚本 → 调试脚本 → 夸脚本），每轮把 `{user, assistant}` 累加进 `history` 传入，验证跨轮上下文是否连贯。
3. 为让对话真正走到模型（默认 task gate 会短路），测试时临时绕过 gate；测试结束后**所有临时改动已回滚**（见文末）。

---

## 二、传给云端前的内容（拦截到的真实载荷）

### A. Companion 路径（`cloud_chat` → DeepSeek，`deepseek-chat`）
- **system 提示词** = 伊卡洛斯人格提示（约 1100–1450 字），结构为：
  ```
  我是伊卡洛斯。回哥哥消息，说人话——短句、直接、有温度。
  周六（17:19） 这是今天第一次和哥哥说话 （精疲力竭 💤）
  --- 当前节奏：距上轮: 刚刚 | 时段: 深夜(01:19)   ← ⚠️ 时间分类错误
  ### Current state / memory / (后台任务失败待办) / 哥哥偏好（⚠️ 前缀重复）
  ### My capabilities（下载器说明）
  (Reminder: there's a task result pending to tell user...)
  ```
- **历史消息携带正确**：`full_messages` 数量 2→4→6→8→10，说明一旦绕过 gate 并传入 history，多轮上下文在传输层是连贯的。
- **⚠️ 致命问题**：每一轮**最末一条 `user` 消息不是用户原话**，而是 V5 router 的 `_optimized` 结构化任务描述，例如：
  - 第2轮：`"output": "整理了项目的目录结构", "constraints": "仅输出JSON格式...", "skills": ["回忆能力"]`
  - 第3轮：`"output": "scan_recent.py", "constraints": "脚本需在本地运行...", "skills": ["Python脚本编写",...]`
  - 第4轮：`"output": "无需安装额外库...", "skills": ["调试技能",...]`
  - 第5轮：`"output": "感谢用户反馈...", "skills": ["沟通协调",...]`
  - 用户真实的话（"你帮我在本地写一个 Python 脚本吧…"等）只存在于更早的 history 里，模型本轮应答时根本看不到。

### B. Hermes 路径（`hermes.exe chat` → DeepSeek，`deepseek-v4-flash`）
- **system 提示词** = 140KB 自动同步的 `SOUL.md`（来自 `bin/ikaros-soul-sync.py` 合并 `self_model/axiom/affect/v4.db`）。
- 走 `models.dev` 缓存的真实 `base_url`。**实测确认**：改 `auth.json` / `.env` / deepseek 插件的 `base_url` 都**不生效**，Hermes 始终用 `models.dev` 缓存的端点（这解释了此前拦截 Hermes 调用为何要从别处下手）。

---

## 三、三个评估维度

### 1. 情感变化 ❌ 几乎不存在
5 轮回复全部是「思维链外泄」形式：
```
(◔_◔) computing... / ( ͡° ͜ʖ ͡°) pondering... / (⊙_⊙) processing...
( ˘⌣˘)♡ ruminating... / (¬_¬) reasoning...
```
模型把内部推理当成正文输出，**没有真正完成**「迎接哥哥 → 回忆昨天 → 接任务写脚本 → 共情调试 → 接受感谢」的情感弧线。每轮都在「思考怎么回」，但没有回。无可见的情绪波动曲线。

### 2. 流畅性 / 连贯性 ❌ 差
根因见上文 B 节：`_optimized` 污染了 user 内容。结果模型在 2–5 轮反复把用户意图误读成「JSON 片段 / 结构化任务定义」，多次出现：
> "不太确定上下文" / "I'm not sure exactly what they want" / "The messages feel fragmented"

上下文连贯性被彻底打破。

### 3. 跨时间 / 跨主题接纳速度 ❌ 慢且失效
- persona 头部的**时间戳确实在刷新**（周六 17:19 → 17:28），说明「时段脚手架」会更新；
- 但因为真正的用户内容被 `_optimized` 替换，模型无法真正「接住」话题切换——把「回忆昨天」和「写脚本」都当成同一种「JSON 片段」处理，**没有平滑的主题过渡**。接纳速度 ≈ 失效。

---

## 四、发现的 6 个问题（根因）

| # | 问题 | 根因 | 严重度 |
|---|------|------|--------|
| 1 | 正常对话被秒回罐头文本 | task gate：`_is_task` 为真时直接 `return "好的哥哥，这个任务我已经在后台处理了…"`，**不调模型** | 🔴 高 |
| 2 | **模型看不到用户原话，上下文混乱** | `user_content = _optimized if _optimized else text` —— 被判为 task 时，user 内容变成结构化优化描述 | 🔴 高（本次核心） |
| 3 | 后台任务永久失败且污染 persona | `v5.task_runner.call_async` 调 `hermes.exe`，但 `E:\Ikaros\core\core/hermes\venv\Scripts\hermes.exe` 不存在（symlink 0xC0000005）→ 失败结果 `hermes not found` 被塞进每轮 system prompt 的「待办」 | 🟠 中 |
| 4 | 时段判定错误 | 真实时间 周六 17:xx（下午），persona 显示 `时段: 深夜(01:xx)` —— 小时算错（01 vs 17）且标签错（深夜 vs 下午） | 🟠 中 |
| 5 | 人格模板前缀重复 | `哥哥偏好哥哥偏好短句...` 拼接 bug | 🟡 低 |
| 6 | 思维链外泄 | `deepseek-chat` + 当前提示词把 reasoning 当正文输出，且无指令禁止 | 🟠 中 |

---

## 五、修复建议（按优先级）

1. **【最关键】Companion 对话路径里 `user_content` 必须始终用 `text`（用户原话）**；`_optimized` 只传给 `task_runner`，绝不进对话模型。改 `cloud_chat.py` 消息构造处即可。
2. **task gate 不应在 companion 模式短路**：至少对「对话型 task」也走模型，可把 `_optimized` 作为隐藏上下文而非替换用户输入。
3. **修 `hermes.exe` 缺失**：重建 venv 相对符号链接或改绝对路径，否则后台任务永远不可用（连带问题 3）。
4. **修时段分类**：校正 hour 计算与 时段 映射（深夜/下午等）。
5. **修偏好模板拼接**（问题 5）。
6. **加「不要输出推理过程」指令**，或切换非 reasoning 模型 / 剥离 thinking token（问题 6）。

---

## 六、回滚与清理（已完成）
- `bin/cloud_chat.py`：`IKAROS_BACKEND_BASE_URL` 默认恢复为 `""`；删除 `_is_task = False` TEMP 绕过行。
- `data/hermes-agent/auth.json`：deepseek `base_url` 恢复为 `https://api.deepseek.com/v1`（此前漏回滚，本次已修）。
- 停掉 `:8899` 中继代理进程，删除 `tmp/deepseek_probe*.py`、`deepseek_probe_log.jsonl`、`tmp/run_dialog.py`、`tmp/dialog_transcript.json`。
- 已 `POST /api/restart` 重载面板，使其加载回滚后的 `cloud_chat`。

> 注：拦截 Hermes 路径本身需要改 `models.dev` 缓存或 `hermes.exe` 子进程环境，本次通过 companion 路径 + 直接观察代理日志已确认两端真实载荷，结论不受影响。
