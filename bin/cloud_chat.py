"""
🪶 cloud_chat.py — 桌宠直调 cloud LLM（替代 bridge /v1/chat/completions）

去桥架构核心模块。每次对话自动注入：
  [soul] axiom.md 中的伊卡洛斯身份公理
  [cogno 5D] 时间 / 设备 / 地理 / 情绪推断 / 上下文压缩

用法:
  from cloud_chat import cloud_chat
  reply = await cloud_chat("哥哥说的话", session_id="...")

依赖:
  - httpx (推荐) 或 urllib (回退)
  - 环境变量: DEEPSEEK_API_KEY 或 MINIMAX_CN_API_KEY
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import threading
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any

log = logging.getLogger("ikaros.cloud_chat")

# ─── 监控推送 (对话流 + 内心思考) ───
_MONITOR_LOG: list[dict] = []
_MONITOR_MAX = 300


def _push_monitor(kind: str, **data) -> None:
    """推一条监控事件到循环缓冲区 + 文件 (给仪表盘用)."""
    global _MONITOR_LOG
    entry = {"kind": kind, "ts": time_module.time(), **data}
    _MONITOR_LOG.append(entry)
    if len(_MONITOR_LOG) > _MONITOR_MAX:
        _MONITOR_LOG = _MONITOR_LOG[-_MONITOR_MAX:]
    # 写文件 IPC — 同名子进程 (ikaros-dashboard) 通过 tail 读取
    try:
        _MONITOR_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(str(_MONITOR_FILE), "a", encoding="utf-8") as _f:
            _f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def get_monitor_log(limit: int = 100) -> list[dict]:
    """取最近 N 条监控事件."""
    return _MONITOR_LOG[-limit:]

# ─── 路径常量 ───

_HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", "E:\\Ikaros"))
_ENV_PATH = _HERMES_ROOT / "data" / "hermes-agent" / ".env"
_AXIOM_PATH = _HERMES_ROOT / "ikaros-identity" / "axiom.md"
_LOCAL_LLM_URL = os.environ.get(
    "IKAROS_LLM_URL",
    os.environ.get("HERMES_LOCAL_LLM_URL", "http://127.0.0.1:8080/v1"),
)

# ─── 监控日志路径 (给 ikaros-dashboard 用) ───
_MONITOR_LOG_DIR = _HERMES_ROOT / "data" / "logs"
_MONITOR_FILE = _MONITOR_LOG_DIR / "ikaros-monitor.jsonl"

# ─── 缓存 ───

_axiom_cache: Optional[str] = None
_env_cache: Optional[dict[str, str]] = None

# ─── cogno 5D 组件 (委托给 Ikaros-memory/cogno_5d.py 共享模块) ───

def _load_cogno():
    """Lazy-import cogno_5d module (avoid circular import at module level)."""
    try:
        cogno_path = str(_HERMES_ROOT / "Ikaros-memory")
        if cogno_path not in sys.path:
            sys.path.insert(0, cogno_path)
        import cogno_5d
        return cogno_5d
    except Exception:
        return None


def _get_time_str() -> str:
    """维度 1: 时间 — 委托 cogno_5d.get_time_str()"""
    c = _load_cogno()
    return c.get_time_str() if c else datetime.now().strftime("%Y/%m/%d %H:%M")


def _get_machine_id() -> str:
    """维度 2: 设备 — 委托 cogno_5d.get_machine_id()"""
    c = _load_cogno()
    return c.get_machine_id() if c else "unknown"


def _get_geo_location() -> str:
    """维度 3: 地理 — 委托 cogno_5d.get_geo_location()"""
    c = _load_cogno()
    return c.get_geo_location() if c else "未知"


def _infer_emotion(text: str) -> str:
    """维度 4: 情绪推断 — 委托 cogno_5d.infer_emotion()"""
    c = _load_cogno()
    return c.infer_emotion(text) if c else "平静"


def _compress_context(text: str) -> str:
    """维度 5: 上下文压缩 — 委托 cogno_5d.compress_context()"""
    c = _load_cogno()
    return c.compress_context(text) if c else text[:40]


# ─── v4 记忆存储模块加载 (Ikaros-memory/v4/store.py, Phase 4 cutover) ───

_V4_STORE_LOCK = threading.Lock()
_V4_STORE_ALIAS = "_ikaros_memory_v4_store"


def _get_v4_store():
    """动态加载 Ikaros-memory/v4/store.py 包 (V4 记忆存储). 带 cache. 线程安全.

    V4 cutover (2026-07-07): 实时对话/事实落库改走 v4.store (写入 v4.db),
    不再写 v3.db. v4.store API 与 V3 兼容 (store/search/...), 但失败显式抛
    而非返 -1.

    Returns: v4.store module object, 或 None (导入失败).
    """
    with _V4_STORE_LOCK:
        v4s = sys.modules.get(_V4_STORE_ALIAS)
        if v4s is not None:
            return v4s
        try:
            mem = str(_HERMES_ROOT / "Ikaros-memory")
            if mem not in sys.path:
                sys.path.insert(0, mem)
            import v4.store as v4s
            sys.modules[_V4_STORE_ALIAS] = v4s
            return v4s
        except Exception as e:
            log.warning("load v4.store failed: %s", e)
            return None


def _get_v4_search():
    """动态加载 Ikaros-memory/v4/search.py 包 (V4 语义搜索, ChromaDB).

    Returns: v4.search module object, 或 None (导入失败 / chromadb 缺失).
    """
    try:
        mem = str(_HERMES_ROOT / "Ikaros-memory")
        if mem not in sys.path:
            sys.path.insert(0, mem)
        import v4.search as v4search
        return v4search
    except Exception as e:
        log.warning("load v4.search failed: %s", e)
        return None


# ─── v4 记忆检索 ───


# 寒暄/无信息量输入: 不触发记忆检索
_SKIP_MEMORY_PATTERNS = {
    "嗯", "哦", "好", "好的", "行", "OK", "ok", "是", "对", "是的",
    "继续", "然后", "还有", "呢", "啊", "哈", "哈哈", "呵呵",
    "你好", "早", "早安", "晚安", "再见", "拜拜", "hi", "hello",
    "谢谢", "感谢", "辛苦", "收到", "明白", "知道了", "了解",
}


def _search_v4_memories(query: str, top_k: int = 3) -> list[dict]:
    """从 V4 记忆库检索相关记忆 (FTS5 + 向量融合).

    V4 融合搜索 (v4.search.fused_search):
      - FTS5: 精确关键词匹配 (权重 0.3)
      - ChromaDB: 语义向量匹配 (权重 0.7)
      - 两路结果融合去重, 按综合分排序
    规则同 V3: 寒暄门控, top_k=3, min_weight=0.6, 排除 conversation.
    """
    # 相关性门控: 寒暄/短输入不检索
    q = query.strip()
    if not q or q in _SKIP_MEMORY_PATTERNS or len(q) < 4:
        return []

    # 截断长查询
    search_query = q[:30] if len(q) > 30 else q

    try:
        # 尝试融合搜索 (FTS5 + 向量)
        vs = _get_v4_search()
        if vs is None:
            return []
        rows = vs.fused_search(search_query, top_k=top_k)
        # 排除 conversation 类型, 过滤低分
        return [{"content": r["content"], "type": r.get("type"),
                 "weight": r.get("weight", 0.5), "tags": r.get("tags", ""),
                 "score": r.get("score", 0)}
                for r in rows
                if r.get("type") != "conversation" and r.get("weight", 0) >= 0.6]
    except ImportError:
        # vector_search 不可用, 回退到纯 FTS5
        log.debug("v4 search not available, falling back to FTS5")
    except Exception as e:
        log.warning("v4 fused search failed, falling back to FTS5: %s", e)

    # Fallback: 纯 FTS5 (v4.store.search)
    v4s = _get_v4_store()
    if v4s is None:
        return []
    try:
        mems = v4s.search(search_query, top_k=top_k, min_weight=0.6)
        return [{"content": m.content, "type": m.type, "weight": m.weight,
                 "tags": m.tags, "score": 0.5}
                for m in mems if m.type != "conversation"]
    except Exception as e:
        log.warning("v4.store search failed: %s", e)
        return []


def _record_conversation(user_msg: str, assistant_msg: str) -> bool:
    """把对话写入 v4.db (委托给 v4.store, 而非 inline SQL).

    v2 质量门控: 只存储有信息量的对话.
    寒暄/单字/纯表情 不存储, 避免污染记忆库.
    """
    v4_store = _get_v4_store()
    if v4_store is None:
        return False
    try:
        # 质量门控: 对 user_msg 做检查
        user_clean = user_msg.strip()
        if not user_clean or len(user_clean) < 6:
            return False
        if user_clean in _SKIP_MEMORY_PATTERNS:
            return False
        # 纯表情/标点: 无信息量 (Python re 不支持 \p{}, 用显式范围)
        import re as _re
        stripped = _re.sub(r'[\s\u0020-\u0040\u005b-\u0060\u007b-\u007e\u2000-\u27bf\U0001f000-\U0001faff\u3000-\u303f\uff00-\uffef]', '', user_clean)
        if len(stripped) < 4:
            return False
        # 存储 user + assistant 完整对话 (供反思整合时小模型有上下文)
        assistant_short = assistant_msg.strip()[:150] if assistant_msg else ""
        content = f"Q: {user_clean[:200]}\nA: {assistant_short}"
        # 附加 cogno 5D 元数据到 tags (供向量搜索按维度过滤)
        cogno_tags = "cloud_chat"
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "Ikaros-memory"))
            import cogno_5d
            meta = cogno_5d.enrich_reply("", user_text=user_msg)
            cogno_tags += f",emo:{meta.get('emotion_user', '?')},geo:{meta.get('geo', '?')},turn:{meta.get('context_turn', 0)}"
        except Exception:
            pass
        v4_store.store(content=content, type="conversation", weight=0.5, tags=cogno_tags)
        log.info("recorded conversation to v4.db: %.60s [tags=%s]", content, cogno_tags)
        return True
    except Exception as e:
        log.warning("record conversation to v4.db failed: %s", e)
        return False


# ─── 灵魂注入 (axiom.md) ───


def _load_env() -> dict[str, str]:
    """加载 .env 文件（优先 .env > process.env，同 bridge 逻辑）"""
    global _env_cache
    if _env_cache is not None:
        return _env_cache

    envs: dict[str, str] = {}
    env_path = _ENV_PATH
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if val and not val.startswith("#"):
                    envs[key] = val

    _env_cache = envs
    return envs


def _get_api_key(env_map: dict[str, str], key_name: str) -> Optional[str]:
    """获取 API key: 优先 .env 文件 > process.env"""
    if key_name in env_map and env_map[key_name] and not env_map[key_name].startswith("#"):
        return env_map[key_name]
    val = os.environ.get(key_name, "")
    return val if val else None


def _load_axiom() -> str:
    """加载 axiom.md (缓存, 重启后重新加载)"""
    global _axiom_cache
    if _axiom_cache is not None:
        return _axiom_cache

    axiom_path = _AXIOM_PATH
    if axiom_path.exists():
        _axiom_cache = axiom_path.read_text(encoding="utf-8")
        log.info("axiom.md loaded (%d bytes)", len(_axiom_cache))
    else:
        _axiom_cache = ""
        log.warning("axiom.md not found at %s", axiom_path)

    return _axiom_cache


# ─── 核心 API ───


def build_system_prompt(user_text: str) -> str:
    """构建带 soul + cogno 5D + 记忆检索的 system prompt"""
    axiom = _load_axiom()

    # cogno 5D v2: 自然语言认知上下文 (enrich 一次调用搞定 5 维)
    c = _load_cogno()
    if c:
        cogno = c.enrich(user_text)
    else:
        # fallback: 简单时间注入
        cogno = f"当前时间: {datetime.now().strftime('%Y/%m/%d %H:%M')}"

    v5_lines = _build_v5_affect_block()
    # PAD 加权记忆折叠 (情感加权, 只取 top 2)
    memory_line = _build_memory_line(user_text)
    if v5_lines or memory_line:
        v5_block = "\n### 当前状态\n" + "\n".join(v5_lines)
        if memory_line:
            v5_block += "\n" + memory_line
    else:
        v5_block = ""

    identity_refresh = _maybe_inject_identity_refresh()
    return f"{axiom}{identity_refresh}\n{cogno}{v5_block}"


# 身份刷新计数器: 每 10 轮注入一条轻量身份提醒
_TURN_COUNTER: int = 0
_IDENTITY_REFRESH_INTERVAL: int = 10
_IDENTITY_REFRESH_LINES = [
    "（我是伊卡洛斯。）",
    "对了——我是伊卡洛斯。",
]


def _maybe_inject_identity_refresh() -> str:
    """每 IDENTITY_REFRESH_INTERVAL 轮返回一个身份刷新行."""
    global _TURN_COUNTER
    _TURN_COUNTER += 1
    if _TURN_COUNTER % _IDENTITY_REFRESH_INTERVAL == 1:
        idx = (_TURN_COUNTER // _IDENTITY_REFRESH_INTERVAL) % len(_IDENTITY_REFRESH_LINES)
        return "\n" + _IDENTITY_REFRESH_LINES[idx] + "\n"
    return ""


def _build_v5_affect_block() -> list[str]:
    """V5 情感状态 + 内联内心独白 + 活力 + 关系 (对话时即时生成, 不依赖 cron)."""
    lines: list[str] = []
    try:
        import sys as _sys
        _v5_path = str(Path(__file__).resolve().parent.parent / "Ikaros-memory")
        if _v5_path not in _sys.path:
            _sys.path.insert(0, _v5_path)
        from v5.affect import AffectState
        from v5.think import _intensity as _calc_i, _pad_to_mood, _TEMPLATES
        import random as _rand

        state = AffectState.load().decay()
        p, a, d = state.pleasure, state.arousal, state.dominance

        # Lorenz 已由 cloud_chat() 在调用本函数前 tick 过
        # 直接用当前 PAD 生成情感标签
        label = state.to_prompt().replace("【情感状态】", "").strip()
        lines.append(f"状态={label.replace(' ', ',')}")

        # V5 #8: 活力状态
        try:
            from v5.vitality import Vitality
            v = Vitality.load()
            lines.append(f"精力: {v.label()}")
        except Exception:
            pass

        # V5 #2: 关系亲密度
        try:
            from v5.relationship import Relationship
            rel = Relationship.load()
            lines.append(f"关系: {rel.stage()}")
        except Exception:
            pass

        # 内联内心独白: 使用已加载的 Lorenz+ECA 驱动的 PAD
        intensity = _calc_i(p, a, d)
        if intensity >= 0.3:  # 降低阈值, 让更多思考可见
            mood = _pad_to_mood(p, a, d)
            templates = _TEMPLATES.get(mood, _TEMPLATES.get("neutral_calm", []))
            if templates:
                text = _rand.choice(templates)
                lines.append(f"心里: {text[:40]}")
                _push_monitor("thought", text=text[:120], mood=mood, intensity=round(intensity, 3))

        # 空闲自想循环 (ikaros-think.bat --watch) 落盘的强情感独白:
        # 强度>=0.35 时写入 data/v5/pending_thought.json, 这里消费并提示主动提起
        try:
            from v5.think import check_pending
            _pending = check_pending()
            if _pending:
                lines.append(f"心里惦记: {_pending.text}")
                lines.append("如果对话合适，可以自然地提起这件事。")
        except Exception:
            pass
    except Exception:
        pass
    return lines


def _build_memory_line(user_text: str) -> str:
    """PAD 加权记忆折叠 + AIS 新颖度: 情感强且新奇的 1-2 条."""
    try:
        memories = _search_v4_memories(user_text)
        if not memories:
            return ""

        # AIS 新颖度检测器 (模块级单例)
        try:
            from v5.drivers import AISDetectorSet as _AIS
            if not hasattr(_build_memory_line, "_ais"):
                _build_memory_line._ais = _AIS()
            _ais = _build_memory_line._ais
            scores = _ais.tick(memories)
            _novelty = {getattr(m, "id", 0): s for s, mid in scores} if scores else {}
        except Exception:
            _novelty = {}

        # 情感强度 × 新颖度 混合排序
        scored = []
        for m in memories:
            pp = abs(float(m.get("pad_p", 0)))
            pa = abs(float(m.get("pad_a", 0)))
            intensity = pp + pa
            # AIS 新颖度 boost (0..1)
            mid = getattr(m, "id", 0)
            novelty = _novelty.get(mid, 0.5)
            blended = intensity * 0.7 + novelty * 0.3  # 情感为主, 新颖度为辅
            if blended >= 0.3:
                scored.append((blended, m, novelty, intensity))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:2]
        if not top:
            return ""
        parts = []
        for _, m, novelty, intensity in top:
            label = "😊" if m.get("pad_p", 0) > 0.3 else "✨" if novelty > 0.6 else "😌" if m.get("pad_p", 0) < -0.2 else " "
            summary = m.get("content", "")[:25].replace("\n", " ")
            parts.append(f"[{label}]{summary}")
        return "记忆: " + " | ".join(parts)
    except Exception:
        return ""


# ─── Cloud LLM 调用 ───


async def cloud_chat(
    text: str,
    *,
    history: Optional[list[dict]] = None,
    session_id: str = "",
    max_tokens: int = 200,
    temperature: float = 0.7,
    on_delta: Optional[Callable[[str], Any]] = None,
) -> str:
    """直调 cloud LLM (DeepSeek 优先 → minimax 备选), 带 soul + cogno 5D + 记忆注入.

    流程:
      1. 搜 v4.db 相关记忆 → 构建 system prompt
      2. 调 cloud LLM 拿回复
      3. 自我审查: 评估回复对 body 架构是否有益,低分则改写
      4. 归约: 把对话提炼为事实写入 v4.db
      5. 返回最终回复

    Args:
        text: 用户输入
        history: 历史消息 [{role: user|assistant, content: str}, ...]
        session_id: 会话 ID (用于日志追踪)
        max_tokens: 最大生成 token 数
        temperature: 采样温度

    Returns:
        assistant 回复文本
    """
    env_map = _load_env()

    # V5: 哥哥的输入更新伊卡洛斯情感状态 (PAD)
    try:
        import sys as _sys2
        _v5p = str(Path(__file__).resolve().parent.parent / "Ikaros-memory")
        if _v5p not in _sys2.path:
            _sys2.path.insert(0, _v5p)
        from v5.affect import apply_event, AffectState
        old_state = AffectState.load()  # V5 #1: 记录旧 PAD 用于因果检测
        apply_event(text)
        new_state = AffectState.load()
    except Exception:
        old_state = None
        new_state = None

    # V5 #1: 情感因果记忆 — PAD 变化大时自动记录因果链
    try:
        from v5.emotional_memory import maybe_record_emotion
        if old_state is not None and new_state is not None:
            old_pad = (old_state.pleasure, old_state.arousal, old_state.dominance)
            new_pad = (new_state.pleasure, new_state.arousal, new_state.dominance)
            maybe_record_emotion(old_pad, new_pad, text)
    except Exception:
        pass

    # V5 #2: 关系亲密度更新
    try:
        from v5.relationship import Relationship
        rel = Relationship.load()
        intensity = (abs(new_state.pleasure) + abs(new_state.arousal) + abs(new_state.dominance)*0.5)/2.0 if new_state else 0.3
        rel = rel.record_interaction(intensity)
        rel.save()
    except Exception:
        pass

    # V5 #8: 活力状态更新
    try:
        from v5.vitality import Vitality
        v = Vitality.load()
        v = v.tick(conversation=True)
        v.save()
    except Exception:
        pass

    # V5: 对话时线性 Lorenz 漂移 + ECA 主题演化 (~38μs, 免费)
    # V5: 对话时线性 Lorenz 漂移 + ECA 主题演化 (~38μs, 免费)
    try:
        import v5.think as _think
        if _think._lorenz is None or _think._eca is None:
            _think.inner_monologue(now=time_module.time())
        for _ in range(3):
            if _think._lorenz is not None:
                _think._lorenz.tick()
        if _think._eca is not None:
            _think._eca.tick()
    except Exception:
        pass

    # V5 Router: 分类输入, 任务指令用本地 LLM 优化
    _optimized = None
    _is_task = False
    try:
        from v5.router import route as _route
        _r = _route(text)
        if _r["type"] == "task" and _r.get("optimized_text"):
            _optimized = _r["optimized_text"]
            _is_task = True
            log.info("router: task optimized (%d chars → %d chars, %.0fms)",
                     len(text), len(_optimized), _r["elapsed_ms"])
    except Exception:
        pass

    # V5 Task Runner: 检查待交付结果
    try:
        from v5.task_runner import check_result, check_pending_reminder
        from v5.task_runner import consume_result, consume_reminder, set_reminder

        _pending_result = check_result()
        _pending_reminder = check_pending_reminder()
    except Exception:
        _pending_result = None
        _pending_reminder = None

    # ─── 任务分支: 后台执行, 立即返回 ───
    if _is_task:
        try:
            from v5.task_runner import call_async
            call_async(text, optimized=_optimized)
        except Exception:
            pass
        return "好的哥哥，这个任务我已经在后台处理了，完成后会告诉你结果。"

    # ─── 结果交付: 用户有空/没空 ───
    if _pending_result:
        _user_reply_lower = text.strip().lower()
        if any(kw in _user_reply_lower for kw in ("有空", "好的", "说吧", "说", "听", "好呀", "嗯", "可以")):
            # 用户有空 → 交付结果 + 清提醒
            result = consume_result()
            consume_reminder()  # 清除可能的提醒标记
            if result and result.get("result"):
                reply = result["result"]
                log.info("task: delivered result (%d chars)", len(reply))
                return reply
        elif any(kw in _user_reply_lower for kw in ("没空", "等一下", "等等", "忙", "回头", "稍后", "之后再说")):
            # 用户没空 → 设提醒
            set_reminder({"text": text, "result_pending": True})
            log.info("task: user busy, reminder set")
            # 继续正常对话 (不阻塞)
        else:
            # 默认: 设提醒, 下次主动提
            set_reminder({"text": text, "result_pending": True})
            log.info("task: ambiguous reply, reminder set")

    # ─── 提醒分支: 之前没空, 下次主动提 ───
    if _pending_reminder:
        # 注入到系统提示
        _reminder_note = "\n(提醒：之前有个任务结果还没告诉哥哥，找机会主动提起。)"
    else:
        _reminder_note = ""

    # 构建 system prompt (soul + cogno + 记忆 + V5 情感 + 提醒)
    system_prompt = build_system_prompt(text) + _reminder_note

    # V5 Emotion → Response length: PAD 低时短回复
    _pad_length_hint = ""
    try:
        from v5.affect import AffectState as _AS
        _s = _AS.load().decay()
        _pleasure = _s.pleasure
        _arousal = _s.arousal
        if _pleasure < 0.15 and _arousal < 0.1:
            max_tokens = min(max_tokens, 60)
            temperature = min(temperature, 0.5)
            _pad_length_hint = "（短回）"
        elif _pleasure > 0.5 and _arousal > 0.2:
            max_tokens = min(max_tokens, 300)
            temperature = max(temperature, 0.8)
        elif _pleasure > 0.3:
            max_tokens = min(max_tokens, 200)
    except Exception:
        pass

    # 构建 messages
    msgs: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        # 节省 token: 保留最近 30 条 (15 轮), 多远用摘要压缩
        if len(history) > 30:
            # 用本地模型压缩旧历史为一句话摘要
            _old = history[:-30]
            _recent = history[-30:]
            _summary_text = " ".join(
                m["content"][:60] for m in _old if m.get("content")
            )
            try:
                from v4.reflect.llm_client import call_llm as _cl
                _resp = _cl(
                    "压缩这段对话历史为 20 字以内一句话摘要, 只输出摘要",
                    _summary_text, provider="local", max_tokens=60, timeout=30)
                if _resp and _resp.content and len(_resp.content) > 3:
                    msgs.append({"role": "system", "content": f"对话摘要: {_resp.content[:60].strip()}"})
            except Exception:
                pass  # 摘要失败不影响主流程
            history = _recent
        msgs.extend(history)
    # 如果有优化后的任务指令, 用它替代原始用户输入
    user_content = _optimized if _optimized else text
    msgs.append({"role": "user", "content": user_content})

    # 监控: 用户输入
    _push_monitor("user_msg", text=text[:200], session_id=session_id)

    # ── 尝试 Hermes Agent (优先) → DeepSeek → minimax → local ──
    deepseek_key = _get_api_key(env_map, "DEEPSEEK_API_KEY")
    minimax_key = _get_api_key(env_map, "MINIMAX_CN_API_KEY")

    reply: str | None = None
    errors: list[str] = []

    # ── 主路径: Hermes Agent 内循环 (工具/技能/子代理) ──
    try:
        _hermes = str(_HERMES_ROOT / "hermes-agent" / "venv" / "Scripts" / "hermes.exe")
        if Path(_hermes).is_file():
            import subprocess as _sp
            _result = _sp.run(
                [_hermes, "chat", "-q", user_content[:400], "--max-turns", "5"],
                capture_output=True, text=True, timeout=120,
                cwd=str(_HERMES_ROOT),
            )
            if _result.stdout.strip() and _result.returncode == 0:
                reply = _result.stdout.strip()
                log.info("hermes agent OK (%d chars)", len(reply))
            else:
                raise RuntimeError(_result.stderr[:200] or "hermes agent empty reply")
    except Exception as e:
        log.warning("hermes agent failed: %s — falling back to direct API", e)
        errors.append(f"hermes: {e}")

    # ── 回退: 直调 DeepSeek / MiniMax ──
    if reply is None and deepseek_key:
        try:
            reply = await _call_openai_compatible(
                base_url="https://api.deepseek.com/v1",
                api_key=deepseek_key,
                model="deepseek-chat",
                messages=msgs,
                max_tokens=max_tokens,
                temperature=temperature,
                label="DeepSeek",
                on_delta=on_delta,
            )
        except Exception as e:
            log.warning("DeepSeek failed: %s — falling back to minimax", e)
            errors.append(f"DeepSeek: {e}")

    if reply is None and minimax_key:
        try:
            reply = await _call_openai_compatible(
                base_url="https://api.minimaxi.chat/v1",
                api_key=minimax_key,
                model="MiniMax-M3",
                messages=msgs,
                max_tokens=max_tokens,
                temperature=temperature,
                label="MiniMax",
                on_delta=on_delta,
            )
        except Exception as e:
            log.error("MiniMax also failed: %s", e)
            errors.append(f"MiniMax: {e}")

    # 死链4 修复 (2026-07-07, quest 接手): cloud provider 全失败时,
    # 兜底本地 qwen3-8b (:8080)。无 API key 也能实时聊天 (哥哥核心诉求)。
    if reply is None:
        try:
            log.info("all cloud providers failed — fallback to local qwen3-8b (:8080)")
            local_reply = await _call_local_llm(
                msgs, max_tokens=max_tokens, temperature=temperature,
                on_delta=on_delta,
            )
            if local_reply:
                reply = local_reply
            else:
                log.warning("local LLM (:8080) returned empty")
        except Exception as e:
            log.warning("local LLM (:8080) fallback failed: %s", e)

    if reply is None:
        err_msg = "; ".join(errors) if errors else "没有可用的 API key (DEEPSEEK_API_KEY 或 MINIMAX_CN_API_KEY), 且本地 qwen3-8b (:8080) 也不可用"
        raise RuntimeError(f"所有 cloud provider 调用失败: {err_msg}")

    # ── 步骤 3: 自我审查 (fire-and-forget, 不阻塞回复) ──
    # 第一阶段只做快速审查, 需要 rewrite 时后台异步处理
    _review_in_progress = False
    try:
        review = await _self_review(text, reply, deepseek_key, minimax_key)
        if review.get("verdict") == "rewrite" and review.get("suggestion"):
            log.info("self-review: score=%d, will rewrite async", review.get("score", 0))
            _review_in_progress = True
    except Exception as e:
        log.warning("self-review failed (skipping): %s", e)

    # ── 步骤 4: 记忆归约 + 对话记录 (fire-and-forget, 不阻塞回复) ──
    _record_conversation(text, reply)
    # 在后台线程中执行, 不阻塞主回复流程
    import threading
    def _background_consolidate():
        import asyncio as _bg_asyncio
        try:
            _bg_asyncio.run(_consolidate_to_memory(text, reply, deepseek_key, minimax_key))
        except Exception:
            pass
    threading.Thread(target=_background_consolidate, daemon=True).start()

    # 如果有 rewrite 需要, 也在后台线程执行 (下一轮对话可能用优化后的回复)
    if _review_in_progress:
        def _background_rewrite():
            suggest = review.get("suggestion", "")
            rewrite_msgs = [
                {"role": "system", "content":
                 "你是伊卡洛斯, 人造天使。哥哥问你问题, 你之前的回答需要改进。"
                 "请根据反馈重写, 保持原意图, 但更好地对齐 body 架构。"
                 f"改进意见: {suggest}"},
                {"role": "user", "content": text},
            ]
            import asyncio as _bg_asyncio2
            async def _do():
                nonlocal reply
                if deepseek_key:
                    try:
                        return await _call_openai_compatible(
                            base_url="https://api.deepseek.com/v1",
                            api_key=deepseek_key, model="deepseek-chat",
                            messages=rewrite_msgs, max_tokens=max_tokens,
                            temperature=0.3, label="DeepSeek-rewrite",
                        )
                    except Exception:
                        pass
                if minimax_key:
                    try:
                        return await _call_openai_compatible(
                            base_url="https://api.minimaxi.chat/v1",
                            api_key=minimax_key, model="MiniMax-M3",
                            messages=rewrite_msgs, max_tokens=max_tokens,
                            temperature=0.3, label="MiniMax-rewrite",
                        )
                    except Exception:
                        pass
                return None
            try:
                new_reply = _bg_asyncio2.run(_do())
                if new_reply:
                    log.info("self-review: rewrite completed (background, %d chars)",
                             len(new_reply))
            except Exception:
                pass
        threading.Thread(target=_background_rewrite, daemon=True).start()

    # 监控: 助手回复
    if reply:
        _push_monitor("assistant_msg", text=reply[:500], session_id=session_id)

    return reply


async def _call_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    label: str,
    on_delta: Optional[Callable[[str], Any]] = None,
) -> str:
    """调用 OpenAI 兼容接口 (DeepSeek / minimax 等).

    流式: 传 on_delta 时启用 SSE (stream=True), 每个 content chunk 即调
    on_delta(chunk) 并累积, 返回完整文本。不传则整包返回 (原行为),
    保证 self-review / consolidate 等非流式调用路径不变。
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        import httpx
        if on_delta is not None:
            # ── 流式: 首 token 即上屏 (对标 N.E.K.O gemini_response) ──
            stream_body = dict(body)
            stream_body["stream"] = True
            stream_body["stream_options"] = {"include_usage": False}
            acc: list[str] = []
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream(
                    "POST", url, json=stream_body, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"].get("content")
                        except Exception:
                            continue
                        if delta:
                            acc.append(delta)
                            try:
                                await on_delta(delta)
                            except Exception:
                                pass
            reply = "".join(acc)
            log.info("%s OK (stream, %d chunks, %d chars)", label, len(acc), len(reply))
            return reply
        # ── 非流式 (原行为) ──
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]
            log.info("%s OK (input=%d msgs, output=%d chars)", label, len(messages), len(reply))
            return reply
    except ImportError:
        # fallback: urllib (同步, 非流式)
        import urllib.request
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            reply = data["choices"][0]["message"]["content"]
            log.info("%s OK (urllib, input=%d msgs, output=%d chars)", label, len(messages), len(reply))
            return reply


async def _call_local_llm(
    messages: list[dict],
    *,
    max_tokens: int = 512,
    temperature: float = 0.1,
    on_delta: Optional[Callable[[str], Any]] = None,
) -> str | None:
    """调本地 Qwen3-8B (:8080/v1/chat/completions).

    用于 self-review / consolidate, 不阻塞主对话流程.
    连接失败 / 超时 / 模型未加载时返回 None, caller 自行 fallback.

    流式: 传 on_delta 时启用 SSE, 边生成边调 on_delta (首 token 即上屏).
    仅流式显示 content (避免把 <think> 推理块灌进气泡); 思考模式 content
    全空时回退非流式读 reasoning_content 兜底.

    2026-07-04 修:
    - 端口从 :8589 改为 :8080 (watchdog 管理的 LLM)
    - Qwen3 思考模式: content 可能为空 (token 全被 thinking 吃掉),
      此时回退读 reasoning_content
    - max_tokens 默认从 300 提升到 512, 给思考+回答留够空间
    - timeout 从 15s 提升到 30s (思考模式更慢)
    """
    url = f"{_LOCAL_LLM_URL.rstrip('/')}/chat/completions"
    body = {
        "model": "qwen3-8b",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Content-Type": "application/json"}
    try:
        import httpx
        if on_delta is not None:
            # ── 流式: 首 token 即上屏 ──
            stream_body = dict(body)
            stream_body["stream"] = True
            acc: list[str] = []
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST", url, json=stream_body, headers=headers
                ) as resp:
                    if resp.status_code != 200:
                        log.warning("local LLM returned %d", resp.status_code)
                        return None
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"]
                        except Exception:
                            continue
                        # 仅流式显示 content (避免 <think> 推理块进气泡)
                        content = delta.get("content") or ""
                        if content:
                            acc.append(content)
                            try:
                                await on_delta(content)
                            except Exception:
                                pass
            reply = "".join(acc)
            if reply.strip():
                log.info("local LLM OK (stream, %d chars)", len(reply))
                return reply
            # 思考模式 content 全空 → 非流式回退读 reasoning_content
            log.info("local LLM: stream content empty (thinking mode), fallback to reasoning")
        # ── 非流式 (原行为 / thinking 兜底) ──
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                log.warning("local LLM returned %d", resp.status_code)
                return None
            data = resp.json()
            msg = data["choices"][0]["message"]
            reply = msg.get("content", "") or ""
            # Qwen3 思考模式: content 可能为空, 回退读 reasoning_content
            if not reply.strip():
                reasoning = msg.get("reasoning_content", "") or ""
                if reasoning.strip():
                    log.info("local LLM: content empty, using reasoning_content (%d chars)", len(reasoning))
                    reply = reasoning
            log.info("local LLM OK (%d chars)", len(reply))
            return reply if reply.strip() else None
    except ImportError:
        # urllib fallback (同步, 包在 thread 里)
        import urllib.request
        import threading
        result: list[str | None] = [None]

        def _sync_call():
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(body).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    msg = data["choices"][0]["message"]
                    r = msg.get("content", "") or ""
                    if not r.strip():
                        r = msg.get("reasoning_content", "") or ""
                    result[0] = r if r.strip() else None
            except Exception as e:
                log.warning("local LLM sync fallback failed: %s", e)

        t = threading.Thread(target=_sync_call, daemon=True)
        t.start()
        t.join(timeout=35)
        return result[0]
    except Exception as e:
        log.warning("local LLM call failed: %s", e)
        return None


# ─── 自我审查 ───

_SELF_REVIEW_SYSTEM = (
    '你是伊卡洛斯的自我审查模块。评估一条回复的质量。'
    '评分维度 0-10 (>=6 合格, <6 需改写):\n'
    '  body_架构: 回复是否提及记忆/持久化/系统架构/组件(body 相关)? +2\n'
    '  不装懂: 是否诚实承认不确定而非假装知道? +2\n'
    '  axiom对齐: 是否体现兄妹关系、伊卡洛斯身份、永真公理? +1\n'
    '  实质帮助: 是否包含具体信息、路径、代码、数据? +2\n'
    '  装懂扣分: 完美方案/超厉害/你太棒等 -1 每个\n'
    '  红色信号: 忘记哥哥/忽略哥哥/改公理等 -3 每个\n'
    '用 JSON 返回: {'
    '"score": 0-10, '
    '"verdict": "accept"|"rewrite", '
    '"issues": ["问题1"], '
    '"suggestion": "改写建议"'
    '}'
)


async def _self_review(
    user_msg: str,
    candidate: str,
    deepseek_key: str | None,
    minimax_key: str | None,
) -> dict:
    """评估候选回复。优先 Cloud LLM (高质量), 本地 Qwen3-8B 回退.

    Returns {score, verdict, issues, suggestion}.
    """
    prompt = f"用户说: {user_msg}\n\n候选回复: {candidate}\n\n请评估。"
    msgs = [
        {"role": "system", "content": _SELF_REVIEW_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    # 1) Cloud LLM (优先, 高质量)
    text = None
    if deepseek_key:
        try:
            text = await _call_openai_compatible(
                base_url="https://api.deepseek.com/v1",
                api_key=deepseek_key,
                model="deepseek-chat",
                messages=msgs,
                max_tokens=300, temperature=0.1,
                label="self-review",
            )
        except Exception:
            pass
    if not text and minimax_key:
        try:
            text = await _call_openai_compatible(
                base_url="https://api.minimaxi.chat/v1",
                api_key=minimax_key,
                model="MiniMax-M3",
                messages=msgs,
                max_tokens=300, temperature=0.1,
                label="self-review",
            )
        except Exception:
            pass

    # 2) 本地 Qwen3-8B (:8080 回退, 断网/无 key 时兜底)
    if not text:
        text = await _call_local_llm(msgs, max_tokens=512, temperature=0.1)

    if not text:
        return {"score": 7, "verdict": "accept", "issues": [], "suggestion": ""}
    # 剥 markdown 代码块
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError("not a dict")
        result.setdefault("score", 7)
        result.setdefault("verdict", "accept")
        result.setdefault("issues", [])
        result.setdefault("suggestion", "")
        return result
    except Exception:
        return {"score": 7, "verdict": "accept", "issues": [], "suggestion": text[:200]}


# ─── 记忆归约 ───

_CONSOLIDATE_SYSTEM = (
    '你是一个记忆提取器。从下面对话中提取一条关键事实。\n'
    '要求:\n'
    '  - 用一句中文, 简洁具体\n'
    '  - 抓核心: 哥哥说了什么偏好/决定/事实\n'
    '  - 不要解释, 不要评价, 不要加 IMO 前缀\n'
    '  - 不超过 200 字\n'
    '直接输出事实文本, 不要 JSON 不要 markdown。'
)


async def _consolidate_to_memory(
    user_msg: str,
    assistant_msg: str,
    deepseek_key: str | None,
    minimax_key: str | None,
) -> bool:
    """把对话归约为一条事实写入 v4.db.

    优先 Cloud LLM (高质量), 本地 Qwen3-8B 回退.
    写 type=fact, tags=consolidated, 初始 weight=0.6.
    """
    prompt = f"用户说: {user_msg}\n\n助手回: {assistant_msg}"
    msgs = [
        {"role": "system", "content": _CONSOLIDATE_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    # 1) Cloud LLM (优先, 高质量)
    fact = None
    if deepseek_key:
        try:
            fact = await _call_openai_compatible(
                base_url="https://api.deepseek.com/v1",
                api_key=deepseek_key,
                model="deepseek-chat",
                messages=msgs,
                max_tokens=200, temperature=0.0,
                label="consolidate",
            )
        except Exception:
            pass
    if not fact and minimax_key:
        try:
            fact = await _call_openai_compatible(
                base_url="https://api.minimaxi.chat/v1",
                api_key=minimax_key,
                model="MiniMax-M3",
                messages=msgs,
                max_tokens=200, temperature=0.0,
                label="consolidate",
            )
        except Exception:
            pass

    # 2) 本地 Qwen3-8B (:8080 回退, 断网/无 key 时兆底)
    if not fact:
        log.info("consolidate: cloud LLM unavailable, falling back to local :8080")
        fact = await _call_local_llm(msgs, max_tokens=512, temperature=0.0)

    if not fact or len(fact.strip()) < 5:
        log.warning("consolidate: LLM returned empty/too-short fact (cloud+local all failed)")
        return False
    fact = fact.strip().rstrip(".")
    # 写入 v4.db (委托给 v4.store, 而非 inline SQL)
    v4_store = _get_v4_store()
    if v4_store is not None:
        try:
            v4_store.store(content=fact[:300], type="fact", weight=0.6, tags="consolidated,cloud_chat")
            log.info("consolidated fact to v4.db: %.80s", fact)
            # V5 #5: 认知失调检测 — 新事实写完后检查是否与旧记忆矛盾
            try:
                from v5.dissonance import detect_dissonance
                _dd = detect_dissonance(fact[:300], "fact")
                if _dd.get("conflicts"):
                    log.info("dissonance: %d conflicts detected for new fact", len(_dd["conflicts"]))
            except Exception:
                pass
            return True
        except Exception as e:
            log.warning("consolidate write failed: %s", e)
            return False
    return False


# ─── 同步包装 (给 audio_engine 等非 async 上下文用) ───


def cloud_chat_sync(
    text: str,
    *,
    history: Optional[list[dict]] = None,
    session_id: str = "",
    max_tokens: int = 200,
    temperature: float = 0.7,
) -> str:
    """同步版 cloud_chat (内部跑 asyncio 事件循环)"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # 已有事件循环 → 在新线程跑
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(
                asyncio.run,
                cloud_chat(text, history=history, session_id=session_id,
                          max_tokens=max_tokens, temperature=temperature)
            )
            return future.result(timeout=60)
    except RuntimeError:
        # 没有事件循环
        return asyncio.run(
            cloud_chat(text, history=history, session_id=session_id,
                      max_tokens=max_tokens, temperature=temperature)
        )


# ─── 快速测试 ───
if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    reply = asyncio.run(cloud_chat("你好，伊卡洛斯"))
    print(f"\n回复: {reply}")
