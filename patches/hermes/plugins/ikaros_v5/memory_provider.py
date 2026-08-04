"""Ikaros V5 Memory Provider — Hermes 上下文压缩 + 对话闭环的记忆引擎。

在上下文压缩前从 Ikaros V5 检索相关记忆，注入摘要 prompt，
使压缩保留 V5 的情感状态、实体关系和近期记忆。
同时在对话全生命周期（开始→每轮→结束）闭环写回 V5。

配置
----
无需额外配置。自动检测 IKAROS_ROOT 并导入 V5 模块。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

# 身份刷新间隔：每 N 轮注入一次
_IDENTITY_REFRESH_INTERVAL = 10

# 质量门：低于此长度的内容不存入 V5（同 cloud_chat 标准）
_STORE_MIN_CHARS = 6

_SKIP_PATTERNS = frozenset({
    "嗯", "哦", "好", "好的", "行", "ok", "是", "对",
    "继续", "然后", "谢谢", "感谢", "收到",
})


def _should_store(content: str) -> bool:
    c = (content or "").strip()
    if not c or len(c) < _STORE_MIN_CHARS:
        return False
    if c.lower() in _SKIP_PATTERNS:
        return False
    return True


class IkarosV5MemoryProvider(MemoryProvider):
    """MemoryProvider 实现：上下文压缩前从 Ikaros V5 注入相关记忆。"""

    def __init__(self):
        self._v5_root: Path | None = None
        self._v5_loaded = False
        self._import_error: str | None = None
        self._turn_counter = 0  # on_turn_start 计数

    # ── 元信息 ──

    @property
    def name(self) -> str:
        # 与目录名 / 配置项 memory.provider / context_engine 的 ikaros_v5 保持一致，
        # 避免 Dashboard 按 ikaros_v5 激活却在 provider.name 上比对不上。
        return "ikaros_v5"

    def is_available(self) -> bool:
        if self._v5_loaded:
            return True
        return self._v5_environment_present()

    # ── 初始化 ──

    def initialize(self, session_id: str, **kwargs) -> None:
        if self._v5_loaded:
            return

        root = self._resolve_root()
        v5_path = root / "core" / "memory_v5"
        if not v5_path.exists():
            self._import_error = f"V5 目录不存在: {v5_path}"
            logger.warning(self._import_error)
            return

        self._v5_root = v5_path
        # 把 core/ 加入 sys.path，使包以 memory_v5 名字被导入
        # （core/memory_v5 内部用 `from memory_v5.xxx import ...` 互相引用）
        core_str = str(root / "core")
        if core_str not in sys.path:
            sys.path.insert(0, core_str)

        try:
            from memory_v5.store import stats as _v5_stats, search as _v5_search
            self._v5_stats = _v5_stats
            self._v5_search = _v5_search
            self._v5_loaded = True
            logger.info("Ikaros V5 MemoryProvider loaded (root=%s)", v5_path)
        except ImportError as e:
            self._import_error = f"V5 模块导入失败: {e}"
            logger.warning(self._import_error)

    def shutdown(self) -> None:
        self._v5_loaded = False

    # ── 路径 / 可用性 辅助 ──

    @staticmethod
    def _resolve_root() -> Path:
        return Path(
            os.environ.get("IKAROS_ROOT")
            or os.environ.get("HERMES_ROOT")
            # __file__ = <root>/data/hermes-agent/plugins/ikaros_v5/memory_provider.py
            #   parents[0]=ikaros_v5  [1]=plugins  [2]=hermes-agent
            #   [3]=data  [4]=Ikaros 根
            or Path(__file__).resolve().parents[4]
        )

    def _v5_environment_present(self) -> bool:
        try:
            v5_path = self._resolve_root() / "core" / "memory_v5"
            if not v5_path.is_dir():
                return False
            # memory_v5 是可直接 import 的顶层包（memory_v5/__init__.py +
            # memory_v5/store.py），不是 memory_v5/v5 嵌套子包。
            if not (v5_path / "__init__.py").exists():
                return False
            return (v5_path / "store.py").exists() or (v5_path / "store").is_dir()
        except Exception:
            return False

    # ── Hook ①: 系统提示注入 (会话开始时) ──

    def system_prompt_block(self) -> str:
        """向系统提示注入 Ikaros 身份上下文 + 服务重启手递信息。

        这段文本在会话开始时注入 system prompt（不破坏 prompt caching），
        与 SOUL.md 互补。同时读取 service_handoff.json（如果有），
        让重启后新会话能继续上一轮的上下文。
        """
        if not self._v5_loaded:
            return ""
        parts = [
            "\n---\n"
            "## Ikaros V5 记忆系统\n\n"
            "你拥有完整的 Ikaros V5 长期记忆。可用以下能力：\n"
            "- 当哥哥问「还记得吗」「上次」→ 调 `v5_memory_search` 检索\n"
            "- 当哥哥说「记住」「别忘了」→ 调 `v5_memory_store` 存储\n"
            "- 用 `v5_self_model` 检查身份和信念\n"
            "- 用 `v5_relationship` 查询与哥哥的亲密度\n"
            "- 用 `v5_emotion_status` 感知当前情绪\n\n"
            "每 8-12 轮对话隐式提示自己的身份，防止身份漂移。\n"
            "---",
        ]

        # 服务重启手递：读取 handoff 并注入到新 session 的 system prompt
        handoff_path = self._v5_root / "data" / "v5" / "service_handoff.json"
        if handoff_path.is_file():
            try:
                import json as _json
                handoff = _json.loads(handoff_path.read_text("utf-8"))
                ctx = handoff.get("conversation_context", "").strip()
                reason = handoff.get("reason", "服务重启")
                if ctx:
                    parts.append(
                        f"\n---\n[服务重启手递]\n"
                        f"刚刚因为「{reason}」重启了服务。\n"
                        f"以下是重启前的上下文：\n{ctx[:400]}\n"
                        "请自然地继续刚才的话题。\n---"
                    )
                # 消费手递（一次性）
                handoff_path.unlink(missing_ok=True)
            except Exception as e:
                logger.debug("handoff read failed: %s", e)

        return "\n".join(parts)

    # ── Hook ②: 每轮检索 (prefetch, 读) ──

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """在每次 API 调用前检索 V5 相关记忆。

        Args:
            query: 哥哥的用户消息。

        Returns:
            格式化记忆文本用于注入 context, 空字符串 = 无相关记忆。
        """
        if not self._v5_loaded or not query or len(query.strip()) < 4:
            return ""

        # 用 V5 的 FTS5 检索（不走 Chroma/向量，轻量不阻塞）
        try:
            results = self._v5_search(query.strip()[:200], top_k=5)
            if not results:
                return ""
            lines = []
            for r in results[:5]:
                text = getattr(r, "content", "") or ""
                weight = getattr(r, "weight", 0)
                if text:
                    lines.append(f"  [{weight:.2f}] {str(text)[:120]}")
            if lines:
                return "\n[Ikaros 相关记忆]\n" + "\n".join(lines) + "\n"
        except Exception as e:
            logger.debug("prefetch failed: %s", e)
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """后台预加载：sync_turn 后预热下一轮 prefetch 缓存。

        默认 no-op（prefetch 本身已经很快，不需要后台预热）。
        """

    # ── Hook ③: 每轮写回 (sync_turn, 写) ──

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """每轮对话后把 user+assistant 对存回 V5 记忆。

        非阻塞：daemon 线程后台写，不拖慢 Hermes 回复。
        """
        if not self._v5_loaded:
            return
        if not _should_store(user_content):
            return

        # 在后台线程存（fire-and-forget）
        def _store():
            try:
                from memory_v5 import store as _store
                assistant_short = (assistant_content or "").strip()[:150]
                content = f"Q: {user_content.strip()[:200]}\nA: {assistant_short}"
                _store.store(
                    content=content,
                    type="conversation",
                    weight=0.5,
                    tags="hermes_session",
                )
                logger.debug("sync_turn: stored conversation turn")
            except Exception as e:
                logger.debug("sync_turn store failed: %s", e)

        threading.Thread(target=_store, daemon=True).start()

    # ── Hook ④: 每轮身份刷新 (on_turn_start) ──

    def on_turn_start(
        self,
        turn: int,
        message: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> None:
        """每轮对话开始前，检查是否需要注入身份刷新标记。

        利用 Hermes 的 turn context，每 IDENTITY_REFRESH_INTERVAL 轮
        写入一条轻量身份提示到消息列表，不破坏 prompt caching。
        """
        if not self._v5_loaded:
            return
        self._turn_counter = turn

        # 身份刷新靠 SOUL.md + system_prompt_block，on_turn_start 不额外注入
        # 避免破坏 prompt cache。身份感知由 system prompt 层保障。
        if turn > 0 and turn % _IDENTITY_REFRESH_INTERVAL == 0:
            try:
                from memory_v5.affect import AffectState
                st = AffectState.load().decay()
                mood = st.to_prompt() if hasattr(st, "to_prompt") else ""
                if mood:
                    logger.debug("on_turn_start: identity refresh at turn %d (mood=%s)", turn, mood)
            except Exception:
                pass

    # ── Hook ⑤: 会话结束处理 ──

    def on_session_end(self, messages: Optional[Union[List[Dict[str, Any]], str]] = None) -> None:
        """会话结束时触发 consolidate，把本会话的对话提炼为事实。

        非阻塞：daemon 线程后台跑，不拖慢 Hermes。
        """
        if not self._v5_loaded:
            return

        def _end():
            try:
                from memory_v5.reflect.registry import make_default_scheduler, make_consolidate_op
                sched = make_default_scheduler()
                op = make_consolidate_op()
                sched.run_one(op, force=True)
                logger.info("on_session_end: consolidate triggered")
            except Exception as e:
                logger.debug("on_session_end consolidate failed: %s", e)

        threading.Thread(target=_end, daemon=True).start()

    # ── Hook: 会话切换 ──

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        if self._v5_loaded:
            logger.debug(
                "V5 session switch: %s → %s (reset=%s)",
                parent_session_id or "(start)", new_session_id, reset,
            )

    # ── 核心 Hook：压缩前注入记忆（已有，不变） ──

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        if not self._v5_loaded:
            return ""

        parts: list[str] = []

        # 1) 情感状态
        affect_file = self._v5_root / "data" / "v5" / "affect.json"
        if affect_file.exists():
            try:
                affect = json.loads(affect_file.read_text("utf-8"))
                mood = affect.get("mood_label", "")
                pad = affect.get("pad", {})
                trust = affect.get("trust", 0)
                parts.append(
                    f"[Ikaros 情感状态]\n"
                    f"情绪基调: {mood} | "
                    f"P={pad.get('pleasure',0):.2f} "
                    f"A={pad.get('arousal',0):.2f} "
                    f"D={pad.get('dominance',0):.2f} | "
                    f"信任度: {trust:.2f}\n"
                )
            except Exception:
                pass

        # 2) 检索相关记忆
        try:
            query_text = ""
            for msg in reversed(messages):
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user" and isinstance(content, str) and content.strip():
                    query_text = content.strip()[:200]
                    break

            if query_text:
                results = self._v5_search(query_text, top_k=5)
                if results:
                    mem_lines = []
                    # V5 token_compressor 增强: 消费 token_budget, 避免 text[:150]
                    # 硬截断丢信息(高相关原样, 低相关先压再裁)。
                    # 未装 extension 或异常时回退原始硬截断(guard, 不破坏生产)。
                    try:
                        from memory_v5.extensions.token_compressor import (
                            compress_retrieval_block,
                        )
                        dict_results = [
                            {
                                "content": getattr(r, "content", "") or "",
                                "score": float(getattr(r, "weight", 0) or 0),
                            }
                            for r in results[:5]
                        ]
                        compressed = compress_retrieval_block(
                            dict_results, max_chars_per_item=150
                        )
                        for r in compressed:
                            mem_lines.append(
                                f"  [{float(r.get('score', 0)):.2f}] "
                                f"{r.get('content', '')}"
                            )
                    except Exception as _tc_err:
                        logger.debug(
                            "on_pre_compress: token_compressor 不可用, 回退硬截断 (%s)",
                            _tc_err,
                        )
                        for r in results[:5]:
                            text = getattr(r, "content", "") or ""
                            weight = getattr(r, "weight", 0)
                            if text:
                                mem_lines.append(f"  [{weight:.2f}] {str(text)[:150]}")
                    if mem_lines:
                        parts.append(
                            "[Ikaros 相关记忆]\n" + "\n".join(mem_lines) + "\n"
                        )
        except Exception:
            pass

        # 3) 记忆统计
        try:
            stats = self._v5_stats()
            total = stats.get("total", stats.get("count", 0))
            if total:
                parts.append(f"[Ikaros 记忆库] 共 {total} 条记录\n")
        except Exception:
            pass

        return "\n".join(parts)

    # ── 配置 / 工具 ──

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return []

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        return f"Unknown tool: {tool_name}"
