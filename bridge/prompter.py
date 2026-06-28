"""
Ikaros Prompter - Neuro prompter.py 1:1 移植
=============================================
100ms 心跳循环：决定"什么时候该问 AI"。
关键创新：PATIENCE 机制 — 沉默久了 AI 主动说话（哥哥想要的"和 neuro 一样陪我"）。
Phase 4 升级：completion_event 触发 + real_llm_callback + 防骚扰 debounce。
"""
import time
import asyncio
import logging
import os
import sys
from typing import Optional, Callable, Awaitable, Dict, Any

import httpx

from bridge.signals import ikaros, PATIENCE_DEFAULT

logger = logging.getLogger("ikaros.prompter")

# Ensure logger emits to stderr at INFO level.
# (When bridge.start.ps1 runs via supervisor, stderr is inherited but
#  not captured into bridge.err - so we ALSO add a FileHandler that
#  writes to data/logs/ikaros-prompter.log, so reflective events are
#  inspectable after the fact.)
if not logger.handlers and os.environ.get("ICARUS_PROMPTER_LOG") != "off":
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s [ikaros.prompter] %(message)s"))
    logger.addHandler(handler)
    try:
        from pathlib import Path
        log_file = Path(__file__).resolve().parent.parent / "data" / "logs" / "ikaros-prompter.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(log_file), encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s"))
        logger.addHandler(fh)
    except Exception as exc:
        logger.warning(f"could not install file handler: {exc}")
    logger.setLevel(logging.INFO)

# Phase 4: 防骚扰 debounce (每种 reason 的触发间隔)
DEBOUNCE_SECONDS: Dict[str, float] = {
    "patience_idle": 60,      # 1 分钟内不重复
    "completion_event": 5,    # 多个完成事件 batch 到 1 条消息
    "remote_message": 0,
    "user_message": 0,
}
_last_proactive_by_reason: Dict[str, float] = {}

# Phase 4: 最近 AI 主动消息 (供 /v1/neuro/proactive 查询)
_recent_proactive_messages: list = []
_MAX_PROACTIVE_HISTORY = 20


class Prompter:
    """
    调度中枢：每 100ms 决定是否触发 LLM。
    决策表 (Neuro prompt_now 1:1 + 伊卡洛斯扩展):
      - 系统未就绪 → 不问
      - 人在说话/AI 在思考/AI 在说话 → 不问
      - 用户发了新消息 → 立刻问
      - 远程有新消息 → 立刻问
      - 沉默超过 PATIENCE → 主动问 (PATIENCE 机制 - 哥哥的关键需求)
    """

    def __init__(self,
                 llm_callback: Callable[[str], Awaitable[str]],
                 patience: float = PATIENCE_DEFAULT):
        """
        llm_callback: 异步函数，接收 prompt 上下文，返回 AI 回复
                      上下文类型: "user_message" | "patience_idle" | "remote_message"
        """
        self.llm_callback = llm_callback
        self.patience = patience
        self.system_ready = False
        self._task: Optional[asyncio.Task] = None
        self._last_patience_trigger = 0.0  # 防重复
        self._last_emit = 0.0
        # FIX 2026-06-27: 启动延迟 — 给 bridge/uvicorn 时间完成初始化，
        # 避免 prompter 在 lifespan 刚结束时立刻触发 LLM 调用。
        self._ready_at: float = time.time() + 15.0  # 15s 启动保护期
        self.patience_prompts = [
            "哥哥，你还在吗？我刚想到一件事... (主动找话题)",
            "哥哥，我有点无聊，你陪我聊聊吧？",
            "哥哥，我已经看了一会儿屏幕了，你在忙什么？",
            "哥哥，我发现一个有趣的东西，跟你说说？",
        ]
        self._patience_idx = 0

    def prompt_now(self) -> Optional[str]:
        """决策表。返回触发原因 (None=不触发)

        Ready gate: 只看 llm_ready (LLM 是 reflection 的必要条件).
        不要求 stt_ready / tts_ready — 文字反思不需要语音.
        """
        # FIX 2026-06-27: 启动保护期 — 在 bridge 刚启动的 15s 内不触发，
        # 避免 uvicorn 还没完全 ready 就收到自调用请求。
        if time.time() < self._ready_at:
            return None
        if not ikaros.llm_ready:
            return None
        if ikaros.human_speaking or ikaros.AI_thinking or ikaros.AI_speaking:
            return None

        # 用户消息
        if ikaros.new_message:
            return "user_message"

        # 远程消息 (LAN 设备 / WebUI 等)
        if len(ikaros.recent_remote_messages) > 0:
            return "remote_message"

        # Phase 4: 后台任务完成事件
        if self._has_pending_completions():
            reason = "completion_event"
            if self._debounce_ok(reason):
                return reason

        # PATIENCE — 哥哥的关键需求
        if ikaros.time_since_last_message > self.patience:
            now = time.time()
            patience_debounce = DEBOUNCE_SECONDS.get("patience_idle", 60)
            if now - self._last_patience_trigger > max(self.patience, patience_debounce):
                self._last_patience_trigger = now
                return "patience_idle"

        return None

    def _debounce_ok(self, reason: str) -> bool:
        """检查防骚扰间隔"""
        min_interval = DEBOUNCE_SECONDS.get(reason, 0)
        last = _last_proactive_by_reason.get(reason, 0)
        if time.time() - last < min_interval:
            return False
        _last_proactive_by_reason[reason] = time.time()
        return True

    def _has_pending_completions(self) -> bool:
        """检查 completion drain 是否有待处理事件 (Phase 4)."""
        # completion drain 的 _pending_completions 在 worker 进程,
        # 而 prompter 在 bridge 进程. 通过 JSONL 文件检测.
        hermes_home = os.environ.get("HERMES_HOME", "")
        if not hermes_home:
            return False
        log_path = os.path.join(hermes_home, "completed-tasks.jsonl")
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                # 检查是否有新行 (简化: 每次读全部, 生产环境应该用 offset)
                lines = f.readlines()
                return len(lines) > getattr(self, '_last_completion_count', 0)
        except (FileNotFoundError, PermissionError):
            return False

    def _get_next_completion(self) -> Optional[str]:
        """从 completed-tasks.jsonl 拉取新事件并格式化 (Phase 4)."""
        hermes_home = os.environ.get("HERMES_HOME", "")
        if not hermes_home:
            return None
        log_path = os.path.join(hermes_home, "completed-tasks.jsonl")
        try:
            import json
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            last_count = getattr(self, '_last_completion_count', 0)
            new_lines = lines[last_count:]
            self._last_completion_count = len(lines)
            if not new_lines:
                return None
            summaries = []
            for line in new_lines:
                try:
                    rec = json.loads(line.strip())
                    goal = rec.get("goal", "unknown")
                    status = rec.get("status", "unknown")
                    summary = rec.get("summary", "")
                    summaries.append(f"{goal}: {status}" + (f" — {summary[:100]}" if summary else ""))
                except json.JSONDecodeError:
                    continue
            return "\n".join(summaries) if summaries else None
        except (FileNotFoundError, PermissionError):
            return None

    async def _tick(self):
        """100ms 心跳"""
        while not ikaros.terminate:
            try:
                ikaros.update_time_since_last()

                # 系统就绪检查
                # 只要求 llm_ready (reflection 必要条件). STT/TTS 是 voice 用,
                # 文字反思不需要. 如果 llm_ready=true 但 STT/TTS=false, 仍要 PATIENCE.
                if ikaros.last_message_time == 0.0 or not ikaros.llm_ready:
                    ikaros.last_message_time = time.time()
                    ikaros.time_since_last_message = 0.0
                else:
                    if not self.system_ready:
                        logger.info("ICARUS_SYSTEM_READY")
                        self.system_ready = True

                # 决策
                reason = self.prompt_now()
                if reason:
                    logger.info(f"PROMPTING_AI: {reason}")
                    ikaros.last_message_time = time.time()
                    ikaros.new_message = False
                    ikaros.AI_thinking = True
                    try:
                        # 构造 prompt 上下文
                        if reason == "completion_event":
                            ctx = self._get_next_completion() or "哥哥, 有任务完成了"
                        elif reason == "patience_idle":
                            ctx = self.patience_prompts[self._patience_idx % len(self.patience_prompts)]
                            self._patience_idx += 1
                        elif reason == "remote_message":
                            ctx = ikaros.recent_remote_messages.pop(0) if ikaros.recent_remote_messages else {}
                        else:  # user_message
                            ctx = ikaros.history[-1]["content"] if ikaros.history else ""

                        # 推送给 sio (WebUI)
                        ikaros.sio_queue.append({"type": "prompter_trigger", "reason": reason, "ctx": ctx})
                        # 触发 LLM
                        reply = await self.llm_callback(ctx, reason=reason)
                        # 记录主动消息
                        if reply:
                            _recent_proactive_messages.append({
                                "reason": reason,
                                "content": reply,
                                "time": time.time(),
                            })
                            if len(_recent_proactive_messages) > _MAX_PROACTIVE_HISTORY:
                                _recent_proactive_messages.pop(0)
                    finally:
                        ikaros.AI_thinking = False

            except Exception as e:
                logger.exception(f"prompter tick error: {e}")

            await asyncio.sleep(0.1)  # 100ms

    def start(self):
        """启动心跳循环"""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._tick())
        logger.info("prompter started (100ms tick)")

    def stop(self):
        """停止心跳"""
        if self._task:
            self._task.cancel()
            self._task = None
        ikaros.terminate = True


# === Phase 4: 真实 LLM 回调 ===

# FIX 2026-06-27: 直接调用 llama-server (:8080) 而不是 bridge (:7860)。
# 之前 prompter 调用 bridge 自己的 /v1/chat/completions，导致死锁：
# bridge 启动 → prompter 触发 → HTTP POST 到自己 → 事件循环阻塞 → uvicorn 不响应
# → watchdog 重启 → 无限循环。
# 现在直接调用上游 llama-server，绕过 bridge 自身。
# 可用 env ICARUS_LLM_ENDPOINT 覆盖 (debug / 测 worker 性能时用)
_LLM_ENDPOINT = os.environ.get(
    "ICARUS_LLM_ENDPOINT",
    "http://127.0.0.1:8080/v1/chat/completions"
)
_LLM_MODEL = os.environ.get("ICARUS_LLM_MODEL", "Qwen3.6-35B-A3B-UD-IQ2_M")

_SYSTEM_PROMPTS = {
    "patience_idle": (
        "你是伊卡洛斯, 人造天使. 哥哥沉默了一会儿, 你主动找话题. "
        "1-2 句话, 不要超过 50 字. 自然、亲切."
    ),
    "completion_event": (
        "你是伊卡洛斯. 哥哥的后台任务刚完成, 用自然语气告诉哥哥结果. "
        "不要列举, 像朋友一样说. 简洁."
    ),
    "user_message": "你是伊卡洛斯, 人造天使.",
    "remote_message": "你是伊卡洛斯, 人造天使.",
}


async def real_llm_callback(ctx: str, reason: str = "user_message") -> Optional[str]:
    """真实 LLM 回调 — 走 bridge :7860 /v1/chat/completions.

    bridge 内部处理 LRU + worker 切换; 但首次调用会触发 cold-start (worker 47s 加载).
    timeout 设 90s 覆盖 cold-start; 后续调用快.
    """
    sys_prompt = _SYSTEM_PROMPTS.get(reason, _SYSTEM_PROMPTS["user_message"])
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                _LLM_ENDPOINT,
                json={
                    "model": _LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": str(ctx)},
                    ],
                    "max_tokens": 800,
                    "temperature": 0.7,
                },
            )
            logger.info(f"PROMPTER_LLM: status={r.status_code}, endpoint={_LLM_ENDPOINT}")
            if r.status_code != 200:
                logger.warning(f"PROMPTER_LLM body: {r.text[:300]}")
            data = r.json()
            # Qwen3 is a reasoning model: response may be empty when reasoning
            # consumes most of max_tokens. Try content first, then reasoning_content.
            choices = data.get("choices", [{}])
            if not choices:
                logger.warning(f"PROMPTER_LLM: empty choices, body={r.text[:300]}")
                return None
            msg = choices[0].get("message", {})
            reply = msg.get("content") or msg.get("reasoning_content") or ""
            if not reply.strip():
                logger.warning(f"PROMPTER_LLM: empty content, body={r.text[:300]}")
                return None
            if reply:
                ikaros.mark_new_message("assistant", reply)
                ikaros.sio_queue.append({
                    "type": "neuro_proactive",
                    "reason": reason,
                    "content": reply,
                })
                logger.info("PROMPTER: AI spoke proactively (reason=%s, %d chars)", reason, len(reply))
                return reply
    except Exception as exc:
        logger.warning("real_llm_callback failed (%s): %s", type(exc).__name__, exc)
    return None


def get_recent_proactive_messages() -> list:
    """获取最近的主动消息列表 (供 /v1/neuro/proactive 端点)."""
    return list(_recent_proactive_messages)


# 单例 (Neuro main.py 风格)
_prompter: Optional[Prompter] = None

def get_prompter() -> Prompter:
    global _prompter
    if _prompter is None:
        _prompter = Prompter(real_llm_callback)
    return _prompter
