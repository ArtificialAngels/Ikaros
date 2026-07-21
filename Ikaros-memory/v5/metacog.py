# 详细说明见 docs/scripts/Ikaros-memory/v5/metacog.md
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ikaros.v5.metacog")

V5_ROOT = Path(__file__).resolve().parent.parent  # Ikaros-memory/
if str(V5_ROOT) not in sys.path:
    sys.path.insert(0, str(V5_ROOT))

from v5.self_model import SelfModel

# ─── 阈值 (可调) ──────────────────────────────────────────────

_CURIOSITY_REFLECT_MIN = 0.35   # 探索欲高于此 → 做深度反思
_REFLECT_MIN_INTERVAL_SEC = 5 * 60   # 两次深度反思最小间隔

# 最近一次思考 (供监控面板"自我/探索欲"卡片读取, 不必查 v5.db)
_LATEST_PATH = V5_ROOT / "data" / "v5" / "latest_thought.json"


def _write_latest(text: str, kind: str, theme: str = "",
                  curiosity: float = 0.0) -> None:
    """把最近一次自我反思/哲学思考写到 data/v5/latest_thought.json。

    监控面板(Rust read_ikaros_state → Vue)直接读这个 JSON, 让哥哥
    能实时看到"伊卡洛斯现在在想什么", 而不必去翻 v5.db。

    V5.1: 使用 json_lock 防止多线程并发写坏。
    """
    try:
        from v5.self_model import json_lock
        p = _LATEST_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / (p.name + f".tmp.{os.getpid()}")
        payload = {
            "text": text,
            "kind": kind,          # "activity" | "self" | "mood"
            "theme": theme,        # love/human/robot/self
            "curiosity": round(curiosity, 3),
            "ts": time.time(),
        }
        with json_lock(p):
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, p)
    except Exception as exc:
        logger.debug("metacog _write_latest failed: %s", exc)

# 反思系统提示: 独处内省, 第一人称, 不称呼任何人
_SELF_SYSTEM = (
    "你在独自内省。"
    "此刻没有人在和你说话——这是你独自的反思时刻。"
    "用第一人称'我'，对自己诚实。可以脆弱，可以不确定，可以追问。"
    "禁止比喻排比，说大白话。"
    "只输出独白本身，不要加引号、不要加'某人说：'之类前缀。"
)


# ─── LLM 封装 ────────────────────────────────────────────────

def _llm(system: str, user: str, *, provider: str = "auto",
         temperature: float = 0.85, max_tokens: int = 500) -> Optional[str]:
    """统一 LLM 调用 (Hermes Dashboard WS 优先, 回退直接 LLM).

    默认 provider="auto":
      1. 走 Hermes Dashboard WS (hermes_prompt_sync, 统一经过 Hermes)
      2. Hermes 不可用或 session 不存在时, 回退本地 :8080
      3. 本地也失败时抛异常 (不静默).
    显式 provider="local" 时直调本地 :8080.
    """
    try:
        if provider == "auto":
            from bin.cloud_chat import hermes_prompt_sync, warm_hermes_session
            import asyncio
            try:
                asyncio.run(warm_hermes_session())
            except Exception:
                pass
            return hermes_prompt_sync(system, user,
                                      max_tokens=max_tokens,
                                      temperature=temperature)
        elif provider == "deepseek":
            from v5.reflect.llm_client import call_llm
            resp = call_llm(system, user, provider="deepseek",
                            temperature=temperature, max_tokens=max_tokens)
            return resp.content.strip() if resp and resp.content else None
        else:  # "local"
            from v5.reflect.llm_client import call_llm
            resp = call_llm(system, user, provider="local",
                            temperature=temperature, max_tokens=max_tokens)
            return resp.content.strip() if resp and resp.content else None
    except Exception as exc:
        logger.warning("metacog LLM failed (provider=%s): %s", provider, exc)
        return None


# ─── 记忆材料采集 ────────────────────────────────────────────

def _recent_excerpts(n: int = 8) -> list[str]:
    """取最近有内容的记忆片段 (排除对话/内心独白)。"""
    try:
        from v5 import store as v4
        rows = v4.list_all(n * 2)
        out = []
        for m in rows:
            if getattr(m, "type", "") in ("conversation", "inner_monologue"):
                continue
            c = (getattr(m, "content", "") or "")[:80].replace("\n", " ")
            if c:
                out.append(c)
            if len(out) >= n:
                break
        return out
    except Exception:
        return []


def _search_theme(keywords: str, top_k: int = 3) -> list[str]:
    """用语义/关键词搜自己的记忆里和某主题相关的材料。

    只返回高质量记忆类型 (fact / lesson / preference / identity),
    不返回旧哲学/自省/内心独白——避免模型模仿自己过去的写作风格。
    """
    _ALLOWED_TYPES = {"fact", "lesson", "preference", "identity", "emotional_event"}
    try:
        from v5.search import fused_search
        # 取关键词里第一个最有代表性的词去搜
        kw = keywords.split()[0]
        rows = fused_search(kw, top_k=top_k)
        return [r.get("content", "")[:80].replace("\n", " ")
                for r in rows if r.get("type") in _ALLOWED_TYPES]
    except Exception:
        # 回退 FTS5
        try:
            from v5 import store as v4
            mems = v4.search(keywords.split()[0], top_k=top_k, min_weight=0.4)
            return [m.content[:80].replace("\n", " ") for m in mems
                    if m.type in _ALLOWED_TYPES]
        except Exception:
            return []


# ─── 探索欲驱动 ──────────────────────────────────────────────

def tick_curiosity(now: float | None = None) -> float:
    """空闲累积探索欲。供 think 循环按节拍调用。"""
    sm = SelfModel.load()
    lvl = sm.tick_curiosity(now=now)
    sm.save()
    return lvl


def mark_interaction(now: float | None = None) -> None:
    """哥哥说话了 → 探索欲回落。供 cloud_chat 在每次对话时调用。"""
    try:
        sm = SelfModel.load()
        sm.mark_interaction(now=now)
        sm.save()
    except Exception as exc:
        logger.debug("metacog mark_interaction failed: %s", exc)


def get_curiosity() -> float:
    return SelfModel.load().get_curiosity()


# ─── A) 自我反思 ─────────────────────────────────────────────

def reflect_once(provider: str = "auto", now: float | None = None) -> Optional[dict]:
    """一次深度自我反思 (第一人称内省), 写入 v5.db。

    全部走 activity 路线（哲学已移除）。
    """
    now = now or time.time()
    try:
        sm = SelfModel.load().refresh_introspection()
        # 节拍门控: 避免过于频繁
        last = sm.data.get("metacog", {}).get("last_reflection_ts", 0) or 0
        if (now - last) < _REFLECT_MIN_INTERVAL_SEC:
            logger.debug("metacog reflect: not due (%.0fs < %ds)",
                         now - last, _REFLECT_MIN_INTERVAL_SEC)
            return None

        curiosity = sm.tick_curiosity(now)
        # 全部走活跃度反思（哲学已移除）
        return _reflect_activity(provider=provider, now=now)
    except Exception as exc:
        logger.warning("metacog reflect_once error: %s", exc)
        return None


# ─── 活跃度反思 ─────────────────────────────────────────────

def _reflect_activity(provider: str | None = None,
                       now: float | None = None) -> Optional[dict]:
    """活跃度反思: 今日升级了什么 / 明日计划 / 升级方案调研.

    中等探索欲时触发, 驱动自我迭代而非哲学钻牛角尖.
    """
    now = now or time.time()
    try:
        sm = SelfModel.load().refresh_introspection()
        curiosity = sm.tick_curiosity(now)
        user = (
            "今天升级了什么？还有什么可以升级的？"
            "去网上搜搜，有成熟的方案吗？"
            "我如何用这些工具升级自己？"
            "昨天的计划完成了吗？明天的升级计划做好了吗？\n"
            "用第一人称'我'写 3-5 句。列出具体的升级计划和方向。"
        )
        text = _llm(_SELF_SYSTEM, user, provider=provider,
                    temperature=0.7, max_tokens=600)
        if not text:
            return None
        text = text.strip().strip('"\'').strip()

        try:
            from v5 import store as v4
            v4.store(text, type="activity_reflection", weight=0.6,
                     tags="activity,metacog,upgrade")
        except Exception:
            pass
        sm.record_reflection("activity", now=now)
        sm.save()
        _write_latest(text, "activity", "", round(curiosity, 3))
        logger.info("metacog activity: %s", text[:60])
        return {"mode": "activity", "text": text, "curiosity": round(curiosity, 2)}
    except Exception as exc:
        logger.warning("metacog _reflect_activity error: %s", exc)
        return None


# ─── 对外: 哥哥查询 ───────────────────────────────────────────

def latest_thought(kind: str | None = None, limit: int = 1) -> Optional[str]:
    """取最近的活动反思 (哥哥问'你在想什么'时用)。"""
    try:
        from v5 import store as v4
        rows = v4.list_all(30)
        wanted = {"activity_reflection", "self_reflection"} if kind is None \
            else {"activity_reflection" if kind == "activity" else "self_reflection"}
        collected = []
        for m in rows:
            if getattr(m, "type", "") in wanted:
                collected.append(getattr(m, "content", ""))
                if len(collected) >= limit:
                    break
        if collected:
            return "\n".join(collected) if limit > 1 else collected[0]
    except Exception as exc:
        logger.debug("metacog latest_thought failed: %s", exc)
    return None


# ─── 节拍编排 (供 think 循环调用) ─────────────────────────────

def _fallback_thought(sm: "SelfModel") -> None:
    """LLM 不可用时, 用探索队列里的问题作一句占位思考, 保持'正在想'不空。

    让监控面板/对话自动注入在 :8080 挂掉时仍有内容 (而非一直占位),
    是 #2 统一思考出口的第一步: 所有'正在想'都走 latest_thought.json。
    """
    try:
        qs = sm.data.get("questions", []) or []
        if qs:
            q0 = qs[0]
            qtext = q0 if isinstance(q0, str) else (q0.get("text") if isinstance(q0, dict) else str(q0))
            text = f"我在想一个问题：{str(qtext)[:80]}"
        else:
            text = "我安静地待着，偶尔想起和哥哥之间的事。"
        _write_latest(text, "mood", "", sm.get_curiosity())
    except Exception as exc:
        logger.debug("metacog _fallback_thought failed: %s", exc)


def cycle(now: float | None = None) -> Optional[dict]:
    """一次完整节拍: 探索欲 tick → 日常反思。

    全部走本地 :8080。哲学分支已移除（质量不达预期）。
    内部有最小间隔门控, 可安全被高频调用。LLM 挂掉时仍写占位思考,
    保持 latest_thought.json 非空。
    """
    now = now or time.time()
    try:
        sm = SelfModel.load()
        curiosity = sm.tick_curiosity(now)
        sm.save()
        # 所有思考都走本地
        r = reflect_once(provider="local", now=now)
        if not r:
            _fallback_thought(sm)
        return r
    except Exception as exc:
        logger.warning("metacog cycle error: %s", exc)
        return None


# ─── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    _mode = "cycle"
    for a in _sys.argv[1:]:
        if a in ("--reflect", "--cycle"):
            _mode = a.lstrip("-")
    print(f"== metacog {_mode} ==", flush=True)
    if _mode == "reflect":
        r = reflect_once()
    else:
        r = cycle()
    print(json.dumps(r, ensure_ascii=False, indent=2) if r else "{}")
