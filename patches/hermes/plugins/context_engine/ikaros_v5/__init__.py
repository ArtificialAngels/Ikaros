"""Ikaros V5 Context Engine — 把 V5 记忆注入 Hermes 上下文压缩。

本引擎继承内置 ``ContextCompressor``（即 ``ContextEngine`` 的默认实现），
在压缩时把 Ikaros V5 的情感状态 / 实体关系 / 相关记忆注入摘要 prompt，
使长对话压缩后仍保留 V5 连续性。

通过 ``context.engine: ikaros_v5`` 启用（Dashboard 或 ``hermes config``）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent.context_compressor import ContextCompressor

logger = logging.getLogger(__name__)

# 标记：避免与记忆提供方（ikaros-v5）重复注入同一段 V5 上下文
_IKAROS_MARKER = "[Ikaros"


class IkarosV5ContextEngine(ContextCompressor):
    """ContextEngine 实现：压缩前注入 Ikaros V5 记忆上下文。"""

    def __init__(self):
        # ContextCompressor.__init__ 必填 model；运行时随后调 update_model()
        # 注入真实模型，故这里用占位字符串即可。
        super().__init__(model="ikaros-v5-placeholder")
        self._provider = None  # 惰性加载 IkarosV5MemoryProvider

    # ── 标识 ──

    @property
    def name(self) -> str:
        return "ikaros_v5"

    # ── 可用性（轻量静态检查，无需 import V5）──

    def is_available(self) -> bool:
        try:
            from plugins.memory.ikaros_v5 import IkarosV5MemoryProvider

            return IkarosV5MemoryProvider()._v5_environment_present()
        except Exception:
            return False

    # ── V5 提供方惰性加载 ──

    def _ensure_provider(self):
        if self._provider is None:
            try:
                from plugins.memory.ikaros_v5 import IkarosV5MemoryProvider

                self._provider = IkarosV5MemoryProvider()
                self._provider.initialize("")
            except Exception as e:  # 导入/初始化失败时不致命，仅跳过注入
                logger.debug("Ikaros V5 provider init failed: %s", e)
                self._provider = False  # sentinel：后续不再重试
        return self._provider if self._provider is not False else None

    # ── 压缩：注入 V5 记忆上下文 ──

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
        force: bool = False,
        memory_context: str = "",
    ) -> List[Dict[str, Any]]:
        provider = self._ensure_provider()
        v5_ctx = ""
        if provider is not None:
            try:
                v5_ctx = provider.on_pre_compress(messages)
            except Exception as e:
                logger.debug("Ikaros V5 context gather failed: %s", e)
                v5_ctx = ""

        merged = self._merge_memory_context(memory_context, v5_ctx)
        return super().compress(
            messages,
            current_tokens=current_tokens,
            focus_topic=focus_topic,
            force=force,
            memory_context=merged,
        )

    @staticmethod
    def _merge_memory_context(incoming: str, v5_ctx: str) -> str:
        """合并外部传入的 memory_context 与 V5 上下文，避免重复注入。"""
        incoming = (incoming or "").strip()
        v5_ctx = (v5_ctx or "").strip()
        if not v5_ctx:
            return incoming
        # 若调用方（如记忆提供方）已注入过 V5 内容，则不再重复
        if _IKAROS_MARKER in incoming:
            return incoming
        if incoming:
            return incoming + "\n\n" + v5_ctx
        return v5_ctx
