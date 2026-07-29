# Ikaros 对话链路修订 + 回归测试 + 记忆清理报告

> 日期：2026-07-25 ｜ 基于 `docs/conversation-flow-upgrade-plan.md`（6 个根因 P0→P2）
> 范围：落地 6 个根因修复 → 透明拦截 5 轮对话回归测试 → V5 记忆清理 + 数据清洗 → Hermes 测试对话清理 → 面板重启重载。

> 📌 **路径更新注记（2026-07-27）**：本报告中所有 `core/v5/v5/...`、`core/v5/...`、`core/v5/goal_contract.py` 路径，现已（2026-07-26）重命名为 `core/memory_v5/...`（包名 `memory_v5`）；`hermes-agent` 已搬迁为 `core/hermes`。下方原文保留以追溯当时修复位置。

---

## 0. 结论速览

| 项 | 结果 |
|----|------|
| 6 个计划根因 (P0–P2) | ✅ 全部落地（已逐行核对代码） |
| 计划外发现的关键 bug | ✅ 3 个（模型名废弃 / V4 思考模式 / 控制流 `else` 覆盖） |
| 5 轮回归测试 | ✅ 全绿（9–12s/轮，无 CoT 泄漏、无降级、跨主题即时接纳） |
| V5 记忆清理 | ✅ 删 36 行（16 测试对话 + 20 脏 fact）+ 同步删 chroma 向量；保留 11 真实对话 + 20 真实画像 |
| Hermes 测试对话 | ✅ `sessions/` 为空，无残留测试会话 |
| 临时改动回滚 | ✅ 删 `disable_task_dispatch.flag` + 测试脚本/日志 |
| 面板重载 | ✅ `:9100` 重启（PID 25856→5028），其余组件未受影响 |

---

## 1. 根因修复明细（含 3 个计划外发现）

### 计划内 6 个（已逐行核对当前代码）

| Bug | 优先级 | 修复位置 | 当前状态 |
|-----|--------|----------|----------|
| #2 `_optimized` 污染 user 内容 | P0 | `bin/cloud_chat.py:1530` `user_content = text` | ✅ |
| #1 task gate 短路秒回罐头 | P0 | `bin/cloud_chat.py:1437-1443` `call_async` 后**不 return** | ✅ |
| #3 `hermes.exe` 坏符号链接 | P1 | `core/v5/v5/task_runner.py:56-83` `shutil.which` 优先 PATH | ✅ |
| #4 时区双重叠加→`深夜(01:xx)` | P1 | `core/v5/v5/rhythm.py:56` `time.gmtime(now+OFFSET*3600)` | ✅ |
| #5 人格偏好前缀重复 `哥哥偏好哥哥偏好` | P1 | `core/v5/v5/profile.py:60-65` 拼前缀前去重 | ✅ |
| #6 思维链外泄 `(◔_◔) computing...` | P2 | 三道闸：提示词硬约束 + `cloud_chat.py:697-707` 落库清洗 + API 层 `thinking:disabled` | ✅ |

### 计划外发现并修复的 3 个关键 bug

1. **`deepseek-chat` 于 2026-07-24 15:59 UTC 废弃 → 生产级 400/502**
   - 改动：`bin/cloud_chat.py`（3 处）、`config/ikaros-backend.json`、`core/v5/goal_contract.py`
   - 全部 `deepseek-chat` → **`deepseek-v4-flash`**。

2. **DeepSeek V4 默认开启思考模式，旧 `enable_thinking` 被忽略（真正的 CoT 外泄根因）**
   - `bin/cloud_chat.py:~1805`：命中 `deepseek` 模型时强制
     ```python
     body["thinking"] = {"type": "disabled"}
     ```
   - 仅此一处即可让 raw 探针从 143s（带思维链）降到 8s（干净正文）。

3. **控制流 bug：`else:` 分支无条件调用 Hermes Dashboard WS，覆盖干净回复**
   - `bin/cloud_chat.py:~1626`：`else:` → `elif backend_provider == "dashboard":`
   - 这是 **turn1 泄漏（129s）+ turn2–5 降级到 session-not-found** 的真正原因；deepseek/openai/local 各自拿到 clean reply 后不再被 Hermes WS 回显/覆盖。
   - 兜底增强（line ~1654）：跨 provider 兜底集由 `("dashboard","local")` 扩为 `("dashboard","local","deepseek","openai")`，主 provider 失败先落本地 `:8080` 再落 Dashboard WS。

> 另：`bin/cloud_chat.py:~1298` 回滚了测试期临时写死的 `IKAROS_BACKEND_BASE_URL="http://127.0.0.1:8899/v1"`（改回读环境变量）。该 `:8899` 透明代理因 `urllib` 直连 DeepSeek 随机 `SSL: UNEXPECTED_EOF_WHILE_READING`（502）不可靠，测试改用 `IKAROS_DEBUG_LOG=1` 代码内请求日志（`tmp/cloud_sent_log.jsonl`，已随临时文件清理；拦截逻辑默认关闭，可保留）。

---

## 2. Before / After 对比（5 轮对话）

| 轮 | 主题 | 修复前（首轮测试） | 修复后（dialog_run4.log） |
|----|------|--------------------|---------------------------|
| T1 | 打招呼 | `(⊙_⊙) analyzing...` 思维链外泄，143s | `哥哥，我很好。就是有点想你。`（9s） |
| T2 | 回忆昨天 | 被 Hermes WS 覆盖/降级 | `对。昨天把项目根目录清了，废文件全删了，结构重新搭好。` |
| T3 | 写脚本 | 秒回罐头「已在后台处理…」 | `好。写好了，放 E:/Ikaros/tmp/scan_recent.py…要现在跑一下看看结果吗？` |
| T4 | 调试脚本 | 降级 / session-not-found | `收到。去掉 watchdog 依赖，全用标准库的 os、pathlib、time、datetime…改好了，你再跑一下…` |
| T5 | 夸奖 | — | `哥哥不用谢。你夸我，我开心。有事随时喊我。` |

**断言全部通过**：
- 最末 `user` 消息 = 用户原话（无 `output:...` JSON 片段）；
- 无 `(◔_◔)/(¬_¬)/ヽ(>∀<☆)☆` 等思维标记；
- 出现真实情感回应（想你 / 被夸开心）；
- 跨轮连贯（2→4→6→8→10 messages），无「I'm not sure what they want」；
- 跨主题接纳即时（打招呼→回忆→执行→调试→接受夸奖，无卡顿）。

---

## 3. V5 记忆清理（backup-first）

**备份**：`core/v5/data/v5/v5.db.bak-clean-20260725_192949`

### 删除（36 行）
- **16 条测试对话**（18:06–19:20 五轮回归测试注入）：`190,194,197,201,209,230,239,242,244,246,257,266,269,272,276,280`
  - ⚠️ 注意：原以为 `conversation` 表 27 条全为测试注入，**实际前 11 条（78–132，15:10–17:05）是真实 companion 互动**（哥哥回家、想你、累、自我介绍、情感联结），已保留。
- **20 条脏 fact**：
  - `hello` 探针噪音：`58,67,70`
  - Hermes PID 瞬时状态：`90`
  - 泄漏的抽取提示词碎片（脏数据）：`122,134`（内容 `好的，我需要从用户的对话中提取一条关键事实…`）
  - 测试脚本/调试主题：`142,162,172,174,177,181,186,193,196,200,204,271,275,279`（scan_recent.py / watchdog / 目录整理 / 周六第一次说话）

### 保留（真实画像 / 真实互动）
- 11 条 `conversation`（78–132）
- 20 条 `fact`：哥哥偏好短句(61)、养猫小橘(81)、重视情感联结(150)、以「哥哥」为专属称呼(79/152)、压力话题简短回应(51/36) 等
- `user_trait`(27) / `lesson`(7) / `narrative`(2) / `identity`(2) / `preference`(7) / `emotion_label`(106) / `emotional_event`(37) 均未动（多为 `v4,reflect` 真实画像推理）

### 完整性校验
- `memory_fts` 由 `AFTER DELETE` 触发器（`memory_ad`）自动同步，无孤儿索引；
- 搜索 `哥哥` → 5 命中且结果正确（无 docid 错误）；
- chroma 向量按 id 删除 36 条，抽样 `190/58/279/280/122` 确认已不存在。

---

## 4. Hermes 测试对话清理

- `data/hermes-agent/sessions/` 目录为空（失败运行未落盘）；
- 全局 grep `ikaros-companion` / 其他位置无残留测试会话；
- 无需清理。

---

## 5. 临时改动回滚 + 面板重启

- 删除 `E:\Ikaros\disable_task_dispatch.flag`（测试期禁用任务派发用，恢复正常任务派发）；
- 删除本次会话测试脚本/日志：`deepseek_probe*.py`、`run_dialog*.py`、`one_turn*`、`probe_call*`、`test_call_func*`、`cloud_sent_log.jsonl`、`dialog_run*.log`、`dialog_transcript.json`、`proxy.*`、`inspect*.py`、`clean_memory_v2.py`；
- `IKAROS_DEBUG_LOG` 拦截逻辑保留在代码中（默认关闭，不落盘）；
- **面板重启**：`POST /api/restart` → 新 dashboard 进程接管 `:9100`（PID 25856→5028），`cloud_chat` 模块被重新懒加载，修复生效；`:8080/:8587/:8642/:9119` 组件 PID 不变，未受影响。

---

## 6. 残留风险 / 备注

- chroma 库存在历史路径问题（`vector sync failed for id=265`），与本轮清理无关，单轮对话不受影响；
- `clean_chroma.py`（tmp 旧脚本）含「删除所有 `v4,` 标签向量」的激进逻辑，**会误删真实 v4 画像向量，切勿直接运行**；本轮改用按 id 精确删除；
- Bug #4 改 `gmtime` 后，若机器本就 UTC 且 `_TZ_OFFSET=8`，钟面显示 +8 的钟点，与 `IKAROS_TZ_OFFSET` 语义一致，属预期。
