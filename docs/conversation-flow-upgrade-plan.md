# Ikaros 对话系统「修订升级方案」

> 基于全对话流程拦截测试（见 `docs/conversation-flow-test-report.md`）的 6 个阻断性问题，结合参考项目
> `E:\Ikaros-something\reference project\` 中 **agend-main**（任务/会话解耦架构）与
> **hermes-studio-main**（reasoning 独立通道模式）给出可落地的修订方案。
> 所有根因均已定位到具体 `file:line`。

---

## 0. 设计原则（来自参考项目）

| 参考项目 | 可借鉴模式 | 解决本项目的 |
|----------|-----------|-------------|
| **agend-main** (`agend/task_runner.py`, `session.py`) | 任务内容与对话会话**解耦**：worker 拿到 `task` 字符串独立执行，supervisor 独立检查，二者都不污染 companion 的对话消息 | Bug #1 task gate 短路、Bug #2 `_optimized` 污染 |
| **hermes-studio-main** (`docs/chat-chain-changes/2026-06-04-pr1333-reasoning-merge.md`, `2026-06-19-bridge-history-reasoning-content.md`) | `reasoning_content` / `thinking.delta` 是**独立通道**，绝不进入可见 `message.delta`，历史回放也保留为 sentinel 而非正文 | Bug #6 思维链外泄 |

> 核心一句话：**对话层只携带用户原话 + 模型可见系统提示；任务层（task spec）走后台 worker，互不打扰。**

---

## 1. Bug #2（🔴 最高优先级）— `_optimized` 污染对话 user 内容

**现象**：模型每轮收到的最末 `user` 消息是 router 的结构化任务描述
`"output":"scan_recent.py","constraints":...,"skills":[...]`，用户原话只在更早 history → 模型把意图误读成 JSON 片段。

**根因**：`bin/cloud_chat.py:1470`
```python
user_content = _optimized if _optimized else text      # ← 被判 task 时, 用优化描述替换了用户原话
msgs.append({"role": "user", "content": user_content})
```

**修复**：对话层永远用 `text`（用户原话）；`_optimized` 只传给 task_runner，绝不进消息体。
```python
# 对话层只携带用户原话；_optimized 是结构化任务规格, 仅交给后台 worker
user_content = text
msgs.append({"role": "user", "content": user_content})
```

---

## 2. Bug #1（🔴 高）— task gate 短路，对话被秒回罐头文本

**现象**：router 判为 task 时直接 `return "好的哥哥，这个任务我已经在后台处理了…"`，根本不调模型 → 正常对话（写脚本/调试/回忆）全变秒回。

**根因**：`bin/cloud_chat.py:1386-1393`
```python
if _is_task:
    try:
        from v5.task_runner import call_async
        call_async(text, optimized=_optimized)
    except Exception:
        pass
    return "好的哥哥，这个任务我已经在后台处理了，完成后会告诉你结果。"   # ← 短路, 不调模型
```

**修复（对齐 agend 解耦）**：后台派发任务，**但不短路**——继续走模型，让 companion 自然应答。
```python
if _is_task:
    try:
        from v5.task_runner import call_async
        call_async(text, optimized=_optimized)   # 后台 worker 拿到真实原话 + 结构化 spec
    except Exception as e:
        log.warning("task dispatch failed: %s", e)   # 不再静默吞掉
    # 不 return —— 落到下方模型调用, companion 像平常一样回复
```

---

## 3. Bug #3（🟠 中）— 后台任务 `hermes.exe` 缺失，永久失败且污染 persona

**现象**：`call_async` 调 `hermes.exe` 失败 → 失败结果 `hermes not found` 被塞进每轮 system prompt 的「待办」上下文，污染所有对话。

**根因**：`core/memory_v5/task_runner.py:66,203`（注：原文档写 `core/v5/v5/`，已于 2026-07-26 重命名为 `core/memory_v5`）写死路径
`_HERMES_ROOT / "core/hermes" / "venv" / "Scripts" / "hermes.exe"`
而真实可执行文件在 `E:/Ikaros/core/hermes/venv/Scripts/hermes.exe`（PATH 可解析，`command -v hermes.exe` 成功），写死路径指向了已损坏的 `core/hermes-agent/...` 符号链接（注：`hermes-agent/` 已于 2026-07-26 搬迁为 `core/hermes/`）。

**修复**：稳健解析（优先 PATH，再回退已知位置），与 `bin/` 下其他脚本的解析策略保持一致。
```python
import shutil
def _resolve_hermes_exe() -> str:
    found = shutil.which("hermes.exe")
    if found:
        return found
    for cand in (
        _HERMES_ROOT.parent / "core/hermes" / "venv" / "Scripts" / "hermes.exe",
        _HERMES_ROOT / "core/hermes" / "venv" / "Scripts" / "hermes.exe",
    ):
        if cand.exists():
            return str(cand)
    raise FileNotFoundError("hermes.exe not found on PATH or known locations")
```
并在「待办/结果」注入 persona 处：任务失败时记录**干净的失败状态**（不要原样塞 `hermes not found` 原始报错），仅在哥哥主动问「任务怎么样了」时优雅告知。

---

## 4. Bug #4（🟠 中）— 时段判定错误（`深夜(01:xx)` vs 真实下午 17:xx）

**现象**：persona 头部正确显示 `周六（17:19）`，但节奏块显示 `时段: 深夜(01:19)`。

**根因**：`core/v5/v5/rhythm.py:54` 时区**双重叠加**：
```python
lt = time.localtime(now + _TZ_OFFSET * 3600)   # localtime 已按本机时区本地化, 又 +8h → UTC+16 → 回卷成 01:xx
```
本机已是 UTC+8，`localtime` 再 +8h 等于 UTC+16。

**修复**：对 UTC 显式叠加偏移（用 `gmtime`），保留 `IKAROS_TZ_OFFSET` 覆盖能力：
```python
lt = time.gmtime(now + _TZ_OFFSET * 3600)      # UTC + offset = 正确的本地钟点
hour, minute = lt.tm_hour, lt.tm_min
```
`_period_label(hour)` 与 `infer_activity(hour, ...)` 已用同一 `hour`，无需改。

---

## 5. Bug #5（🟡 低）— 人格偏好模板前缀重复 `哥哥偏好哥哥偏好…`

**现象**：system prompt 出现 `哥哥偏好哥哥偏好短句和直接语气…`。

**根因**：偏好记忆拼接时给每条记忆统一前缀 `哥哥偏好`，但部分已存记忆内容自身就以 `哥哥偏好`/`偏好` 开头 → 叠加。

**修复**：拼接处归一化（去掉记忆内容里已有的前缀再统一加）：
```python
import re
def _fmt_pref(content: str) -> str:
    c = re.sub(r'^(哥哥)?偏好', '', content.strip())   # 去掉残留前缀
    return f"哥哥偏好{c}"
```
（拼接入口在 persona 构建处；定位命令：`grep -rn "哥哥偏好\|说人话比修辞" core bin` 找到渲染函数后套用。）

---

## 6. Bug #6（🟠 中）— 思维链外泄（`(◔_◔) computing...` 当正文）

**现象**：5 轮回复全是 `(◔_◔) computing... / (¬_¬) reasoning...` 形式的推理过程，无情感、无答案。

**根因**：deepseek-chat 在当前提示词下把 reasoning 写进了**可见 content**；且污染后的 assistant 回复被原样存入 history → 跨轮累积恶化。Hermes 的做法是把 `reasoning_content` 当**独立通道**（见参考 `pr1333-reasoning-merge.md`），本项目 `cloud_chat.py:1892` 已分离 `reasoning_content`，但没阻止模型把思考标记塞进可见 content，也没在落库时清洗。

**修复（三道闸）**：
1. **提示词硬约束**（加进 companion system prompt）：
   > 「直接回复哥哥，口语化、短句。严禁输出 `(◔_◔) computing` / `(¬_¬) reasoning` 之类的思考过程标记，也不要把内心独白当回复发出。」
2. **落库清洗**：把 assistant 回复写入记忆/history 前，剥离思维标记行（正则去除开头的 `(◔_◔) ...` / `(¬_¬) ...` 等 kaomoji+思维词行），保证下一轮 history 干净。
3. **API 层**：`deepseek-chat` 本非推理模型，确认未请求 thinking；若改用 `deepseek-reasoner`，则按 Hermes 模式把 `reasoning_content` 单独保留、绝不并入正文。

---

## 7. 实施分期（建议顺序）

| 期 | 内容 | 对应 Bug | 风险 |
|----|------|---------|------|
| P0 | `#1`+`#2` 消息构造与 task gate 解耦 | 2,1 | 低；纯逻辑， companion 对话立刻恢复 |
| P1 | `#4` 时区、`#3` hermes.exe 解析、`#5` 偏好前缀 | 4,3,5 | 低；局部改动 |
| P2 | `#6` 提示词约束 + 落库清洗 + reasoning 通道规整 | 6 | 中；需回归验证多轮不再累积污染 |

---

## 8. 验证方案（复用本次拦截手段）

1. 重启面板重载 `cloud_chat`（`POST /api/restart`）。
2. 重新起 `:8899` 透明中继代理，把 companion 的 `IKAROS_BACKEND_BASE_URL` 指过去（测试用，不落盘）。
3. 跑 5 轮驱动器（打招呼→回忆→写脚本→调试→夸脚本），断言：
   - 最末 `user` 消息 = 用户**原话**（不再有 `output:...` JSON 片段）；
   - `时段` 显示 `下午(17:xx)`（不再 `深夜(01:xx)`）；
   - 回复无 `(◔_◔)/(¬_¬)` 思维标记，且出现真实情感回应（如感谢被接住）；
   - `full_messages` 跨轮连贯（2→4→6→8→10），模型不再「I'm not sure what they want」。
4. 后台任务：确认 `call_async` 能解析到真实 `hermes.exe` 且不再往 persona 注入 `hermes not found`。

---

## 9. 回滚与风险

- 每处改动独立、可单文件 `git diff` 回退；P0 改动不影响 Hermes 路径。
- `rhythm.py` 改 `gmtime` 后，若机器本就 UTC（无 TZ），`_TZ_OFFSET=8` 会显示 +8 的钟点——与现有 `IKAROS_TZ_OFFSET` 语义一致，属预期。
- 测试用代理/临时 `base_url` 改动**不写入生产配置**，仅测试会话内生效。

> 参考项目路径：`E:\Ikaros-something\reference project\agend-main\agend\`（task_runner.py / session.py）、
> `E:\Ikaros-something\reference project\hermes-studio-main\docs\chat-chain-changes\`（reasoning-merge / bridge-history-reasoning-content）。
