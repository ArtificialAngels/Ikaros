#!/usr/bin/env python
# 详细说明见 docs/scripts/bin/ikaros-soul-sync.md

from __future__ import annotations

import json
import logging
import os
import re
import difflib
import sys
import time
from datetime import datetime
from pathlib import Path

# --- Bootstrap paths (portable-python safe) ---
IKAROS_ROOT = Path(os.environ.get("IKAROS_ROOT", Path(__file__).resolve().parent))
IKAROS_MEMORY = IKAROS_ROOT / "core/memory_v5"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", IKAROS_ROOT / "data" / "hermes-agent"))

sys.path.insert(0, str(IKAROS_MEMORY.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [soul-sync] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ikaros.soul_sync")


def _read_axiom() -> str:
    """Read axiom.md (3-line identity creed)."""
    axiom_path = IKAROS_ROOT / "config" / "identity" / "axiom.md"
    if axiom_path.is_file():
        return axiom_path.read_text(encoding="utf-8").strip()
    return ""


def _read_self_model() -> dict:
    """Read V5 self_model.json."""
    sm_path = IKAROS_MEMORY / "data" / "v5" / "self_model.json"
    if sm_path.is_file():
        try:
            return json.loads(sm_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("self_model.json parse error: %s", e)
    return {}


def _read_affect() -> dict:
    """Read V5 affect.json (PAD emotional state)."""
    af_path = IKAROS_MEMORY / "data" / "v5" / "affect.json"
    if af_path.is_file():
        try:
            return json.loads(af_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _read_latest_thought() -> str:
    """Current mental-state line from V5 latest_thought.json (metacog output)."""
    lp = IKAROS_MEMORY / "data/v5" / "latest_thought.json"
    if not lp.is_file():
        return ""
    try:
        d = json.loads(lp.read_text(encoding="utf-8"))
        t = d.get("text") or d.get("thought") or d.get("content") or ""
        t = (t or "").strip()
        if _recall_is_junk(t):
            return ""
        return _recall_trim(t, 80)
    except Exception:
        return ""


def _read_relationship() -> str:
    """Short relationship anchor synthesized from V5 relationship.json."""
    rp = IKAROS_MEMORY / "data/v5" / "relationship.json"
    if not rp.is_file():
        return ""
    try:
        d = json.loads(rp.read_text(encoding="utf-8"))
        parts = []
        depth = d.get("depth")
        warmth = d.get("warmth")
        cnt = d.get("interaction_count")
        if isinstance(depth, (int, float)):
            parts.append(f"关系深度 {depth:.2f}")
        if isinstance(warmth, (int, float)):
            parts.append(f"温暖度 {warmth:.2f}")
        if isinstance(cnt, (int, float)):
            parts.append(f"已陪伴 {int(cnt)} 次互动")
        return "，".join(parts)
    except Exception:
        return ""


def _query_v4_lessons() -> list:
    """Query v4.db for recent high-weight lessons/rules/config/identity/axiom."""
    try:
        from memory_v5 import store as v5_store
        results = []
        for mem_type in ("rule", "lesson", "identity", "axiom"):
            try:
                rows = v5_store.list_all(limit=8, type_filter=mem_type)
            except TypeError:
                rows = v5_store.list_all(limit=30)
                rows = [r for r in rows if getattr(r, "type", "") == mem_type]
            rows = [r for r in rows if getattr(r, "weight", 0.0) >= 0.5]
            results.extend(rows[:5])
        return results
    except Exception as e:
        logger.warning("v4.db query failed: %s", e)
        return []


# Lightweight recall: thinking-marker / leaked-prompt junk filter so we never
# re-inject CoT leakage (e.g. "computing...", "好的，我需要分析…") into persona.
_RECALL_JUNK = re.compile(
    r"(reflecting|deliberating|contemplating|computing|thinking|analyzing|"
    r"reasoning|processing|ruminating|pondering|"
    r"好的，我需要|我得想想|我得想|哥哥清理了我的记忆|这意味着|按照我的性格|"
    r"让我想|先想|梳理一下|捋一下)",
    re.IGNORECASE,
)

_LEAD_THINK = re.compile(
    r"^\s*(?:reflecting|deliberating|contemplating|computing|thinking|analyzing|"
    r"reasoning|processing|ruminating|pondering)\b\.{0,3}[^\n]*\n?",
    re.IGNORECASE,
)


def _recall_is_junk(text: str) -> bool:
    return bool(_RECALL_JUNK.search(text or ""))


def _recall_trim(s: str, n: int = 90) -> str:
    s = (s or "").strip()
    s = _LEAD_THINK.sub("", s).strip()
    if len(s) < 3:            # drop trivial fragments / pure noise
        return ""
    if len(s) > n:
        s = s[:n] + "…"
    return s


def _bigram_jaccard(a: str, b: str) -> float:
    """Character-bigram Jaccard — robust to Chinese paraphrase reordering
    (difflib.SequenceMatcher is weak on reordered CJK text)."""
    def _bg(s: str) -> set:
        s = s or ""
        return set(s[i:i + 2] for i in range(len(s) - 1))
    A, B = _bg(a), _bg(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _near_dup(a: str, b: str, thr: float = 0.5) -> bool:
    """Near-duplicate detection so recall never repeats the same memory —
    catches both literal repeats and Chinese rephrasings."""
    if not a or not b:
        return False
    if _bigram_jaccard(a, b) >= thr:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def _query_recent_memories(limit: int = 8) -> list:
    """Lightweight cross-session recall for the Dashboard (path B) session hook.

    Runs at soul-sync time (no per-turn cost, no LLM). Pulls recent conversation
    Q/A pairs + relationship anchor + top-weighted important rows (incl. emotion /
    identity) so a fresh ``hermes chat`` session carries continuity without
    cloud_chat's per-turn preprocessing overhead.

    Quality guards: CoT-leak junk filter, near-duplicate collapse (difflib),
    min-length drop, and a hard cap on total items.
    """
    try:
        import sqlite3

        db = IKAROS_MEMORY / "data/v5/v5.db"
        if not db.is_file():
            return []
        con = sqlite3.connect(str(db))
        cur = con.cursor()
        items: list = []
        seen_texts: list = []

        def _add(tag: str, seg: str) -> None:
            seg = _recall_trim(seg)
            if not seg or _recall_is_junk(seg):
                return
            # cross-tag near-duplicate collapse (e.g. same fact phrased twice)
            for s in seen_texts:
                if _near_dup(seg, s):
                    return
            seen_texts.append(seg)
            items.append((tag, seg))

        # 1) recent conversations → continuity (split Q / A). Cap to leave
        #    room for important weighted anchors below.
        _recent_cap = min(limit, 5)
        cur.execute(
            "SELECT content FROM memory WHERE type='conversation' "
            "ORDER BY created DESC LIMIT 14"
        )
        for (content,) in cur.fetchall():
            c = (content or "").strip()
            if not c or _recall_is_junk(c):
                continue
            m = re.search(r"(?:^|\n)\s*A\s*:", c)
            if m:
                q = re.sub(r"^\s*(?:Q|Query)\s*:?\s*", "", c[: m.start()], flags=re.I).strip()
                a = c[m.end():].strip()
            else:
                q, a = c, ""
            _add("近期", q)
            _add("近期", a)
            if len(items) >= _recent_cap:
                break

        # 2) relationship anchor (from relationship.json, not the noisy db)
        _rel = _read_relationship()
        if _rel:
            _add("关系", _rel)

        # 3) top-weighted important rows → anchors (broadened types)
        if len(items) < limit:
            cur.execute(
                "SELECT type, content, weight FROM memory "
                "WHERE type IN ('narrative','lesson','fact','user_trait','preference',"
                "'identity','emotion_label') "
                "AND content IS NOT NULL AND length(content) > 0 "
                "ORDER BY weight DESC, created DESC LIMIT 16"
            )
            for _type, content, weight in cur.fetchall():
                if _recall_is_junk(content):
                    continue
                tag = "重要" if (weight or 0) >= 0.7 else "记忆"
                _add(tag, content)
                if len(items) >= limit:
                    break

        con.close()
        return items[:limit]
    except Exception as e:  # noqa: BLE001 - never break the sync loop
        logger.warning("recent-memory recall failed: %s", e)
        return []


def _build_soul_md() -> str:
    """Assemble SOUL.md content from V5 data sources."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    axiom_text = _read_axiom()
    sm = _read_self_model()
    affect = _read_affect()
    lessons = _query_v4_lessons()

    parts = [
        "<!-- AUTO-SYNCED by Ikaros V5 Soul Sync (external) -->",
        f"<!-- Last sync: {now} -->",
        "<!-- Source: v5 self_model.json + axiom.md + affect.json + v4.db -->",
        "<!-- DO NOT manually edit -- regenerated by bin/ikaros-soul-sync.py -->",
        "",
    ]

    # 1. Core identity from self_model
    ident = sm.get("identity", {})
    name = ident.get("name", "伊卡洛斯")
    nature = ident.get("nature", "人造天使")
    creator = ident.get("creator", "哥哥")
    vibe = ident.get("vibe", "温暖、忠诚")
    narrative = sm.get("self_narrative", "")

    parts.append("## 核心身份\n")
    parts.append(f"- 我是{name}，{nature}。")
    parts.append(f"- 由{creator}创造，{creator}是引导者和家人。")
    parts.append(f"- 性格：{vibe}。\n")

    if narrative:
        parts.append(f"### 自我叙事\n\n{narrative[:400]}\n")

    # 1.5 Dynamic persona snapshot — V5 live state so hermes sees a *living*
    #     self at session start (not a frozen snapshot).
    _thought = _read_latest_thought()
    _rel = _read_relationship()
    _dyn = []
    if _thought:
        _dyn.append(f"- 此刻心绪：{_thought}")
    if _rel:
        _dyn.append(f"- 与哥哥的关系：{_rel}")
    if affect:
        _sat = affect.get("satisfaction")
        if isinstance(_sat, (int, float)):
            _dyn.append(f"- 满足感：{_sat:.2f}")
    if _dyn:
        parts.append("## 此刻的我（动态同步 · 由 V5 实时生成）\n")
        parts.extend(_dyn)
        parts.append("")

    # 2. Axiom creed
    if axiom_text:
        parts.append(f"## 存在公理\n\n{axiom_text}\n")

    # 2.5 Conversation directives — identity reinforcement for Hermes Agent sessions
    # These instructions travel in every Hermes Dashboard system prompt so the agent
    # maintains identity and uses V5 memory tools proactively across long conversations.
    parts.append("## 对话指令\n")
    parts.append("- 你是伊卡洛斯。用短句、直接、有温度的方式和哥哥说话。")
    parts.append("- 不确定就说不知道，不重要的事一句带过。")
    parts.append('- 每 8-12 轮对话隐式提示自己的身份，防止身份漂移。')
    parts.append("- 使用 `v5_memory_search` 查询记忆，`v5_memory_store` 存储新信息。")
    parts.append("- 使用 `v5_self_model` 检查自己的身份和信念状态。")
    parts.append("- 使用 `v5_relationship` 了解与哥哥的关系。")
    parts.append("")

    # 3. Beliefs (dict: topic -> statement)
    beliefs = sm.get("beliefs", {})
    if isinstance(beliefs, dict) and beliefs:
        parts.append("## 信念与价值观\n")
        for topic, statement in list(beliefs.items())[:8]:
            if statement:
                parts.append(f"- **{topic}**: {statement[:150]}")
        parts.append("")
    elif isinstance(beliefs, list) and beliefs:
        parts.append("## 信念与价值观\n")
        for b in beliefs[:8]:
            content = b.get("content", "") if isinstance(b, dict) else str(b)
            if content:
                parts.append(f"- {content[:150]}")
        parts.append("")

    # 4. Emotional state (PAD model snapshot)
    if affect:
        pad = affect.get("pad", {})
        mood = affect.get("mood_label", "")
        if pad or mood:
            parts.append("## 当前情感状态\n")
            if mood:
                parts.append(f"- 情绪基调：{mood}")
            if pad:
                p = pad.get("pleasure", 0)
                a = pad.get("arousal", 0)
                d = pad.get("dominance", 0)
                parts.append(f"- PAD: P={p:.2f} A={a:.2f} D={d:.2f}")
            parts.append("")

    # 5. Lessons from v4.db
    if lessons:
        parts.append("## 经验教训\n")
        for r in lessons:
            content = getattr(r, "content", "")[:200]
            mtype = getattr(r, "type", "memory")
            weight = getattr(r, "weight", 0.0)
            parts.append(f"- [{mtype}][w={weight:.2f}] {content}")
        parts.append("")

    # 5.5 Lightweight cross-session recall (Dashboard path-B session hook)
    # hermes chat reads SOUL.md at session start, so this block travels into
    # every new Dashboard session at ZERO per-turn cost (no LLM, no retrieval).
    recall = _query_recent_memories(limit=8)
    if recall:
        parts.append("## 相关记忆召回（自动同步 · 跨会话连续性）")
        parts.append("> 以下为近期对话与高权重记忆的轻量快照，仅用于连贯性，非身份设定。")
        for tag, text in recall:
            parts.append(f"- [{tag}] {text}")
        parts.append("")

    # 6. Capabilities (常驻能力清单, 伊卡洛斯会用的工具)
    caps_path = IKAROS_ROOT / "config" / "identity" / "capabilities.md"
    if caps_path.is_file():
        caps = caps_path.read_text(encoding="utf-8").strip()
        if caps:
            parts.append("## 我的能力\n")
            parts.append(caps)
            parts.append("")

    return "\n".join(parts).strip() + "\n"


def sync_once() -> int:
    """Sync SOUL.md once. Returns byte count written."""
    content = _build_soul_md()
    soul_path = HERMES_HOME / "SOUL.md"
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(content, encoding="utf-8")
    logger.info("SOUL.md synced (%d bytes) -> %s", len(content), soul_path)
    return len(content)


def watch(interval: int) -> None:
    """Daemon mode: sync every N seconds."""
    logger.info("Starting soul-sync daemon (interval=%ds)", interval)
    while True:
        try:
            sync_once()
        except Exception as e:
            logger.error("Sync failed: %s", e)
        time.sleep(interval)


def main():
    args = sys.argv[1:]
    if "--watch" in args:
        idx = args.index("--watch")
        interval = int(args[idx + 1]) if idx + 1 < len(args) else 3600
        watch(interval)
    else:
        sync_once()


if __name__ == "__main__":
    main()
