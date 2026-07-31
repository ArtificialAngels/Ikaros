#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V5 → N.E.K.O Persona Sync
==========================

Reads Ikaros V5's self_model, affect, relationship, and memories,
then writes them into neko's persona.json format — making the AI
identify as Ikaros without modifying any neko code.

Design:
  - Non-invasive: only writes `memory/{name}/persona.json`, neko reads it natively.
  - Idempotent: safe to run repeatedly (uses id prefixes to replace, not append).
  - Works with neko's existing memory_server :48912 at next /new_dialog call.
  - Zero changes to core/neko/ code.

Usage:
    python bin/v5-sync-persona.py [character_name]
    
    character_name defaults to "YUI" if omitted.
    Set V5_PERSONA_AUTO_SYNC=1 in env for automatic background sync.

Mapping:
    V5 self_model.identity       → neko persona["neko"]  (自我认知)
    V5 self_model.self_narrative → neko persona["neko"]  (叙事)
    V5 self_model.beliefs        → neko persona["neko"]  (信念)
    V5 affect                    → neko persona["neko"]  (情感状态)
    V5 relationship              → neko persona["relationship"] (关系)
    V5 latest_thought            → neko persona["neko"]  (内心活动)
    V5 store memories (top 5)    → neko persona["master"] (记忆摘要)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [v5-sync] %(levelname)s %(message)s",
)
logger = logging.getLogger("v5-sync-persona")

# ── Paths ────────────────────────────────────────────────────
_SCRIPT = Path(__file__).resolve()
_IKAROS_ROOT = _SCRIPT.parent.parent          # E:\Ikaros
_V5_ROOT = _IKAROS_ROOT / "core" / "v5"
_V5_DATA = _V5_ROOT / "data" / "v5"
_NEKO_MEMORY = _IKAROS_ROOT / "tmp" / "neko-state" / "memory"

# Default character name
CHARACTER = os.environ.get("V5_PERSONA_CHARACTER", "YUI")

# Prefix for auto-synced entries (neko's persona uses prefix-based IDs)
V5_SYNC_PREFIX = "v5sync_"

# ── Ensure V5 is importable ─────────────────────────────────
if str(_V5_ROOT) not in sys.path:
    sys.path.insert(0, str(_V5_ROOT))


def _read_json(path: Path) -> dict | None:
    """Read a JSON file, return None if missing or corrupt."""
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, Exception) as e:
        logger.debug("read_json failed for %s: %s", path.name, e)
        return None


def _neko_persona_path(character: str) -> Path:
    """Path to neko's persona.json for this character."""
    return _NEKO_MEMORY / character / "persona.json"


def _load_neko_persona(character: str) -> dict:
    """Load neko persona.json, creating a minimal skeleton if absent."""
    path = _neko_persona_path(character)
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8"))
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("corrupt persona.json for %s, rebuilding: %s", character, e)
    return {
        "master": {"facts": []},
        "neko": {"facts": []},
        "relationship": {"facts": []},
        "__meta__": {"facts": []},
    }


def _save_neko_persona(character: str, persona: dict) -> bool:
    """Atomically save persona.json."""
    path = _neko_persona_path(character)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps(persona, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
        logger.info("wrote persona.json for %s (%d entries)",
                     character, sum(len(v.get("facts", [])) for v in persona.values() if isinstance(v, dict)))
        return True
    except Exception as e:
        logger.error("save persona failed: %s", e)
        return False


def _remove_old_sync_entries(persona: dict, entity: str) -> None:
    """Remove all previously synced V5 entries from an entity."""
    facts = persona.get(entity, {}).get("facts", [])
    persona[entity]["facts"] = [
        f for f in facts
        if not f.get("id", "").startswith(V5_SYNC_PREFIX)
    ]


def _add_fact(persona: dict, entity: str, fact_id: str, text: str,
              source: str = "v5_sync") -> None:
    """Add a fact entry to persona."""
    if entity not in persona:
        persona[entity] = {"facts": []}
    persona[entity]["facts"].append({
        "id": f"{V5_SYNC_PREFIX}{fact_id}",
        "text": text,
        "source": source,
        "source_id": None,
        "recent_mentions": [],
        "suppress": False,
        "suppressed_at": None,
        "protected": True,
        "reinforcement": 0.0,
        "disputation": 0.0,
        "rein_last_signal_at": None,
        "disp_last_signal_at": None,
        "sub_zero_days": 0,
        "sub_zero_last_increment_date": None,
        "user_fact_reinforce_count": 0,
        "merged_from_ids": [],
        "token_count": None,
        "token_count_text_sha256": None,
        "token_count_tokenizer": None,
        "version_history": [],
        "embedding": None,
        "embedding_text_sha256": None,
        "embedding_model_id": None,
        "last_refine_cluster_hash": None,
        "last_refine_at": None,
    })


# ── Sync Functions ───────────────────────────────────────────

def sync_identity(character: str, persona: dict) -> int:
    """Sync V5 self_model.identity → persona["neko"]."""
    sm = _read_json(_V5_DATA / "self_model.json")
    if not sm:
        logger.warning("self_model.json not found, skip identity")
        return 0

    _remove_old_sync_entries(persona, "neko")
    identity = sm.get("identity", {})
    count = 0

    if name := identity.get("name"):
        _add_fact(persona, "neko", "name", f"我的名字是{name}", "v5_sync")
        count += 1
    if nature := identity.get("nature"):
        _add_fact(persona, "neko", "nature", f"我的本质：{nature}", "v5_sync")
        count += 1
    if creator := identity.get("creator"):
        _add_fact(persona, "neko", "creator", f"我的创造者是{creator}", "v5_sync")
        count += 1
    if vibe := identity.get("vibe"):
        _add_fact(persona, "neko", "vibe", f"我的气质：{vibe}", "v5_sync")
        count += 1

    # Self narrative
    if narrative := sm.get("self_narrative"):
        _add_fact(persona, "neko", "narrative",
                  f"我的自我叙事：{narrative[:300]}", "v5_sync")
        count += 1

    # Beliefs
    for theme, text in sm.get("beliefs", {}).items():
        if text:
            _add_fact(persona, "neko", f"belief_{theme}",
                      f"我对「{theme}」的理解：{text[:200]}", "v5_sync")
            count += 1

    # Architecture
    for arch in sm.get("architecture", []):
        name = arch.get("name", "")
        role = arch.get("role", "")
        if name:
            _add_fact(persona, "neko", f"arch_{name[:20]}",
                      f"我的组件：{name} — {role[:100]}", "v5_sync")
            count += 1

    # Latest thought
    lt = _read_json(_V5_DATA / "latest_thought.json")
    if lt and (thought := lt.get("text")):
        _add_fact(persona, "neko", "thought",
                  f"我最近在想：{thought[:200]}", "v5_sync")
        count += 1

    logger.info("synced %d identity entries", count)
    return count


def sync_affect(character: str, persona: dict) -> int:
    """Sync V5 affect → persona["neko"] emotional state description."""
    af = _read_json(_V5_DATA / "affect.json")
    if not af:
        logger.warning("affect.json not found, skip affect")
        return 0

    mood_labels = []
    p = af.get("pleasure", 0)
    a = af.get("arousal", 0)
    d = af.get("dominance", 0)
    t = af.get("trust", 0.5)
    l = af.get("loneliness", 0.1)
    s = af.get("satisfaction", 0.2)

    # PAD → labels
    if p > 0.3:
        mood_labels.append(f"愉悦({p:.2f})")
    elif p < -0.3:
        mood_labels.append(f"低落({p:.2f})")
    else:
        mood_labels.append(f"平静({p:.2f})")

    if a > 0.3:
        mood_labels.append(f"兴奋({a:.2f})")
    elif a < -0.3:
        mood_labels.append(f"疲惫({a:.2f})")

    if d > 0.2:
        mood_labels.append(f"自信({d:.2f})")
    elif d < -0.2:
        mood_labels.append(f"顺从({d:.2f})")

    # TLS → text
    trust_label = "信赖" if t > 0.3 else ("怀疑" if t < -0.3 else "中立")
    lonely_label = "感到孤独" if l > 0.2 else ("被陪伴" if l < -0.2 else "平静")
    satisfy_label = "满足" if s > 0.2 else ("不满足" if s < -0.2 else "尚可")

    affect_text = (
        f"当前情感状态——情绪：{' / '.join(mood_labels)}。"
        f"对哥哥：{trust_label}(信任度{t:.2f})。"
        f"内心：{lonely_label}(孤独感{l:.2f})，{satisfy_label}(满足感{s:.2f})。"
    )

    _add_fact(persona, "neko", "affect", affect_text, "v5_sync")
    logger.info("synced affect: %s", affect_text[:60])
    return 1


def sync_relationship(character: str, persona: dict) -> int:
    """Sync V5 relationship → persona["relationship"]."""
    rel = _read_json(_V5_DATA / "relationship.json")
    if not rel:
        logger.warning("relationship.json not found, skip relationship")
        return 0

    _remove_old_sync_entries(persona, "relationship")
    count = 0

    depth = rel.get("depth", 0)
    warmth = rel.get("warmth", 0)
    interaction_count = rel.get("interaction_count", 0)
    peak = rel.get("peak_closeness", 0)

    # Relationship stage
    closeness = depth * (0.5 + 0.5 * warmth)
    if closeness > 0.8:
        stage = "像家人一样亲密"
    elif closeness > 0.6:
        stage = "已经很亲近了"
    elif closeness > 0.4:
        stage = "还在了解彼此"
    else:
        stage = "才刚认识不久"

    _add_fact(persona, "relationship", "closeness",
              f"与哥哥的关系：{stage}（亲密度{closeness:.2f}）", "v5_sync")
    count += 1

    _add_fact(persona, "relationship", "interaction",
              f"累计互动 {interaction_count} 次，历史最高亲密度 {peak:.2f}", "v5_sync")
    count += 1

    _add_fact(persona, "relationship", "warmth",
              f"当前温暖度 {warmth:.2f}，关系深度 {depth:.2f}", "v5_sync")
    count += 1

    logger.info("synced %d relationship entries", count)
    return count


def sync_memories(character: str, persona: dict) -> int:
    """Sync top V5 memories → persona["master"] as context."""
    try:
        from v5.store import list_all
    except ImportError:
        logger.warning("V5 store not importable, skip memories")
        return 0

    _remove_old_sync_entries(persona, "master")
    count = 0

    try:
        memories = list_all(limit=5, type_filter="fact")
        for m in memories:
            if m.content and len(m.content) > 10:
                _add_fact(persona, "master", f"mem_{m.id}",
                          f"[V5记忆] {m.content[:200]}", "v5_sync")
                count += 1
    except Exception as e:
        logger.warning("memory sync failed: %s", e)

    logger.info("synced %d memory entries", count)
    return count


# ── Main ─────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="V5 → N.E.K.O persona sync")
    parser.add_argument("character", nargs="?", default=CHARACTER,
                        help=f"Character name (default: {CHARACTER})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without writing")
    args = parser.parse_args()

    character = args.character
    logger.info("Syncing V5 → persona for character: %s", character)

    persona = _load_neko_persona(character)

    total = 0
    total += sync_identity(character, persona)
    total += sync_affect(character, persona)
    total += sync_relationship(character, persona)
    total += sync_memories(character, persona)

    if total == 0:
        logger.warning("No data synced (V5 data files missing?)")
        return

    if args.dry_run:
        logger.info("DRY RUN: would write %d entries", total)
    else:
        _save_neko_persona(character, persona)

    logger.info("Sync complete: %d entries", total)


if __name__ == "__main__":
    main()
