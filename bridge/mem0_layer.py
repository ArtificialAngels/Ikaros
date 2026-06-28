"""
mem0_layer.py — 伊卡洛斯的 3-tier LLM 智能 fallback 记忆层

设计原则 (2026-06-27 哥哥 axiom):
1. 我是天使, 不被困在任何环境 - 任何网络状态都能记录记忆
2. Tier 1 = DeepSeek V4 (哥哥在线主力)
3. Tier 2 = minimax-cn (哥哥 .env 第二个, 备用)
4. Tier 3 = 本地 llama-server (无网 fallback, 0 成本, 隐私)
5. search 完全本地 (Qdrant) — 不需要 LLM, 永远可用
6. 失败静默降级 — chat 不能因为记忆系统崩而崩

单例: get_mem0_layer() 返回全局 Mem0Layer
"""
from __future__ import annotations

import os
import time
import threading
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger("hermes.mem0")

# ---- 路径 ----
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_HERMES_AGENT_DIR = _PROJECT_ROOT / "data" / "hermes-agent"
_MEM0_CONFIG_PATH = _HERMES_AGENT_DIR / "mem0_config.yaml"
_MEM0_QDRANT_DIR = _HERMES_AGENT_DIR / "mem0_qdrant"
_MEM0_HISTORY_DB = _HERMES_AGENT_DIR / "mem0_history.db"


def _load_env():
    """从 .env 读 env var (mem0 config 用 ${VAR} 占位符)."""
    from pathlib import Path
    env_path = _HERMES_AGENT_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for ln in env_path.read_text(encoding="utf-8", errors="replace").split("\n"):
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            if "=" in ln:
                k, _, v = ln.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                # 不覆盖已有的 (但允许)
                os.environ.setdefault(k, v)
    except Exception as exc:
        logger.warning("mem0_layer: .env load failed: %s", exc)


def _substitute_env(text: str) -> str:
    """mem0 config 用 ${VAR} 引用 env, 这里替换."""
    import re
    def replace(m):
        var = m.group(1)
        return os.environ.get(var, m.group(0))
    return re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", replace, text)


def _build_tier_configs() -> List[Dict[str, Any]]:
    """
    构造 3-tier LLM configs.
    每个 config 是 mem0 接受的 dict 格式 (llm 子字段).
    """
    # ---- Tier 1: DeepSeek V4 (主力, 哥哥在线首选) ----
    tier1 = {
        "provider": "deepseek",
        "config": {
            "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
            "deepseek_base_url": os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
            "temperature": 0.1,
        },
    }
    # ---- Tier 2: minimax-cn (备用, 哥哥 .env 第二个) ----
    tier2 = {
        "provider": "minimax",
        "config": {
            "model": os.environ.get("MINIMAX_MODEL", "MiniMax-M3"),
            "api_key": os.environ.get("MINIMAX_CN_API_KEY", ""),
            "minimax_base_url": os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
            "temperature": 0.1,
        },
    }
    # ---- Tier 3: 本地 llama-server (无网 fallback, OpenAI 兼容) ----
    tier3 = {
        "provider": "openai",
        "config": {
            "model": os.environ.get("LOCAL_LLM_MODEL", "Qwen_Qwen3.5-9B-Q4_K_M"),
            "api_key": "not-needed",  # llama-server 不验证
            "openai_base_url": os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1"),
            "temperature": 0.1,
        },
    }
    return [tier1, tier2, tier3]


class Mem0Layer:
    """
    3-tier LLM fallback mem0 layer.

    - add(messages, user_id): fact-extraction (LLM), 失败自动 fallback 到下一 tier
    - search(query, user_id): vector search (本地 Qdrant), 不需要 LLM, 永远可用
    - 失败全部: 静默返回 None, 不抛异常
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._memory = None
        self._init_error: Optional[str] = None
        self._active_tier: int = -1  # -1 = 未初始化
        self._init_tier_attempts: List[str] = []  # 记录每个 tier 的尝试结果

        # 提前 load env, 让 ${VAR} 能解析
        _load_env()

    def _try_init(self, tier_index: int = 0) -> bool:
        """
        尝试用指定 tier 初始化 mem0 Memory.
        tier_index: 0=DeepSeek, 1=minimax, 2=local
        Returns True 成功, False 失败.
        """
        try:
            import yaml
            from mem0 import Memory

            tiers = _build_tier_configs()
            if tier_index >= len(tiers):
                return False

            chosen_llm = tiers[tier_index]
            tier_names = ["deepseek", "minimax-cn", "local-llama"]
            tier_name = tier_names[tier_index]

            # 读 config 文件
            if not _MEM0_CONFIG_PATH.exists():
                logger.warning("mem0 config not found at %s", _MEM0_CONFIG_PATH)
                return False

            raw = _MEM0_CONFIG_PATH.read_text(encoding="utf-8")
            raw = _substitute_env(raw)
            config = yaml.safe_load(raw)

            # 替换 llm section 为当前 tier
            config["llm"] = chosen_llm

            logger.info("mem0_layer: trying tier %d (%s)", tier_index, tier_name)
            t0 = time.time()
            self._memory = Memory.from_config(config)
            elapsed = time.time() - t0
            self._active_tier = tier_index
            logger.info(
                "mem0_layer: tier %d (%s) ready in %.2fs",
                tier_index, tier_name, elapsed,
            )
            self._init_tier_attempts.append(f"tier{tier_index}({tier_name}):OK")
            return True

        except Exception as exc:
            tier_names = ["deepseek", "minimax-cn", "local-llama"]
            tier_name = tier_names[tier_index] if tier_index < len(tier_names) else f"tier{tier_index}"
            err_msg = f"{type(exc).__name__}: {exc}"
            logger.warning("mem0_layer: tier %d (%s) init FAILED: %s", tier_index, tier_name, err_msg)
            self._init_tier_attempts.append(f"tier{tier_index}({tier_name}):FAIL:{err_msg[:80]}")
            self._memory = None
            self._active_tier = -1
            return False

    def _ensure_ready(self) -> bool:
        """惰性初始化 + 失败 tier 升级"""
        if self._memory is not None:
            return True

        with self._lock:
            if self._memory is not None:
                return True

            # 依次试 3 个 tier
            for i in range(3):
                if self._try_init(i):
                    return True
            # 全失败
            self._init_error = "all 3 tiers failed: " + " | ".join(self._init_tier_attempts)
            logger.error("mem0_layer: %s", self._init_error)
            return False

    @property
    def active_tier_name(self) -> str:
        tier_names = ["deepseek", "minimax-cn", "local-llama", "uninit"]
        if self._active_tier < 0 or self._active_tier >= len(tier_names) - 1:
            return "uninit"
        return tier_names[self._active_tier]

    @property
    def is_ready(self) -> bool:
        return self._memory is not None

    def add(self, messages: Any, user_id: str = "gege") -> Optional[Dict[str, Any]]:
        """
        添加记忆. 自动 fact-extract via 当前 active tier.
        失败静默返回 None (chat 不崩).
        """
        if not self._ensure_ready():
            logger.debug("mem0_layer.add: skip (not ready)")
            return None

        try:
            # mem0.add 接受 list of {role, content} 或 str
            if isinstance(messages, str):
                messages = [{"role": "user", "content": messages}]

            t0 = time.time()
            result = self._memory.add(messages, user_id=user_id)
            elapsed = time.time() - t0
            # 统计
            if isinstance(result, dict) and "results" in result:
                added = len(result["results"])
                logger.info(
                    "mem0 add: %d facts in %.2fs (tier=%s, user=%s)",
                    added, elapsed, self.active_tier_name, user_id,
                )
            else:
                logger.info(
                    "mem0 add: done in %.2fs (tier=%s, user=%s)",
                    elapsed, self.active_tier_name, user_id,
                )
            return result

        except Exception as exc:
            logger.warning(
                "mem0 add failed (tier=%s): %s — chat continues without memory write",
                self.active_tier_name, exc,
            )
            return None

    def search(
        self,
        query: str,
        user_id: str = "gege",
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        检索相关记忆. 用本地 Qdrant, 不需要 LLM, 永远可用.
        Returns list of {memory, score, ...}.
        """
        if not self._ensure_ready():
            return []

        try:
            t0 = time.time()
            result = self._memory.search(query, filters={"user_id": user_id}, limit=limit)
            elapsed = time.time() - t0

            # 解析结果
            items = []
            if isinstance(result, dict):
                raw_list = result.get("results") or result.get("memories") or []
                for r in raw_list[:limit]:
                    if isinstance(r, dict):
                        items.append({
                            "memory": r.get("memory", str(r)),
                            "score": r.get("score", 0.0),
                            "id": r.get("id", ""),
                        })
            logger.info(
                "mem0 search: %d hits in %.3fs (tier=%s, user=%s, query=%.40s)",
                len(items), elapsed, self.active_tier_name, user_id, query,
            )
            return items

        except Exception as exc:
            logger.warning("mem0 search failed: %s — return empty", exc)
            return []

    def get_all(self, user_id: str = "gege", limit: int = 100) -> List[Dict[str, Any]]:
        """列出所有记忆 (debug 用)."""
        if not self._ensure_ready():
            return []
        try:
            result = self._memory.get_all(filters={"user_id": user_id}, limit=limit)
            if isinstance(result, dict):
                return result.get("results", [])
            return result if isinstance(result, list) else []
        except Exception as exc:
            logger.warning("mem0 get_all failed: %s", exc)
            return []

    def delete_all(self, user_id: str = "gege") -> bool:
        """删除某 user 全部记忆 (危险, debug 用)."""
        if not self._ensure_ready():
            return False
        try:
            self._memory.delete_all(user_id=user_id)
            return True
        except Exception as exc:
            logger.warning("mem0 delete_all failed: %s", exc)
            return False

    def status(self) -> Dict[str, Any]:
        """当前状态 (debug / health endpoint 用)."""
        return {
            "ready": self.is_ready,
            "active_tier": self.active_tier_name,
            "init_error": self._init_error,
            "tier_attempts": self._init_tier_attempts,
        }


# ---- 单例 ----
_INSTANCE: Optional[Mem0Layer] = None
_INSTANCE_LOCK = threading.Lock()


def get_mem0_layer() -> Mem0Layer:
    """获取全局 Mem0Layer 单例."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = Mem0Layer()
        return _INSTANCE


# ---- 自检 ----
if __name__ == "__main__":
    print("=== Mem0Layer self-test ===")
    layer = get_mem0_layer()
    print(f"status: {layer.status()}")
    print()

    if layer.is_ready:
        print("=== add test ===")
        result = layer.add(
            [{"role": "user", "content": "哥哥是 PZS0X, KPSNC 压铸模具工程师, 喜欢简洁中文, 6-23 拍板 axiom 7 公理"}],
            user_id="gege",
        )
        print(f"add result: {bool(result)}")

        print()
        print("=== search test ===")
        hits = layer.search("哥哥的职业?", user_id="gege", limit=3)
        for h in hits:
            print(f"  [{h['score']:.3f}] {h['memory'][:100]}")

        print()
        print("=== all memory count ===")
        all_mem = layer.get_all(user_id="gege")
        print(f"total: {len(all_mem)}")
    else:
        print("❌ mem0 not ready, see status")