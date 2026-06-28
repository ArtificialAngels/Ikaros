"""
Ikaros Neuro E2E test
====================
跑通: Signals → Prompter (PATIENCE 触发) → Memory (reflection 注入) → LLM callback (mock)
"""
import asyncio
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("ikaros.e2e")

from bridge.signals import ikaros, AI_NAME, HOST_NAME
from bridge.prompter import Prompter
from bridge.neuro.memory import Memory
from bridge.neuro.module import build_system_prompt


# Mock LLM 回调
async def mock_llm(ctx):
    """模拟 LLM 思考 + 回复"""
    logger.info(f"[LLM] thinking... ctx={ctx!r}")
    await asyncio.sleep(0.2)
    reply = f"我刚想到: {ctx!s}" if isinstance(ctx, str) else f"收到: {ctx}"
    logger.info(f"[LLM] reply: {reply}")
    ikaros.mark_new_message("assistant", reply)
    # 模拟 TTS 播放
    ikaros.AI_speaking = True
    await asyncio.sleep(0.1)
    ikaros.AI_speaking = False
    ikaros.last_message_time = time.time()


async def main():
    logger.info("=== Ikaros Neuro E2E ===")
    logger.info(f"AI_NAME: {AI_NAME}, HOST_NAME: {HOST_NAME}")

    # 假装系统就绪
    ikaros.stt_ready = True
    ikaros.tts_ready = True
    ikaros.llm_ready = True

    # 启动 memory reflection
    mem = Memory(ikaros, enabled=True)
    mem_task = asyncio.create_task(mem.run())
    logger.info("memory reflection started")

    # 启动 prompter
    prompter = Prompter(llm_callback=mock_llm, patience=3.0)
    prompter.start()
    logger.info("prompter started")

    # 模拟哥哥发消息
    await asyncio.sleep(0.3)
    logger.info("哥哥发消息: 你好伊卡洛斯")
    ikaros.mark_new_message("user", "你好伊卡洛斯")

    # 等几秒,看 prompter 处理
    await asyncio.sleep(0.5)
    logger.info(f"history len: {len(ikaros.history)}")

    # 等 PATIENCE 触发
    logger.info("等 4s 看 PATIENCE...")
    await asyncio.sleep(4.0)
    logger.info(f"history len after patience: {len(ikaros.history)}")

    # 测 memory 注入
    logger.info("=== memory injection ===")
    inj = mem.get_prompt_injection()
    logger.info(inj["text"][:300])

    # 模拟更多对话触发 reflection (>= 20 条)
    logger.info("=== simulation 25 messages for reflection ===")
    for i in range(25):
        ikaros.mark_new_message("user", f"测试消息 {i}")
        ikaros.mark_new_message("assistant", f"回复 {i}")
        await asyncio.sleep(0.01)
    logger.info(f"history len: {len(ikaros.history)}, processed: {mem.processed_count}")

    # 等 reflection 跑一次
    await asyncio.sleep(6.0)
    logger.info(f"history len: {len(ikaros.history)}, processed: {mem.processed_count}")

    # 测 build_system_prompt
    class StubMod:
        enabled = True
        def get_prompt_injection(self):
            from dataclasses import dataclass
            @dataclass
            class I:
                text = "stub injection"
                priority = 80
                enabled = True
            return I()

    mods = {"mem": mem, "stub": StubMod()}
    prompt = build_system_prompt(ikaros, mods, base_prompt=f"你是 {AI_NAME}")
    logger.info(f"=== built prompt (first 300 chars) ===\n{prompt[:300]}")

    # 关闭
    ikaros.terminate = True
    prompter.stop()
    mem_task.cancel()
    await asyncio.sleep(0.3)
    logger.info("=== done ===")


if __name__ == "__main__":
    asyncio.run(main())
