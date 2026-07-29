"""Semantic rule retrieval for Ikaros agent operation rules.

Replaces the old :8080 LLM-based rule selection (cloud_chat._select_relevant_rules)
with :8587 embedding-based semantic retrieval. Given a query (user text / task
description / sub-agent spec), it returns the top-K most relevant rules from
``docs/agent-rules.yaml``, formatted for injection into the main model's prompt.

Design notes
------------
* Embeddings go through ``memory_v5.search._get_embedding`` (which already talks
  to :8587 and keeps an LRU cache), so no chromadb import is triggered at module
  load -- safe to import from cloud_chat / router / orchestrator.
* Graceful degradation: if :8587 is unavailable, it falls back to lexical
  (tag + text) matching so rules still surface without the embedding service.
* Never raises, never blocks for long: a per-rule TTL cache avoids re-attempting
  embeddings every call when the service is down, and a short global backoff
  stops query-embedding warning spam.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ikaros.memory.v5.rules_retriever")


# ── Rules source ───────────────────────────────────────────────
def _resolve_rules_path() -> Path:
    candidates: list[Path] = []
    hermes_root = os.environ.get("HERMES_ROOT")
    if hermes_root:
        candidates.append(Path(hermes_root) / "docs" / "agent-rules.yaml")
    candidates.append(Path(r"E:/Ikaros/docs/agent-rules.yaml"))
    # rules_retriever lives in core/memory_v5/ -> parents[2] == Ikaros root
    candidates.append(Path(__file__).resolve().parents[2] / "docs" / "agent-rules.yaml")
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]


_RULES_PATH = _resolve_rules_path()
_RULES_MTIME = -1.0
_RULES_CACHE: list[dict] = []


def _load_rules() -> list[dict]:
    """Load rule library (with mtime cache). Returns [] on any failure."""
    global _RULES_MTIME, _RULES_CACHE
    try:
        mtime = _RULES_PATH.stat().st_mtime
    except Exception:
        return _RULES_CACHE or []
    if _RULES_CACHE and mtime == _RULES_MTIME:
        return _RULES_CACHE
    try:
        try:
            import yaml
        except ImportError:
            logger.warning("rules_retriever: PyYAML missing, cannot load rules")
            return _RULES_CACHE or []
        with open(str(_RULES_PATH), encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _RULES_CACHE = data.get("rules") or []
        _RULES_MTIME = mtime
        return _RULES_CACHE
    except Exception as exc:
        logger.warning("rules_retriever: load failed: %s", exc)
        return _RULES_CACHE or []


# ── Embedding (reuse memory_v5.search, which talks to :8587) ──
def _embed(text: str, task: str) -> Optional[list]:
    try:
        from memory_v5.search import _get_embedding
        return _get_embedding(text, task=task)
    except Exception as exc:  # noqa: BLE001
        logger.debug("rules_retriever: embed unavailable: %s", exc)
        return None


_QUERY_FAIL_UNTIL = 0.0  # global backoff: skip query-embed attempts until this ts


def _embed_query(text: str) -> Optional[list]:
    global _QUERY_FAIL_UNTIL
    now = time.time()
    if now < _QUERY_FAIL_UNTIL:
        return None
    emb = _embed(text, task="query")
    if emb is None:
        _QUERY_FAIL_UNTIL = now + 30.0  # don't spam warnings for ~30s
    return emb


_RULE_EMB: dict[str, tuple[Optional[list], float]] = {}  # id -> (vec_or_None, ts)
_RULE_EMB_TTL = 120.0  # re-attempt a failed rule embedding after this many seconds


def _rule_embedding(rule: dict) -> Optional[list]:
    rid = str(rule.get("id") or rule.get("text", "") or id(rule))
    now = time.time()
    cached = _RULE_EMB.get(rid)
    if cached is not None:
        vec, ts = cached
        if vec is not None or (now - ts) < _RULE_EMB_TTL:
            return vec
    vec = _embed(rule.get("text", ""), task="document")
    _RULE_EMB[rid] = (vec, now)
    return vec


def _cosine(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na ** 0.5 * nb ** 0.5)


# ── Lexical fallback (used when :8587 is unavailable) ─────────
# CJK uses character bigrams (not unigrams) to avoid false positives from
# ultra-common single characters like 你/好/我. ASCII keeps word-level tokens.
_CJK_RUN_RE = re.compile(r"[一-鿿]+")


def _terms(text: str) -> set:
    terms: set = set()
    for m in re.finditer(r"[a-z0-9]+", (text or "").lower()):
        terms.add(m.group(0))
    for run in _CJK_RUN_RE.findall(text or ""):
        if len(run) == 1:
            terms.add(run)
        else:
            for i in range(len(run) - 1):
                terms.add(run[i:i + 2])
    return terms


def _lexical_top(query: str, rules: list[dict], k: int) -> list[dict]:
    qterms = _terms(query)
    if not qterms:
        return []
    scored = []
    for r in rules:
        tags = set(r.get("tags", []) or [])
        blob = (r.get("text", "") + " " + " ".join(tags))
        rterms = _terms(blob)
        if not rterms:
            continue
        score = sum(3 if t in tags else 1 for t in qterms if t in rterms)
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:k]]


# ── Public API ─────────────────────────────────────────────────
# Small-talk / greeting phrases that need no operation rules injected.
_GREETING_SET = {
    "你好", "您好", "hi", "hello", "hey", "在吗", "在么", "嗨", "哈喽",
    "嗯", "嗯嗯", "哦", "哦哦", "好", "好的", "行", "行吧", "是", "对", "ok",
    "谢谢", "感谢", "收到", "继续", "然后", "好的呢",
}
# Semantic cosine threshold: rules scoring below this are NOT injected.
_DEFAULT_MIN_SCORE = 0.30


def _format(rules: list[dict]) -> str:
    if not rules:
        return ""
    lines = ["## Agent 操作规则(按当前上下文语义检索)"]
    for r in rules:
        rid = r.get("id", "")
        text = (r.get("text", "") or "").strip()
        lines.append(f"- {rid}: {text}")
    return "\n".join(lines)


def retrieve_relevant_rules(query: str, k: int = 3, min_score: float = _DEFAULT_MIN_SCORE) -> str:
    """Return top-K rules relevant to ``query`` as an injectable prompt block.

    Uses :8587 embeddings when available (with a cosine relevance threshold so
    unrelated queries inject nothing); otherwise lexical fallback. Returns ""
    when no rule clears the threshold (never raises, never blocks long).
    """
    rules = _load_rules()
    if not rules:
        return ""
    raw = (query or "").strip()
    if not raw:
        return ""
    # Greeting / chit-chat short-circuit: small talk needs no operation rules.
    if raw.lower() in _GREETING_SET:
        return ""

    q_emb = _embed_query(raw)
    if q_emb is not None:
        # :8587 online -> trust semantic retrieval with threshold. If nothing
        # clears the bar, return "" (do NOT fall back to lexical, that would
        # re-introduce noise for clearly-unrelated queries).
        scored = []
        for r in rules:
            r_emb = _rule_embedding(r)
            if r_emb is None:
                continue
            s = _cosine(q_emb, r_emb)
            if s >= min_score:
                scored.append((s, r))
        if scored:
            scored.sort(key=lambda x: -x[0])
            return _format([r for _, r in scored[:k]])
        return ""

    # :8587 unavailable -> lexical fallback (already filters score>0).
    top = _lexical_top(raw, rules, k)
    return _format(top)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    import sys
    q = " ".join(sys.argv[1:]) or "帮我写个 Python 脚本把文件分类"
    print(retrieve_relevant_rules(q))
