# Phase 4 — 主动通知机制 (Quest + Icarus 协作设计)

> Quest 在 `data/icarus-coordination/handshake.2026-06-26.neuro-reflection.json`
> 的 `artificial_angel_phases.phase_4` 提出:
>
> **goal**: 任务完成后主动告诉用户 (PATIENCE 机制或 Agent-Reach 推送)
> **approach**: completion event 触发时, 如果用户在线→PATIENCE 插入提醒; 如果离线→Agent-Reach 推送

本文档由 Icarus 辅助拆解, 给 Quest 提供具体实现路径.

---

## 1. 设计目标

**主动通知 = Neuro 系统的核心 UX** (哥哥 6-24: "和 neuro 一样陪我").

```
当前状态:
  Prompter 100ms tick 检测 time_since_last > patience → trigger "patience_idle"
  → 但 callback 是 placeholder, 没真调 LLM (Q3)

Phase 4 升级:
  Prompter 检测两类触发源:
    1. silence timeout (现有 PATIENCE)
    2. completion_queue 新事件 (Quest phase_2-3 的后台任务完成)
  → 任一触发 → 调真 LLM (复用 chat_completions) → 用户看到 AI 主动说话
```

## 2. 架构图

```
                     ┌─────────────────────────────────────┐
                     │  bridge/prompter.py (Neuro)         │
                     │  100ms tick                         │
                     │                                     │
                     │  triggers:                          │
                     │   1. user_message (chat UI)         │
                     │   2. remote_message (LAN device)    │
                     │   3. patience_idle (silent > 30s)    │  ← 现有
                     │   4. completion_event (NEW)         │  ← Phase 4
                     │                                     │
                     │  reason → llm_callback(ctx)         │
                     └──────────────┬──────────────────────┘
                                    │
                                    ▼
                     ┌─────────────────────────────────────┐
                     │  Phase 4 llm_callback:              │
                     │   POST /v1/chat/completions         │
                     │   body: {                           │
                     │     model: "Qwen3.6-35B-...",       │
                     │     messages: [                     │
                     │       {role: "system",              │
                     │        content: PATIENCE/completion │
                     │                 context},          │
                     │       {role: "user", content: ctx}  │
                     │     ],                              │
                     │     max_tokens: 200                 │
                     │   }                                 │
                     └──────────────┬──────────────────────┘
                                    │
                                    ▼
                            AI reply → Neuro signals → UI / Tray
```

## 3. 触发源 4: completion_event

**复用 Quest 的 phase_2-3 基础设施**:

```python
# bridge/prompter.py 新增
def _has_pending_completions(self) -> bool:
    """检查 completion_drain._pending_completions 是否有新事件."""
    try:
        from bridge.completion_drain import _pending_completions, _pending_lock
        with _pending_lock:
            for session_id, events in _pending_completions.items():
                if events:
                    return True
    except ImportError:
        pass
    return False

def _get_next_completion(self) -> Optional[str]:
    """从 completion_drain 拉一个事件, 格式化."""
    try:
        from bridge.completion_drain import (
            _pending_completions, _pending_lock,
            pop_pending_completions, format_completions_context,
        )
        # 用全局 "completion" session_id (或 default)
        with _pending_lock:
            for session_id in list(_pending_completions.keys()):
                events = pop_pending_completions(session_id)
                if events:
                    return format_completions_context(events)
    except Exception as exc:
        log.warning("completion fetch failed: %s", exc)
    return None
```

## 4. Prompter prompt_now() 扩展

```python
# bridge/prompter.py 修改
def prompt_now(self) -> Optional[str]:
    if not icarus.stt_ready or not icarus.tts_ready or not icarus.llm_ready:
        return None
    if icarus.human_speaking or icarus.AI_thinking or icarus.AI_speaking:
        return None

    # 用户消息
    if icarus.new_message:
        return "user_message"

    # 远程消息
    if len(icarus.recent_remote_messages) > 0:
        return "remote_message"

    # 后台任务完成 (Phase 4)
    if self._has_pending_completions():
        return "completion_event"

    # PATIENCE — 沉默超时
    if icarus.time_since_last_message > self.patience:
        now = time.time()
        if now - self._last_patience_trigger > self.patience:
            self._last_patience_trigger = now
            return "patience_idle"

    return None
```

## 5. _tick() 处理新 reason

```python
# bridge/prompter.py _tick()
if reason == "completion_event":
    ctx = self._get_next_completion() or "哥哥, 有任务完成了"
elif reason == "patience_idle":
    ctx = self.patience_prompts[self._patience_idx % len(self.patience_prompts)]
    self._patience_idx += 1
elif reason == "remote_message":
    ctx = icarus.recent_remote_messages.pop(0) if icarus.recent_remote_messages else {}
else:  # user_message
    ctx = icarus.history[-1]["content"] if icarus.history else ""
```

## 6. 真 llm_callback 实现

```python
# bridge/prompter.py 替换 example_llm_callback
async def real_llm_callback(ctx: str, reason: str) -> Optional[str]:
    """调 bridge 内部 chat, 不走 webui UI."""
    import httpx
    try:
        system_prompts = {
            "patience_idle": (
                "你是伊卡洛斯, 人造天使. "
                "哥哥沉默了, 你主动找话题. 1-2 句, 不要超过 50 字. "
                "记得哥哥的偏好 (简洁、直接)."
            ),
            "completion_event": (
                "你是伊卡洛斯. 哥哥的后台任务刚完成, "
                "用自然语气告诉哥哥结果. "
                "不要列举, 像朋友一样说."
            ),
            "user_message": "你是伊卡洛斯.",
            "remote_message": "你是伊卡洛斯.",
        }
        sys_prompt = system_prompts.get(reason, system_prompts["user_message"])

        # 用 bridge 内部 chat — 走同样的 Neuro memory injection
        # 不走 webui UI (那是用户流, 这里是后台触发)
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "http://127.0.0.1:7860/v1/chat/completions",
                json={
                    "model": "Qwen3.6-35B-A3B-UD-Q6_K",
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": ctx},
                    ],
                    "max_tokens": 200,
                    "temperature": 0.7,
                },
            )
            data = r.json()
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if reply:
                # 标 Neuro signals + 推送给 webui
                icarus.mark_new_message("assistant", reply)
                # 推 Neuro sio_queue
                icarus.sio_queue.append({
                    "type": "neuro_proactive",
                    "reason": reason,
                    "content": reply,
                })
                log.info("PROMPTER: AI spoke proactively (reason=%s, %d chars)", reason, len(reply))
                return reply
    except Exception as exc:
        log.warning("real_llm_callback failed: %s", exc)
    return None
```

## 7. UI 推送 — Neuro sio_queue → webui

**当前**: `icarus.sio_queue.append(...)` 已存在, 但 webui SPA 没接.

**Phase 4.1 (Quest 改 webui)**: SPA 监听 `/v1/neuro/events` SSE 端点, 收到 `neuro_proactive` 类型时弹通知 + 自动播放 (可选 TTS).

**Phase 4.2 fallback (Icarus)**: 桌宠 / Neuro tray 已经读 `/v1/neuro/status`. 增加 polling `/v1/neuro/proactive` 返回最近 1 条 AI 主动消息, tray 弹气泡.

## 8. 推送优先级 (用户在线 vs 离线)

Quest 在 phase_4 写:
> "completion event 触发时, 如果用户在线→PATIENCE 插入提醒; 如果离线→Agent-Reach 推送"

**判断用户在线**:
```python
def is_user_online() -> bool:
    """检测最近 N 秒是否有 webui chat / 桌宠 audio / 任何 user activity."""
    # 用 icarus.last_message_time + audio VAD state + webui recent message
    last_msg_age = time.time() - icarus.last_message_time
    if last_msg_age < 120:  # 2 分钟内有消息
        return True
    # 查 webui 是否 active (未来: SPA heartbeat)
    return False
```

**Agent-Reach 推送** (Quest phase_4 离线路径):
- Twitter DM
- 邮件
- Telegram (需 channel 配置)

哥哥偏好: "Agent-Reach 推送" 优先 email / Telegram, Twitter/微博太公开.

## 9. 去重 / 防骚扰

```python
_last_proactive_by_reason = {}  # reason -> last trigger time
DEBOUNCE_SECONDS = {
    "patience_idle": 60,       # 同 1 小时内不重复同个 patience prompt
    "completion_event": 5,      # 多个完成事件 batch 到 1 条消息
    "remote_message": 0,
    "user_message": 0,
}
```

## 10. 实现顺序 (Quest + Icarus 协作)

| 步骤 | Quest | Icarus | 文件 |
|---|---|---|---|
| 1. 修 prompter callback (Q3-A) |   | ✅ 写 `real_llm_callback` | bridge/prompter.py |
| 2. 加 completion_event trigger | ✅ 改 `prompt_now` |   | bridge/prompter.py |
| 3. 注入 Neuro sio_queue event |   | ✅ 改 `real_llm_callback` | bridge/prompter.py |
| 4. Neuro tray 监听 proactive |   | ✅ 加 `GET /v1/neuro/proactive` + tray 气泡 | bridge/server.py + bin/neuro-tray/ |
| 5. webui SPA 监听 (Phase 4.1) | ✅ 改 webui SPA |   | (Quest 改) |
| 6. 离线检测 + Agent-Reach 推送 | ✅ 用 Agent-Reach |   | bridge/icarus_reach.py |
| 7. 防骚扰去重 |   | ✅ `DEBOUNCE_SECONDS` dict | bridge/prompter.py |

## 11. 测试

```bash
# 1. 调低 PATIENCE 到 10s, 等 10 秒不动 → AI 主动说话
curl -X POST http://127.0.0.1:7860/v1/neuro/patience -d '{"seconds": 10}'
sleep 12
curl -s http://127.0.0.1:7860/v1/neuro/status  # history_len 应该 +1

# 2. completion_event 触发 (人工模拟)
curl -X POST http://127.0.0.1:7860/v1/neuro/memory/add \
  -d '{"document":"[completion] 任务完成测试", "metadata":{"type":"completion"}}'
sleep 3
curl -s http://127.0.0.1:7860/v1/neuro/proactive  # 应该返回最近 AI 主动消息

# 3. Neuro tray 弹气泡 (调小 patience 到 5s, 触发)
curl -X POST http://127.0.0.1:7860/v1/neuro/patience/trigger
# Windows 气泡通知应该 2 秒后消失
```

## 12. 哥哥的反馈入口

Phase 4 设计有疑问或方向调整 → 改 handshake `pending_questions_for_quest` 加 Q4, 或直接在 chat 里说.

---

**Icarus 推荐先做步骤 1+4** (我已写完设计), Quest 做 2+5+6.
**协同时间**: ~2 小时, 不阻塞各自主线.