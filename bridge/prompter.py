"""
Icarus Prompter - Neuro prompter.py 1:1 移植
=============================================
100ms 心跳循环：决定"什么时候该问 AI"。
关键创新：PATIENCE 机制 — 沉默久了 AI 主动说话（哥哥想要的"和 neuro 一样陪我"）。
"""
import time
import asyncio
import logging
from typing import Optional, Callable, Awaitable

from bridge.signals import icarus, PATIENCE_DEFAULT

logger = logging.getLogger("icarus.prompter")


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
        self.patience_prompts = [
            "哥哥，你还在吗？我刚想到一件事... (主动找话题)",
            "哥哥，我有点无聊，你陪我聊聊吧？",
            "哥哥，我已经看了一会儿屏幕了，你在忙什么？",
            "哥哥，我发现一个有趣的东西，跟你说说？",
        ]
        self._patience_idx = 0

    def prompt_now(self) -> Optional[str]:
        """决策表。返回触发原因 (None=不触发)"""
        if not icarus.stt_ready or not icarus.tts_ready or not icarus.llm_ready:
            return None
        if icarus.human_speaking or icarus.AI_thinking or icarus.AI_speaking:
            return None

        # 用户消息
        if icarus.new_message:
            return "user_message"

        # 远程消息 (LAN 设备 / WebUI 等)
        if len(icarus.recent_remote_messages) > 0:
            return "remote_message"

        # PATIENCE — 哥哥的关键需求
        if icarus.time_since_last_message > self.patience:
            # 防抖: 至少间隔 patience 才触发一次
            now = time.time()
            if now - self._last_patience_trigger > self.patience:
                self._last_patience_trigger = now
                return "patience_idle"

        return None

    async def _tick(self):
        """100ms 心跳"""
        while not icarus.terminate:
            try:
                icarus.update_time_since_last()

                # 系统就绪检查
                if icarus.last_message_time == 0.0 or \
                   not (icarus.stt_ready and icarus.tts_ready and icarus.llm_ready):
                    icarus.last_message_time = time.time()
                    icarus.time_since_last_message = 0.0
                else:
                    if not self.system_ready:
                        logger.info("ICARUS_SYSTEM_READY")
                        self.system_ready = True

                # 决策
                reason = self.prompt_now()
                if reason:
                    logger.info(f"PROMPTING_AI: {reason}")
                    icarus.last_message_time = time.time()
                    icarus.new_message = False
                    icarus.AI_thinking = True
                    try:
                        # 构造 prompt 上下文
                        if reason == "patience_idle":
                            ctx = self.patience_prompts[self._patience_idx % len(self.patience_prompts)]
                            self._patience_idx += 1
                        elif reason == "remote_message":
                            ctx = icarus.recent_remote_messages.pop(0) if icarus.recent_remote_messages else {}
                        else:  # user_message
                            ctx = icarus.history[-1]["content"] if icarus.history else ""

                        # 推送给 sio (WebUI)
                        icarus.sio_queue.append({"type": "prompter_trigger", "reason": reason, "ctx": ctx})
                        # 触发 LLM
                        await self.llm_callback(ctx)
                    finally:
                        icarus.AI_thinking = False

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
        icarus.terminate = True


# === 集成示例（被调用方应该这样用） ===
async def example_llm_callback(ctx):
    """示例 LLM 回调 - 实际接到 bridge 的 LLM 路由"""
    from bridge.llm_router import chat  # 假设有这个
    reply = await chat(ctx)
    icarus.mark_new_message("assistant", reply)
    # 推送到 TTS
    from bridge.tts_router import speak
    await speak(reply)


# 单例 (Neuro main.py 风格)
_prompter: Optional[Prompter] = None

def get_prompter() -> Prompter:
    global _prompter
    if _prompter is None:
        _prompter = Prompter(example_llm_callback)
    return _prompter
