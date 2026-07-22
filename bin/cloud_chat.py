# For details, see docs/scripts/bin/cloud_chat.md

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re as _re
import sys
import asyncio
import threading
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any

log = logging.getLogger("ikaros.cloud_chat")

# ─── Monitor push (conversation flow + inner thoughts) ───
_MONITOR_LOG: list[dict] = []
_MONITOR_MAX = 300


def _push_monitor(kind: str, **data) -> None:
    """Push a monitor event to ring buffer + file (for dashboard)."""
    global _MONITOR_LOG
    entry = {"kind": kind, "ts": time_module.time(), **data}
    _MONITOR_LOG.append(entry)
    if len(_MONITOR_LOG) > _MONITOR_MAX:
        _MONITOR_LOG = _MONITOR_LOG[-_MONITOR_MAX:]
    # IPC: ikaros-dashboard reads via tail
    try:
        _MONITOR_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(str(_MONITOR_FILE), "a", encoding="utf-8") as _f:
            _f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def get_monitor_log(limit: int = 100) -> list[dict]:
    """Get last N monitor events."""
    return _MONITOR_LOG[-limit:]

# ─── Path constants ───

_HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", os.path.expanduser("~")))
_ENV_PATH = _HERMES_ROOT / "data" / "hermes-agent" / ".env"
_AXIOM_PATH = _HERMES_ROOT / "ikaros-identity" / "axiom.md"
_CAPABILITIES_PATH = _HERMES_ROOT / "ikaros-identity" / "capabilities.md"
_LOCAL_LLM_URL = os.environ.get(
    "IKAROS_LLM_URL",
    os.environ.get("HERMES_LOCAL_LLM_URL", "http://127.0.0.1:8080/v1"),
)

# ─── Monitor log path (for ikaros-dashboard) ───
_MONITOR_LOG_DIR = _HERMES_ROOT / "data" / "logs"
_MONITOR_FILE = _MONITOR_LOG_DIR / "ikaros-monitor.jsonl"

# ─── V5 self-cognition data (for context injection) ───
_V5_DATA_DIR = Path(__file__).resolve().parent.parent / "Ikaros-memory" / "data" / "v5"
_LATEST_THOUGHT_PATH = _V5_DATA_DIR / "latest_thought.json"
_SELF_MODEL_PATH = _V5_DATA_DIR / "self_model.json"

# Module-level state for context completion
_LAST_USER_TEXT = ""
_SELF_STATUS_INTERVAL = 5
_self_status_counter = 0
_last_activity_phrase = ""

# ─── Cache ───

_axiom_cache: Optional[str] = None
_capabilities_cache: Optional[str] = None
_env_cache: Optional[dict[str, str]] = None

# ─── Hermes session (delegated to hermes_client) ───

_HERMES_WARMED = False


async def warm_hermes_session():
    """Start the Hermes background worker (delegated to hermes_client).

    hermes_client maintains one shared WebSocket in a daemon thread,
    eliminating duplicate connections and token fighting.
    """
    global _HERMES_WARMED
    if _HERMES_WARMED:
        return
    try:
        from v5.hermes_client import start as _hermes_start
        _hermes_start()
        _HERMES_WARMED = True
        log.info("warm-hermes: hermes_client worker started")
    except Exception as e:
        log.warning("warm-hermes: failed (%s)", e)

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
    """Dimension 1: time — delegates to cogno_5d.get_time_str()"""
    c = _load_cogno()
    return c.get_time_str() if c else datetime.now().strftime("%Y/%m/%d %H:%M")


def _get_machine_id() -> str:
    """Dimension 2: device — delegates to cogno_5d.get_machine_id()"""
    c = _load_cogno()
    return c.get_machine_id() if c else "unknown"


def _get_geo_location() -> str:
    """Dimension 3: geo — delegates to cogno_5d.get_geo_location()"""
    c = _load_cogno()
    return c.get_geo_location() if c else "unknown"


def _infer_emotion(text: str) -> str:
    """Dimension 4: emotion inference — delegates to cogno_5d.infer_emotion()"""
    c = _load_cogno()
    return c.infer_emotion(text) if c else "calm"


def _compress_context(text: str) -> str:
    """Dimension 5: context compression — delegates to cogno_5d.compress_context()"""
    c = _load_cogno()
    return c.compress_context(text) if c else text[:40]


# ─── v5 memory store module (Ikaros-memory/v5/store.py, code migrated v4->v5 2026-07-12) ───

_V4_STORE_LOCK = threading.Lock()
_V4_STORE_ALIAS = "_ikaros_memory_v4_store"


def _get_v4_store():
    """Lazy load Ikaros-memory/v5/store.py (V5 memory store). Thread-safe.

    V4 cutover (2026-07-07): live conversation/fact writes go to v4.store (v4.db),
    replacing v3.db. v4.store API is V3-compatible (store/search/...), but
    failures raise instead of returning -1.

    Returns: v4.store module object, or None (import failure).
    """
    with _V4_STORE_LOCK:
        v4s = sys.modules.get(_V4_STORE_ALIAS)
        if v4s is not None:
            return v4s
        try:
            mem = str(_HERMES_ROOT / "Ikaros-memory")
            if mem not in sys.path:
                sys.path.insert(0, mem)
            from v5 import store as v4s
            sys.modules[_V4_STORE_ALIAS] = v4s
            return v4s
        except Exception as e:
            log.warning("load v4.store failed: %s", e)
            return None


def _get_v4_search():
    """Lazy load Ikaros-memory/v5/search.py (V5 semantic search, ChromaDB).

    Returns: v4.search module object, or None (import failure / chromadb missing).
    """
    try:
        mem = str(_HERMES_ROOT / "Ikaros-memory")
        if mem not in sys.path:
            sys.path.insert(0, mem)
        from v5 import search as v4search
        return v4search
    except Exception as e:
        log.warning("load v4.search failed: %s", e)
        return None


# ─── Temporal reference parsing (Chinese -> Unix timestamp range) ───

# CNSeq2TimeSpan: 30KB lightweight regex parser for relative time references.
# Core regexes are stable despite being unmaintained since 2019.
# Known bug: 3 x "Asia/shanghai" must be patched to "Asia/Shanghai".
_TEMPORAL_PARSER = None


def _get_temporal_parser():
    """Lazy-load CNSeq2TimeSpan (only on first temporal resolution)."""
    global _TEMPORAL_PARSER
    if _TEMPORAL_PARSER is not None:
        return _TEMPORAL_PARSER
    try:
        from CNSeq2TimeSpan.TimeNormalizer import TimeNormalizer
        _TEMPORAL_PARSER = TimeNormalizer()
        log.info("temporal parser (CNSeq2TimeSpan) loaded")
    except Exception as e:
        log.warning("temporal parser unavailable: %s", e)
        _TEMPORAL_PARSER = False
    return _TEMPORAL_PARSER


# Parsable temporal reference patterns (for stripping from queries)
_TEMPORAL_PATTERNS = [
    "大前天", "前天", "昨天", "今天", "明天", "后天", "大后天",
    "上周", "这周", "下周", "上星期", "这星期", "下星期",
    "上个月", "这个月", "下个月", "去年", "今年", "明年",
    "前几天", "这几天", "最近", "前阵子",
    "三天前", "两天前", "一天前", "几天前",
]


def _resolve_temporal_filter(query: str) -> tuple[str, float | None, float | None]:
    """Extract temporal references from user input, return (cleaned_query, start_ts, end_ts).

    When no temporal reference is found, returns (query, None, None).
    Timestamps are Unix epoch float; end_ts is the inclusive upper bound.
    """
    tp = _get_temporal_parser()
    if tp is False or tp is None:
        return query, None, None

    try:
        res = tp.parse(target=query)
    except Exception as e:
        log.debug("temporal parse failed: %s", e)
        return query, None, None

    if res.get("type") != "timespan":
        return query, None, None

    tsp = res.get("timespan")
    if not tsp or not tsp[0] or len(tsp[0]) < 2:
        return query, None, None

    start_str, end_str = tsp[0][0], tsp[0][1]
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
        start_ts = start_dt.timestamp()
        end_ts = end_dt.timestamp()
    except ValueError:
        return query, None, None

    # Strip resolved temporal phrases, keep semantic core
    cleaned = query
    for pat in sorted(_TEMPORAL_PATTERNS, key=len, reverse=True):
        if pat in cleaned:
            cleaned = cleaned.replace(pat, "").strip()
            break

    # Remove residual placeholders/filler words
    import re as _re
    cleaned = _re.sub(r"的?(事儿|事|吗|呢|啊|呀|么|嘛|哈|哦)", "", cleaned)
    cleaned = cleaned.strip()

    if not cleaned or len(cleaned) < 2:
        cleaned = query  # fallback to original query

    log.info("temporal resolved: '%s' → cleaned='%s' range=[%s, %s]",
             query[:40], cleaned[:30], start_str, end_str)
    return cleaned, start_ts, end_ts


# Memory/time query keywords (triggers "I don't know" signal)
_MEMORY_QUERY_PATTERNS = [
    "记得", "还记得", "想起", "回忆", "昨天", "前天", "上周", "上次",
    "之前", "以前", "过去", "说过", "聊过", "提过", "讨论过",
    "那天", "那天", "当时", "那时候",
]


def _looks_like_memory_query(text: str) -> bool:
    """Check if user input is asking about memory/past (used for anti-fabrication signal)."""
    t = text.lower()
    return any(p in t for p in _MEMORY_QUERY_PATTERNS)


# ─── v4 memory retrieval ───


# Low-info / greeting inputs: skip memory retrieval
_SKIP_MEMORY_PATTERNS = {
    "嗯", "哦", "好", "好的", "行", "OK", "ok", "是", "对", "是的",
    "继续", "然后", "还有", "呢", "啊", "哈", "哈哈", "呵呵",
    "你好", "早", "早安", "晚安", "再见", "拜拜", "hi", "hello",
    "谢谢", "感谢", "辛苦", "收到", "明白", "知道了", "了解",
}


def _search_v4_memories(query: str, top_k: int = 3) -> list[dict]:
    """Search v4 memory store (FTS5 + vector fusion + temporal resolution).

    Pipeline:
      1. Temporal resolution: date references -> date range filter
      2. FTS5 + ChromaDB fusion search (keyword + semantic)
      3. Time range filter (if resolved)

    V4 fusion search (v4.search.fused_search):
      - FTS5: exact keyword match (weight 0.3)
      - ChromaDB: semantic vector match (weight 0.7)
      - Two-way result fusion dedup, scored composite
    Same rules as V3: greeting gate, top_k=3, min_weight=0.6, exclude conversation.
    """
    # Relevance gate: greetings/short inputs skip retrieval
    q = query.strip()
    if not q or q in _SKIP_MEMORY_PATTERNS or len(q) < 4:
        return []

    # ── Temporal resolution ──
    cleaned_q, start_ts, end_ts = _resolve_temporal_filter(q)
    has_time_filter = start_ts is not None and end_ts is not None

    # ── Three-way fusion (FTS5 + vector + time range) -> delegate v5.memory_retrieval (R3) ──
    search_query = cleaned_q[:30] if len(cleaned_q) > 30 else cleaned_q
    time_range = (start_ts, end_ts) if has_time_filter else None
    try:
        _v5p = str(Path(__file__).resolve().parent.parent / "Ikaros-memory")
        if _v5p not in sys.path:
            sys.path.insert(0, _v5p)
        from v5.memory_retrieval import retrieve
        # Exclude user's original text to avoid re-injecting known info (spec R3)
        mems = retrieve(search_query, top_k=max(top_k, 5),
                        time_range=time_range, exclude=[query])
        merged = [
            {"id": m["id"], "content": m["content"], "type": m["type"],
             "weight": m["weight"], "tags": m.get("tags", ""),
             "score": m["score"], "pad_p": m.get("pad_p", 0.0),
             "pad_a": m.get("pad_a", 0.0), "created": m.get("created", 0)}
            for m in mems
        ]
        return merged[:top_k]
    except Exception as e:
        log.warning("memory_retrieval.retrieve failed: %s", e)
        return []


def _record_conversation(user_msg: str, assistant_msg: str) -> bool:
    """Write conversation to v4.db (delegated to v4.store).

    v2 quality gate: only store meaningful conversations.
    Greetings / single chars / emoji-only are skipped to avoid polluting memory.
    """
    v4_store = _get_v4_store()
    if v4_store is None:
        return False
    try:
        # Quality gate: check user_msg
        user_clean = user_msg.strip()
        if not user_clean or len(user_clean) < 6:
            return False
        if user_clean in _SKIP_MEMORY_PATTERNS:
            return False
        # Emoji/punctuation only: no information (explicit ranges, \p{} not available)
        import re as _re
        stripped = _re.sub(r'[\s\u0020-\u0040\u005b-\u0060\u007b-\u007e\u2000-\u27bf\U0001f000-\U0001faff\u3000-\u303f\uff00-\uffef]', '', user_clean)
        if len(stripped) < 4:
            return False
        # Store user + assistant as a complete pair (for reflection context)
        assistant_short = assistant_msg.strip()[:150] if assistant_msg else ""
        content = f"Q: {user_clean[:200]}\nA: {assistant_short}"
        # Attach cogno 5D metadata to tags (for vector search dimension filtering)
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


# ─── Soul injection (axiom.md) ───


def _load_env() -> dict[str, str]:
    """Load .env file (priority: .env > process.env, same as bridge logic)."""
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
    """Get API key: priority .env file > process.env."""
    if key_name in env_map and env_map[key_name] and not env_map[key_name].startswith("#"):
        return env_map[key_name]
    val = os.environ.get(key_name, "")
    return val if val else None


def _load_axiom() -> str:
    """Load axiom.md (cached, reloads on restart)."""
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


def _load_capabilities() -> str:
    """Load capabilities.md (Ikaros capability list, cached, reloads on restart)."""
    global _capabilities_cache
    if _capabilities_cache is not None:
        return _capabilities_cache

    if _CAPABILITIES_PATH.exists():
        _capabilities_cache = _CAPABILITIES_PATH.read_text(encoding="utf-8").strip()
        log.info("capabilities.md loaded (%d bytes)", len(_capabilities_cache))
    else:
        _capabilities_cache = ""
        log.warning("capabilities.md not found at %s", _CAPABILITIES_PATH)

    return _capabilities_cache


# ─── Core API ───

_RULES_PATH = _HERMES_ROOT / "docs" / "agent-rules.yaml"
_RULES_CACHE: list[dict] | None = None
_RULES_YAML_MTIME = 0.0


def _load_rules() -> list[dict]:
    """Load rule library (with mtime cache)."""
    global _RULES_CACHE, _RULES_YAML_MTIME
    try:
        mtime = _RULES_PATH.stat().st_mtime
        if _RULES_CACHE is not None and mtime <= _RULES_YAML_MTIME:
            return _RULES_CACHE
        import yaml
        with open(str(_RULES_PATH), encoding="utf-8") as f:
            _RULES_CACHE = yaml.safe_load(f).get("rules", [])
        _RULES_YAML_MTIME = mtime
    except Exception:
        _RULES_CACHE = []
    return _RULES_CACHE or []


def _select_relevant_rules(user_text: str) -> str:
    """Use local model to select and compress relevant rules for current context.

    Calls :8080 local LLM to pick 1-2 most relevant rules and compress to 1-2 sentences.
    Returns empty string on failure (does not block system prompt build).
    """
    rules = _load_rules()
    if not rules:
        return ""
    # Check if simple greeting / no rules needed
    q = user_text.strip().lower()
    if not q or len(q) < 8 or q in {
        "嗯", "哦", "好", "好的", "行", "ok", "是", "对",
        "继续", "然后", "谢谢", "感谢", "收到",
    }:
        return ""
    try:
        import json, http.client
        rule_list = "\n".join(f"{r['id']}: {r['text']}" for r in rules)
        prompt = (
            "Select the 1-2 most relevant rules for this task. "
            "Output rule ID and one-line reason only.\n"
            f"Task: {user_text[:150]}\nRules:\n{rule_list}"
        )
        body = json.dumps({
            "model": "local-llm", "messages": [
                {"role": "system", "content": "Select 1-2 most relevant rules, output: R1-DRY: reason"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 80, "temperature": 0.1, "stream": False,
        }).encode()
        conn = http.client.HTTPConnection("127.0.0.1", 8080, timeout=8)
        conn.request("POST", "/v1/chat/completions", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status == 200:
            d = json.loads(resp.read())
            selected = (d["choices"][0]["message"].get("content") or "").strip()
            if selected:
                return "\n## Operation hints\n" + selected
    except Exception:
        pass
    return ""


def build_system_prompt(user_text: str, history: Optional[list] = None) -> str:
    """Build system prompt with soul + cogno 5D + memory retrieval."""
    global _LAST_USER_TEXT
    _LAST_USER_TEXT = user_text
    axiom = _load_axiom()

    # cogno 5D v2: natural language cognitive context (single enrich call handles all 5 dims)
    c = _load_cogno()
    if c:
        cogno = c.enrich(user_text)
    else:
        # fallback: simple time injection
        cogno = f"Current time: {datetime.now().strftime('%Y/%m/%d %H:%M')}"

    # R2 rhythm awareness: structured rhythm data (interval + time-of-day), cloud generates tone
    rhythm_block = _build_rhythm_block()

    # R4 history summary (requires history)
    summary_block = _build_summary_block(history)
    # R5 user profile (negative preferences first)
    profile_block = _build_profile_block()
    # R6 emotion diff + emotion memory recall
    emotion_diff_block = _build_emotion_diff_block()
    emotion_recall_block = _build_emotion_recall_block(user_text)

    v5_lines = _build_v5_affect_block()
    # PAD-weighted memory folding (emotionally weighted, top 2 only)
    memory_line = _build_memory_line(user_text)
    if v5_lines or memory_line:
        v5_block = "\n### Current state\n" + "\n".join(v5_lines)
        if memory_line:
            v5_block += "\n" + memory_line
    else:
        v5_block = ""

    identity_refresh = _maybe_inject_identity_refresh()
    thought_note = _maybe_self_thought_note(user_text)
    # V5 context completion (Ikaros handoff): tell conversation role what background is doing
    auto_thought = _maybe_auto_thought()
    self_status = _maybe_self_status()
    task_note = _maybe_task_note()
    activity_note = _maybe_activity_note()
    # Rule injection: local model compresses and selects relevant rules
    rules_block = _select_relevant_rules(user_text)
    capabilities = _load_capabilities()

    # Base block (never trimmed)
    base = f"{axiom}{identity_refresh}\n{cogno}"
    # Status notes grouped together
    status_notes = thought_note + auto_thought + self_status + task_note + activity_note
    cap_block = (f"\n### My capabilities\n{capabilities}\n" if capabilities else "")

    # Priority blocks (spec 4.3): rhythm(1)>memory(2)>status(3)>rules(4)>relationship(5)>summary(6)>emotion(7)
    # Trim from lowest priority (highest number) when exceeding token budget
    ordered_blocks = [
        (1, rhythm_block),
        (2, v5_block),
        (3, status_notes),
        (4, rules_block),
        (5, profile_block),
        (6, summary_block),
        (7, emotion_diff_block + emotion_recall_block),
    ]
    return _enforce_token_budget(base, ordered_blocks, cap_block)


def _enforce_token_budget(base: str, ordered_blocks: list, cap_block: str) -> str:
    """spec 4.3: context block budget 800-1200 tokens, trim by priority when exceeded.

    Estimation: Chinese ~1 token/char, others ~0.5 token/char (char_x safety factor, default 1.0).
    base and cap_block are never trimmed. Normal Chinese prompts are not affected.
    """
    try:
        from v5.preprocess_config import get
        char_x = float(get("token_budget", "char_x", default=1.0))
        max_tokens = int(get("token_budget", "max", default=1200))
    except Exception:
        char_x, max_tokens = 1.0, 1200

    def _est(s: str) -> int:
        cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
        other = len(s) - cjk
        return int((cjk + other * 0.5) * char_x)

    def _total(dropped: set) -> int:
        return (_est(base) + _est(cap_block)
                + sum(_est(t) for p, t in ordered_blocks if t and p not in dropped))

    dropped: set = set()
    total = _total(dropped)
    while total > max_tokens:
        cands = [p for p, t in ordered_blocks if t and p not in dropped]
        if not cands:
            break
        dropped.add(max(cands))
        total = _total(dropped)
    return (base
            + "".join(t for p, t in ordered_blocks if t and p not in dropped)
            + cap_block)


def _is_asking_thinking(text: str) -> bool:
    """Detect if user is asking 'what are you thinking about' type questions."""
    t = (text or "").strip().lower()
    return any(k in t for k in (
        "你在想什么", "在想什么", "你在思考什么", "你想什么呢",
        "你刚才在想", "你心里在想", "你正想着", "你脑子里在想",
        "你最近在想", "你在琢磨什么", "你刚才想啥", "你想啥呢",
    ))


def _maybe_self_thought_note(user_text: str) -> str:
    """When user asks 'what are you thinking', inject Ikaros's latest reflection/philosophy note."""
    if not _is_asking_thinking(user_text):
        return ""
    try:
        _v5p = str(Path(__file__).resolve().parent.parent / "Ikaros-memory")
        if _v5p not in sys.path:
            sys.path.insert(0, _v5p)
        from v5.metacog import latest_thought
        thought = latest_thought()
        if thought:
            return ("\n(哥哥问你在想什么——你可以自然地告诉他你最近在思考：「"
                    + thought[:200] + "」)\n")
    except Exception:
        pass
    return ""


# ─── V5 context completion (Ikaros handoff 2026-07-11) ───
# Tell the conversation role what background is doing: self-thought / self-status / task / activity
# All functions return str, empty string = no injection this turn. Appended at end of build_system_prompt.

def _cjk_bigrams(s: str) -> set[str]:
    """Extract Chinese bigrams (for topic relevance check)."""
    s = "".join(_re.findall(r"[一-鿿]", s or ""))
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _maybe_auto_thought() -> str:
    """P0: each turn inject most recent self-thought as a natural reminder if relevant and curious.

    Data: latest_thought.json (written by metacog, same source as monitor card).
    Threshold: curiosity>=0.4 AND shares Chinese bigrams with current topic, then a light reminder.
    """
    try:
        if not _LATEST_THOUGHT_PATH.is_file():
            return ""
        obj = json.loads(_LATEST_THOUGHT_PATH.read_text(encoding="utf-8"))
        text = (obj.get("text") or "").strip()
        if not text:
            return ""
        cu = float(obj.get("curiosity", 0) or 0)
        if cu < 0.4:
            return ""
        # Relevance: only nudge if shared Chinese bigrams with current user topic
        a = _cjk_bigrams(text)
        b = _cjk_bigrams(_LAST_USER_TEXT or "")
        if a and b and not (a & b):
            return ""  # completely unrelated, don't disturb
        return "\n(You've been thinking about: " + text[:120] + ")\n"
    except Exception:
        return ""


def _maybe_self_status() -> str:
    """P0: every 5 turns inject a self-status line (curiosity/reflection count/recent belief update).

    Note: this is status info, not identity refresh (identity_refresh handles identity); non-duplicate.
    """
    global _self_status_counter
    _self_status_counter += 1
    if _self_status_counter % _SELF_STATUS_INTERVAL != 1:
        return ""
    try:
        if not _SELF_MODEL_PATH.is_file():
            return ""
        d = json.loads(_SELF_MODEL_PATH.read_text(encoding="utf-8"))
        cu = round(float((d.get("curiosity") or {}).get("level", 0)), 2)
        philo = len(d.get("philosophy") or [])
        # Most recently updated belief topic
        last_theme = ((d.get("metacog") or {}).get("last_changed_theme")) or None
        if not last_theme:
            beliefs = d.get("beliefs") or {}
            last_theme = "-"
            best = 0.0
            for k, v in beliefs.items():
                if isinstance(v, dict):
                    ts = v.get("updated", 0) or 0
                    if ts > best:
                        best = ts
                        last_theme = k
        return (f"\n(Self status: curiosity={cu} | reflections={philo} | "
                f"belief update: {last_theme})\n")
    except Exception:
        return ""


def _maybe_task_note() -> str:
    """P1: inject task status line when background task completed/failed (deliver when user is free).

    Reuses task_runner.check_result() (result lands in task_result.json).
    """
    try:
        from v5.task_runner import check_result
        r = check_result()
        if not r:
            return ""
        status = r.get("status")
        goal = (r.get("optimized") or r.get("text") or "后台任务")[:40]
        summary = (r.get("result") or r.get("error") or "")[:120]
        label = "已完成" if status == "done" else "失败"
        return (f"\n(后台任务「{goal}」{label} — 结果: {summary} "
                f"——哥哥有空时告诉他)\n")
    except Exception:
        return ""


def _maybe_activity_note() -> str:
    """P2: inject user activity status change (reuses cogno_5d real-time activity narrative).

    Note: Ikaros handoff originally reads ikaros-monitor.jsonl, but that file contains
    cloud_chat's own conversation log, not the user's activity. The real activity narrative
    is in cogno_5d._get_activity_narrative() (real-time from ikaros_monitor).
    Only inject on change to avoid duplication with cogno 5D block.
    """
    try:
        c = _load_cogno()
        if not c:
            return ""
        phrase = c._get_activity_narrative()
        if not phrase:
            return ""
        global _last_activity_phrase
        if phrase == _last_activity_phrase:
            return ""  # only inject on change
        _last_activity_phrase = phrase
        return f"\n(哥哥现状: {phrase})\n"
    except Exception:
        return ""


# Identity refresh counter: inject a light identity reminder every 10 turns
_TURN_COUNTER: int = 0
_IDENTITY_REFRESH_INTERVAL: int = 10
_IDENTITY_REFRESH_LINES = [
    "（我是伊卡洛斯。）",
    "对了——我是伊卡洛斯。",
]


def _maybe_inject_identity_refresh() -> str:
    """Return an identity refresh line every IDENTITY_REFRESH_INTERVAL turns."""
    global _TURN_COUNTER
    _TURN_COUNTER += 1
    if _TURN_COUNTER % _IDENTITY_REFRESH_INTERVAL == 1:
        idx = (_TURN_COUNTER // _IDENTITY_REFRESH_INTERVAL) % len(_IDENTITY_REFRESH_LINES)
        return "\n" + _IDENTITY_REFRESH_LINES[idx] + "\n"
    return ""


def _build_rhythm_block() -> str:
    """R2 rhythm: inject last-interval + time-of-day (structured data, cloud generates tone).

    Silent on failure, does not block system prompt build.
    """
    try:
        _v5p = str(Path(__file__).resolve().parent.parent / "Ikaros-memory")
        if _v5p not in sys.path:
            sys.path.insert(0, _v5p)
        from v5.rhythm import build_rhythm_block
        return build_rhythm_block()
    except Exception:
        return ""


def _build_summary_block(history) -> str:
    """R4 history summary: compress old turns into a density block (non-blocking, silent on failure).

    Uses build_summary_block_nb: immediately returns last cached summary,
    refreshes stale cache asynchronously in background, never delays first token (spec 4.1).
    """
    try:
        _v5p = str(Path(__file__).resolve().parent.parent / "Ikaros-memory")
        if _v5p not in sys.path:
            sys.path.insert(0, _v5p)
        from v5.summary import build_summary_block_nb
        return build_summary_block_nb(history)
    except Exception:
        return ""


def _build_profile_block() -> str:
    """R5 user profile: inject one negative-preference-priority line (non-blocking, silent on failure)."""
    try:
        _v5p = str(Path(__file__).resolve().parent.parent / "Ikaros-memory")
        if _v5p not in sys.path:
            sys.path.insert(0, _v5p)
        from v5.profile import build_profile_block
        return build_profile_block()
    except Exception:
        return ""


def _build_emotion_diff_block() -> str:
    """R6 emotion diff: inject a note when current vs last emotion gap is large (non-blocking, silent on failure)."""
    try:
        _v5p = str(Path(__file__).resolve().parent.parent / "Ikaros-memory")
        if _v5p not in sys.path:
            sys.path.insert(0, _v5p)
        from v5.emotional_memory import build_emotion_diff_block
        return build_emotion_diff_block()
    except Exception:
        return ""


def _build_emotion_recall_block(user_text: str) -> str:
    """R6 emotion memory recall: pull an old memory when user explicitly mentions emotion (non-blocking, silent on failure)."""
    try:
        _v5p = str(Path(__file__).resolve().parent.parent / "Ikaros-memory")
        if _v5p not in sys.path:
            sys.path.insert(0, _v5p)
        from v5.emotional_memory import build_emotion_recall_block
        return build_emotion_recall_block(user_text)
    except Exception:
        return ""


async def _clock_out(user_text: str, assistant_reply: str) -> None:
    """R7 Clock Out — lightweight state snapshot at conversation end (spec 2.7).

    Must be fire-and-forget: caller uses asyncio.create_task without await.
    Any step failure is silent, does not block main flow.
    """
    try:
        # 1. task_pending.json: if pending result, mark this as checked
        try:
            from v5.task_runner import check_result
            if check_result():
                _pp = _V5_DATA_DIR / "task_pending.json"
                if _pp.is_file():
                    import json as _json
                    try:
                        _d = _json.loads(_pp.read_text(encoding="utf-8"))
                    except Exception:
                        _d = {}
                    if not isinstance(_d, dict):
                        _d = {}
                    _d["last_clockout_seen"] = time_module.time()
                    try:
                        _pp.write_text(_json.dumps(_d, ensure_ascii=False), encoding="utf-8")
                    except Exception:
                        pass
        except Exception:
            pass

        # 2. self_model.json: update last_active
        try:
            if _SELF_MODEL_PATH.is_file():
                import json as _json
                _sm = _json.loads(_SELF_MODEL_PATH.read_text(encoding="utf-8"))
                if not isinstance(_sm, dict):
                    _sm = {}
                _sm["last_active"] = time_module.time()
                _SELF_MODEL_PATH.write_text(_json.dumps(_sm, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        # 3. latest_thought.json: important decision/new insight -> append (don't overwrite metacog output)
        try:
            _kw = ("决定", "方案", "结论", "想通", "明白", "懂了", "发现", "原来",
                   "决定了", "定下来", "搞清楚")
            if any(k in (user_text or "") for k in _kw):
                import json as _json
                _lt: dict = {}
                if _LATEST_THOUGHT_PATH.is_file():
                    try:
                        _lt = _json.loads(_LATEST_THOUGHT_PATH.read_text(encoding="utf-8"))
                    except Exception:
                        _lt = {}
                if not isinstance(_lt, dict):
                    _lt = {}
                _decs = _lt.get("decisions")
                if not isinstance(_decs, list):
                    _decs = []
                _snippet = (user_text or "")[:80].replace("\n", " ")
                _decs.append({"at": time_module.time(), "text": _snippet})
                _lt["decisions"] = _decs[-20:]
                _LATEST_THOUGHT_PATH.write_text(_json.dumps(_lt, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        # 4. relationship.json: interaction counter
        try:
            from v5.relationship import track_interaction
            track_interaction(0.3)
        except Exception:
            pass
    except Exception:
        pass


def _build_v5_affect_block() -> list[str]:
    """V5 emotion state + inline inner monologue + vitality + relationship (generated live, no cron)."""
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

        # Lorenz already ticked by cloud_chat() before this function
        # Use current PAD to generate emotion label directly
        label = state.to_prompt().replace("【情感状态】", "").strip()
        lines.append(f"status={label.replace(' ', ',')}")

        # V5 #8: vitality state
        try:
            from v5.vitality import Vitality
            v = Vitality.load()
            lines.append(f"energy: {v.label()}")
        except Exception:
            pass

        # V5 #2: relationship intimacy
        try:
            from v5.relationship import Relationship
            rel = Relationship.load()
            lines.append(f"bond: {rel.stage()}")
        except Exception:
            pass

        # Inline inner monologue: use already-loaded Lorenz+ECA driven PAD
        intensity = _calc_i(p, a, d)
        if intensity >= 0.3:  # lowered threshold for more visible thoughts
            mood = _pad_to_mood(p, a, d)
            templates = _TEMPLATES.get(mood, _TEMPLATES.get("neutral_calm", []))
            if templates:
                text = _rand.choice(templates)
                lines.append(f"inner voice: {text[:40]}")
                _push_monitor("thought", text=text[:120], mood=mood, intensity=round(intensity, 3))

        # Idle thought loop (ikaros-think.bat --watch) dumped strong emotion monologues:
        # intensity >= 0.35 written to data/v5/pending_thought.json; consume here and hint to bring up
        try:
            from v5.think import check_pending
            _pending = check_pending()
            if _pending:
                lines.append(f"on my mind: {_pending.text}")
                lines.append("If it fits the conversation, you can bring this up naturally.")
        except Exception:
            pass
    except Exception:
        pass
    return lines


def _build_memory_line(user_text: str) -> str:
    """PAD weighted memory folding + AIS novelty: emotionally strong and novel 1-2 items.

    When PAD values are 0 (no emotional metadata stored), falls back to
    using memory weight as the intensity signal.
    """
    try:
        memories = _search_v4_memories(user_text)
        if not memories:
            # Memory recall empty: check if user is asking time/memory questions
            # If so, inject anti-fabrication signal to prevent model from inventing memories
            if _looks_like_memory_query(user_text):
                return "memory: (No recall match — if asked about the past, honestly say you don't remember, don't fabricate)"
            return ""

        # AIS novelty detector (module-level singleton)
        try:
            from v5.drivers import AISDetectorSet as _AIS
            if not hasattr(_build_memory_line, "_ais"):
                _build_memory_line._ais = _AIS()
            _ais = _build_memory_line._ais
            scores = _ais.tick(memories)
            _novelty = {mid: s for s, mid in scores} if scores else {}
        except Exception:
            _novelty = {}

        # Emotional intensity x novelty blended scoring
        scored = []
        for m in memories:
            pp = abs(float(m.get("pad_p", 0)))
            pa = abs(float(m.get("pad_a", 0)))
            intensity = pp + pa
            # When no PAD data (intensity=0), use weight as fallback signal
            if intensity == 0:
                intensity = float(m.get("weight", 0.5))
            # AIS novelty boost (0..1)
            mid = m.get("id", 0)
            novelty = _novelty.get(mid, 0.5)
            blended = intensity * 0.7 + novelty * 0.3
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
        return "memory: " + " | ".join(parts)
    except Exception:
        return ""


# ─── Cloud LLM call ───


async def cloud_chat(
    text: str,
    *,
    history: Optional[list[dict]] = None,
    session_id: str = "",
    max_tokens: int = 200,
    temperature: float = 0.7,
    on_delta: Optional[Callable[[str], Any]] = None,
) -> str:
    """Call cloud LLM (DeepSeek priority -> minimax fallback), with soul + cogno 5D + memory injection.

    Flow:
      1. Search v4.db for related memories -> build system prompt
      2. Call cloud LLM for reply
      3. Self-review: evaluate if reply is beneficial to body architecture, rewrite if low score
      4. Consolidation: refine conversation into facts written to v4.db
      5. Return final reply

    Args:
        text: User input
        history: History messages [{role: user|assistant, content: str}, ...]
        session_id: Session ID (for log tracking)
        max_tokens: Max generation tokens
        temperature: Sampling temperature

    Returns:
        Assistant reply text
    """
    env_map = _load_env()

    # ── Ikaros backend selection (configured via Studio page or ekko chain, via v5-agent/manager.ts) ──
    # provider:
    #   "local"    direct local :8080 (configurable base_url/model), never cloud
    #   "deepseek" direct OpenAI-compatible (configurable base_url/api_key/model) — any DeepSeek endpoint
    #   "openai"   reuse ekko model chain injected remote endpoint (glm / openai / custom provider, etc.)
    #   "dashboard" (default) legacy Dashboard WebSocket -> DeepSeek path, local fallback on failure
    backend_provider = (os.environ.get("IKAROS_BACKEND_PROVIDER") or "dashboard").strip().lower()
    backend_base_url = (os.environ.get("IKAROS_BACKEND_BASE_URL") or "").strip()
    backend_api_key = os.environ.get("IKAROS_BACKEND_API_KEY") or ""
    backend_model = (os.environ.get("IKAROS_BACKEND_MODEL") or "").strip()

    # V5: User's input updates Ikaros emotion state (PAD)
    try:
        import sys as _sys2
        _v5p = str(Path(__file__).resolve().parent.parent / "Ikaros-memory")
        if _v5p not in _sys2.path:
            _sys2.path.insert(0, _v5p)
        from v5.affect import apply_event, AffectState
        old_state = AffectState.load()  # V5 #1: record old PAD for causality detection
        apply_event(text)
        new_state = AffectState.load()
    except Exception:
        old_state = None
        new_state = None

    # V5 #1: Emotion causality memory — auto-record causal chain when PAD changes significantly
    try:
        from v5.emotional_memory import maybe_record_emotion
        if old_state is not None and new_state is not None:
            old_pad = (old_state.pleasure, old_state.arousal, old_state.dominance)
            new_pad = (new_state.pleasure, new_state.arousal, new_state.dominance)
            maybe_record_emotion(old_pad, new_pad, text)
    except Exception:
        pass

    # V5.2 R6: Emotion label auto-tagging (fire-and-forget, local 1.7b, non-blocking)
    try:
        import threading as _th
        from v5.emotional_memory import maybe_label_emotion
        if old_state is not None and new_state is not None:
            _op = (old_state.pleasure, old_state.arousal, old_state.dominance)
            _np = (new_state.pleasure, new_state.arousal, new_state.dominance)
            def _bg_label():
                try:
                    maybe_label_emotion(_op, _np, text)
                except Exception:
                    pass
            _th.Thread(target=_bg_label, daemon=True).start()
    except Exception:
        pass

    # V5 #2: Relationship intimacy update
    try:
        from v5.relationship import Relationship
        rel = Relationship.load()
        intensity = (abs(new_state.pleasure) + abs(new_state.arousal) + abs(new_state.dominance)*0.5)/2.0 if new_state else 0.3
        rel = rel.record_interaction(intensity)
        rel.save()
    except Exception:
        pass

    # V5 #8: Vitality state update
    try:
        from v5.vitality import Vitality
        v = Vitality.load()
        v = v.tick(conversation=True)
        v.save()
    except Exception:
        pass

    # V5 metacog: conversation happens -> curiosity drops (interrupted)
    try:
        import v5.metacog as _mc
        _mc.mark_interaction()
    except Exception:
        pass

    # V5: Linear Lorenz drift + ECA topic evolution during conversation (~38us, free)
    # V5: Linear Lorenz drift + ECA topic evolution during conversation (~38us, free)
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

    # V5 Memory: "remember/remind me/don't let me forget..." -> proactive todo timer,
    # triggered by proactive scheduler to bring up on its own. Short-circuit on hit (saves one LLM call).
    try:
        from v5.proactive import (get_scheduler as _get_sched,
                                   parse_remember_intent as _parse_rem,
                                   fmt_due as _fmt_due)
        _rem = _parse_rem(text)
        if _rem:
            _get_sched().remember_todo(_rem["text"], due_ts=_rem["due_ts"],
                                       kind=_rem["kind"])
            _when = _fmt_due(_rem["due_ts"])
            if _when == "找机会":
                _ack = f"好的哥哥，我记住了——「{_rem['text']}」，我会找机会提醒你的。"
            else:
                _ack = f"好的哥哥，记住啦——{_when}我会提醒你「{_rem['text']}」。"
            if on_delta:
                try:
                    await on_delta(_ack)
                except Exception:
                    pass
            log.info("remember-intent: kind=%s due=%s text=%r",
                     _rem["kind"], _when, _rem["text"])
            return _ack
    except Exception as _e:
        log.debug("remember-intent hook skipped: %s", _e)

    # V5 Router: classify input, optimize task instructions with local LLM
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

    # V5 Task Runner: check pending delivery results
    try:
        from v5.task_runner import check_result, check_pending_reminder
        from v5.task_runner import consume_result, consume_reminder, set_reminder

        _pending_result = check_result()
        _pending_reminder = check_pending_reminder()
    except Exception:
        _pending_result = None
        _pending_reminder = None

    # ─── Task branch: background execution, return immediately ───
    if _is_task:
        try:
            from v5.task_runner import call_async
            call_async(text, optimized=_optimized)
        except Exception:
            pass
        return "好的哥哥，这个任务我已经在后台处理了，完成后会告诉你结果。"

    # ─── Result delivery: user free / busy ───
    if _pending_result:
        _user_reply_lower = text.strip().lower()
        if any(kw in _user_reply_lower for kw in ("有空", "好的", "说吧", "说", "听", "好呀", "嗯", "可以")):
            # User is free -> deliver result + clear reminder
            result = consume_result()
            consume_reminder()  # clear possible reminder flag
            if result and result.get("result"):
                reply = result["result"]
                log.info("task: delivered result (%d chars)", len(reply))
                return reply
        elif any(kw in _user_reply_lower for kw in ("没空", "等一下", "等等", "忙", "回头", "稍后", "之后再说")):
            # User is busy -> set reminder
            set_reminder({"text": text, "result_pending": True})
            log.info("task: user busy, reminder set")
            # Continue normal conversation (non-blocking)
        else:
            # Default: set reminder, proactively mention next time
            set_reminder({"text": text, "result_pending": True})
            log.info("task: ambiguous reply, reminder set")

    # ─── Reminder branch: previously busy, proactively mention next time ───
    if _pending_reminder:
        # Inject into system prompt
        _reminder_note = "\n(Reminder: there's a task result pending to tell user, find a chance to mention proactively.)"
    else:
        _reminder_note = ""

    # Build system prompt (soul + cogno + memory + V5 emotion + reminder)
    system_prompt = build_system_prompt(text, history=history) + _reminder_note

    # V5 Emotion -> Response length: short reply when PAD is low
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

    # Build messages
    msgs: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        # Token saving: keep last 30 messages (15 turns), compress older with summary
        if len(history) > 30:
            # Compress old history into one-sentence summary with local model
            _old = history[:-30]
            _recent = history[-30:]
            _summary_text = " ".join(
                m["content"][:60] for m in _old if m.get("content")
            )
            try:
                from v5.reflect.llm_client import call_llm as _cl
                _resp = _cl(
                    "Compress this conversation history into a single sentence summary within 20 characters, output summary only",
                    _summary_text, provider="local", max_tokens=60, timeout=30)
                if _resp and _resp.content and len(_resp.content) > 3:
                    msgs.append({"role": "system", "content": f"Conversation summary: {_resp.content[:60].strip()}"})
            except Exception:
                pass  # summary failure does not block main flow
            history = _recent
        msgs.extend(history)
    # If task instruction was optimized, use it instead of raw user input
    user_content = _optimized if _optimized else text
    msgs.append({"role": "user", "content": user_content})

    # Monitor: user input
    _push_monitor("user_msg", text=text[:200], session_id=session_id)

    _is_task = _optimized is not None
    deepseek_key = _get_api_key(env_map, "DEEPSEEK_API_KEY")
    minimax_key = _get_api_key(env_map, "MINIMAX_CN_API_KEY")
    reply: str | None = None
    errors: list[str] = []

    _instruction = f"{system_prompt}\n\n--- 用户消息 ---\n{user_content[:400]}"
    if len(_instruction) > 6000:
        _instruction = _instruction[:6000]

    if backend_provider == "local":
        # Direct to local LLM, skip Dashboard/cloud (never triggers DeepSeek 429)
        try:
            reply = await _call_local_llm(
                msgs,
                max_tokens=max_tokens,
                temperature=temperature,
                on_delta=on_delta,
                base_url=backend_base_url or None,
                model=backend_model or None,
            )
        except Exception as e:
            log.warning("local backend failed: %s", e)
            errors.append(f"local: {e}")
            reply = None

    elif backend_provider in ("deepseek", "openai"):
        # Direct OpenAI-compatible (DeepSeek / GLM / OpenAI / any compatible endpoint),
        # using ekko chain or page-configured base_url/key/model
        try:
            reply = await _call_openai_compatible(
                base_url=backend_base_url or "https://api.deepseek.com/v1",
                api_key=backend_api_key,
                model=backend_model or "deepseek-chat",
                messages=msgs,
                max_tokens=max_tokens,
                temperature=temperature,
                label=f"{backend_provider}-backend",
                on_delta=on_delta,
            )
        except Exception as e:
            log.warning("%s backend failed: %s", backend_provider, e)
            errors.append(f"{backend_provider}: {e}")
            reply = None

    else:
        # ── Hermes Dashboard via hermes_client (unified WS path) ──
        try:
            from v5.hermes_client import chat as _hermes_chat
            session_name = "Ikaros-task" if _is_task else "Ikaros"
            loop = asyncio.get_event_loop()
            reply = await loop.run_in_executor(
                None, _hermes_chat, session_name, _instruction,
            )
            if not reply or (isinstance(reply, str) and reply.startswith("(Hermes")):
                raise RuntimeError(f"Hermes returned error: {reply}")
            log.info("%s (%s): %d chars", "task" if _is_task else "chat", session_name, len(reply or ""))

        except Exception as e:
            log.warning("dashboard WS failed: %s", e)
            errors.append(f"dash-ws: {e}")
            reply = None

    if reply is None:
        if backend_provider == "dashboard":
            # Local fallback: when legacy dashboard path has DeepSeek billing down/network issue, use local :8080
            try:
                local_reply = await _call_local_llm(
                    msgs, max_tokens=max_tokens, temperature=temperature
                )
                if local_reply and local_reply.strip():
                    log.info("main reply fell back to local :8080 LLM (%d chars)", len(local_reply))
                    return local_reply.strip()
            except Exception as e:
                log.warning("local :8080 fallback for main reply failed: %s", e)
        err_msg = "; ".join(errors) if errors else "No reply received"
        raise RuntimeError(f"All providers failed: {err_msg}")

    # ── Step 3: Self review (fire-and-forget, non-blocking) ──
    # First phase: quick review only, rewrite in background if needed
    _review_in_progress = False
    try:
        review = await _self_review(text, reply, deepseek_key, minimax_key)
        if review.get("verdict") == "rewrite" and review.get("suggestion"):
            log.info("self-review: score=%d, will rewrite async", review.get("score", 0))
            _review_in_progress = True
    except Exception as e:
        log.warning("self-review failed (skipping): %s", e)

    # ── Step 4: Memory consolidation + conversation recording (fire-and-forget, non-blocking) ──
    _record_conversation(text, reply)
    # Execute in background thread, don't block main reply flow
    import threading
    def _background_consolidate():
        import asyncio as _bg_asyncio
        try:
            _bg_asyncio.run(_consolidate_to_memory(text, reply))
        except Exception:
            pass
    threading.Thread(target=_background_consolidate, daemon=True).start()

    # If rewrite is needed, also run in background thread (optimized reply for next turn)
    if _review_in_progress:
        def _background_rewrite():
            suggest = review.get("suggestion", "")
            rewrite_msgs = [
                {"role": "system", "content":
                 "You are Ikaros, an artificial angel. Your previous reply needs improvement. "
                 "Please rewrite based on feedback, keep original intent, better align with body architecture. "
                 f"Feedback: {suggest}"},
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

    # Monitor: assistant reply
    if reply:
        _push_monitor("assistant_msg", text=reply[:500], session_id=session_id)

    # R7 Clock Out: lightweight state snapshot (fire-and-forget, don't await, don't block reply)
    try:
        import asyncio as _asyncio
        _asyncio.create_task(_clock_out(text, reply if reply else ""))
    except Exception:
        pass

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
    """Call OpenAI-compatible API (DeepSeek / minimax / etc).

    Streaming: when on_delta is set, enables SSE (stream=True), calls on_delta(chunk)
    for each content chunk and accumulates, returns full text. Without on_delta returns
    full response at once (original behavior), preserving non-streaming paths like
    self-review / consolidate.
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
            # ── Streaming: first token on screen (equivalent to N.E.K.O gemini_response) ──
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
        # ── Non-streaming (original behavior) ──
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]
            log.info("%s OK (input=%d msgs, output=%d chars)", label, len(messages), len(reply))
            return reply
    except ImportError:
        # fallback: urllib (sync, non-streaming)
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
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> str | None:
    """Call local LLM (:8080/v1/chat/completions).

    Used for self-review / consolidate, non-blocking for main conversation flow.
    Returns None on connection failure / timeout / model not loaded; caller falls back.

    Streaming: when on_delta is set, enables SSE, calls on_delta during generation
    (first token on screen). Only streams content (avoids <think> reasoning in bubble);
    thinking mode with empty content falls back to non-streaming reading reasoning_content.

    2026-07-04 fixes:
    - Port changed from :8589 to :8080 (watchdog-managed LLM)
    - Qwen3 thinking mode: content may be empty (tokens consumed by thinking),
      fall back to reading reasoning_content
    - max_tokens default raised from 300 to 512 (room for thinking + answer)
    - timeout raised from 15s to 30s (thinking mode is slower)
    """
    _base = (base_url or _LOCAL_LLM_URL).rstrip('/')
    url = f"{_base}/chat/completions"
    body = {
        "model": model or "local-llm",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Content-Type": "application/json"}
    try:
        import httpx
        if on_delta is not None:
            # ── Streaming: first token on screen ──
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
                        # Only stream content (avoid <think> reasoning in bubble)
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
            # Thinking mode content empty -> non-streaming fallback to reasoning_content
            log.info("local LLM: stream content empty (thinking mode), fallback to reasoning")
        # ── Non-streaming (original behavior / thinking fallback) ──
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                log.warning("local LLM returned %d", resp.status_code)
                return None
            data = resp.json()
            msg = data["choices"][0]["message"]
            reply = msg.get("content", "") or ""
            # Qwen3 thinking mode: content may be empty, fallback to reasoning_content
            if not reply.strip():
                reasoning = msg.get("reasoning_content", "") or ""
                if reasoning.strip():
                    log.info("local LLM: content empty, using reasoning_content (%d chars)", len(reasoning))
                    reply = reasoning
            log.info("local LLM OK (%d chars)", len(reply))
            return reply if reply.strip() else None
    except ImportError:
        # urllib fallback (sync, wrapped in thread)
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


# ─── Self review ───

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
    """Evaluate candidate reply. Priority: Hermes Dashboard WS, fallback direct DeepSeek.

    Returns {score, verdict, issues, suggestion}.
    """
    prompt = f"用户说: {user_msg}\n\n候选回复: {candidate}\n\n请评估。"

    # 1) Hermes Dashboard via hermes_client (unified WS path)
    text = None
    try:
        instruction = f"{_SELF_REVIEW_SYSTEM}\n\n--- 用户消息 ---\n{prompt}"
        if len(instruction) > 6000:
            instruction = instruction[:6000]
        from v5.hermes_client import reflect as _hermes_reflect
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, _hermes_reflect, instruction)
        if text and text.startswith("(Hermes"):
            text = None
    except Exception:
        pass

    # 2) fallback: direct DeepSeek
    if not text and deepseek_key:
        try:
            text = await _call_openai_compatible(
                base_url="https://api.deepseek.com/v1",
                api_key=deepseek_key,
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": _SELF_REVIEW_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
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

    # 2) local LLM (:8080 fallback, offline/no-key last resort)
    if not text:
        text = await _call_local_llm(msgs, max_tokens=512, temperature=0.1)

    if not text:
        return {"score": 7, "verdict": "accept", "issues": [], "suggestion": ""}
    # Strip markdown code blocks
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


# ─── Memory consolidation ───

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
) -> bool:
    """Consolidate conversation into one fact in v4.db (local :8080 only, saves token).

    Writes type=fact, tags=consolidated, initial weight=0.6.
    """
    prompt = f"User said: {user_msg}\n\nAssistant replied: {assistant_msg}"
    msgs = [
        {"role": "system", "content": _CONSOLIDATE_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    # Local :8080 only (saves token, bypasses Hermes/cloud)
    log.info("consolidate: local :8080 only")
    fact = await _call_local_llm(msgs, max_tokens=512, temperature=0.0)

    if not fact or len(fact.strip()) < 5:
        log.warning("consolidate: LLM returned empty/too-short fact (cloud+local all failed)")
        return False
    fact = fact.strip().rstrip(".")
    # Write to v4.db (delegated to v4.store, not inline SQL)
    v4_store = _get_v4_store()
    if v4_store is not None:
        try:
            v4_store.store(content=fact[:300], type="fact", weight=0.6, tags="consolidated,cloud_chat")
            log.info("consolidated fact to v4.db: %.80s", fact)
            # V5 #5: Cognitive dissonance detection — check new fact against old memories
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


# ─── Sync wrapper (for audio_engine and other non-async contexts) ───


def cloud_chat_sync(
    text: str,
    *,
    history: Optional[list[dict]] = None,
    session_id: str = "",
    max_tokens: int = 200,
    temperature: float = 0.7,
) -> str:
    """Synchronous version of cloud_chat (runs asyncio event loop internally)."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # Event loop already exists -> run in new thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(
                asyncio.run,
                cloud_chat(text, history=history, session_id=session_id,
                          max_tokens=max_tokens, temperature=temperature)
            )
            return future.result(timeout=60)
    except RuntimeError:
        # No event loop
        return asyncio.run(
            cloud_chat(text, history=history, session_id=session_id,
                      max_tokens=max_tokens, temperature=temperature)
        )


# ─── Metacog / Distill lightweight Hermes sync call ───
# Delegated to hermes_client (no V5 affect/relationship/routing pipeline).
# Used by metacog reflection / self-review background tasks.


def hermes_prompt_sync(
    system_text: str,
    user_text: str,
    *,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    timeout: int = 90,
) -> str:
    """Synchronous Hermes prompt (lightweight, no V5 pipeline).

    Delegated to hermes_client.chat() for unified WS connection management.
    max_tokens/temperature are passed through for the Hermes backend.
    Raises RuntimeError on failure.
    """
    from v5.hermes_client import chat as _hermes_chat
    instruction = f"{system_text}\n\n--- User message ---\n{user_text}"
    if len(instruction) > 6000:
        instruction = instruction[:6000]
    reply = _hermes_chat("Ikaros-metacog", instruction, timeout=timeout)
    if reply.startswith("(Hermes"):
        raise RuntimeError(reply)
    return reply


# ─── Quick test ───
if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    reply = asyncio.run(cloud_chat("Hi, Ikaros"))
    print(f"\nReply: {reply}")
