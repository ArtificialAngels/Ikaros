#!/usr/bin/env python3
"""V5.2: Migrate N.E.K.O neko memory data → Ikaros V5 unified store.

Usage:
    python bin/migrate-neko-to-v5.py [character_name]
    
If character_name is omitted, migrates all characters found in neko's
memory/ directory.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [migrate] %(levelname)s: %(message)s")
logger = logging.getLogger("migrate")

# Paths
IKAROS_ROOT = Path(__file__).resolve().parent.parent
NEKO_MEMORY_DIR = IKAROS_ROOT / "core" / "neko" / "memory"
V5_ROOT = IKAROS_ROOT / "core" / "v5"

# Ensure v5 is importable
sys.path.insert(0, str(V5_ROOT))


def discover_characters() -> list[str]:
    """Discover character names from neko memory directory."""
    if not NEKO_MEMORY_DIR.exists():
        logger.warning("neko memory dir not found: %s", NEKO_MEMORY_DIR)
        return []
    chars = []
    for d in NEKO_MEMORY_DIR.iterdir():
        if d.is_dir() and (d / "facts.json").exists():
            chars.append(d.name)
    return sorted(chars)


def migrate_facts(character: str) -> int:
    """Migrate facts.json → V5 store (type='fact')."""
    from v5 import store
    facts_path = NEKO_MEMORY_DIR / character / "facts.json"
    if not facts_path.exists():
        logger.info("  [%s] no facts.json, skip", character)
        return 0

    with open(facts_path, "r", encoding="utf-8") as f:
        facts = json.load(f)

    if not isinstance(facts, list):
        logger.warning("  [%s] facts.json not a list, skip", character)
        return 0

    count = 0
    for fact in facts:
        content = fact.get("content", "")
        if not content:
            continue
        importance = fact.get("importance", 5)
        weight = min(1.0, importance / 10.0 + 0.3)
        tags = f"character:{character},migrated:neko"
        if fact.get("entity"):
            tags += f",entity:{fact['entity']}"
        try:
            store.store(
                content=content,
                type="fact",
                weight=weight,
                tags=tags,
                character=character,
                pad_p=float(fact.get("pad_p", 0.0)),
                pad_a=float(fact.get("pad_a", 0.0)),
                pad_d=float(fact.get("pad_d", 0.0)),
            )
            count += 1
        except Exception as e:
            logger.warning("  [%s] fact store failed: %s", character, e)

    logger.info("  [%s] migrated %d facts", character, count)
    return count


def migrate_reflections(character: str) -> int:
    """Migrate reflections.json → V5 reflections table."""
    from v5 import reflections as refmod
    refl_path = NEKO_MEMORY_DIR / character / "reflections.json"
    if not refl_path.exists():
        logger.info("  [%s] no reflections.json, skip", character)
        return 0

    with open(refl_path, "r", encoding="utf-8") as f:
        refs = json.load(f)

    if isinstance(refs, dict):
        refs = refs.values() if hasattr(refs, 'values') else []
    if not isinstance(refs, list):
        logger.warning("  [%s] reflections.json not list/dict, skip", character)
        return 0

    count = 0
    for ref in refs:
        content = ref.get("content", "") if isinstance(ref, dict) else ""
        if not content:
            continue
        importance = ref.get("importance", 5) if isinstance(ref, dict) else 5
        entity = ref.get("entity", "master") if isinstance(ref, dict) else "master"
        rtype = ref.get("relation_type", "experience") if isinstance(ref, dict) else "experience"

        try:
            rid = refmod.synthesize(
                character=character,
                content=content,
                entity=entity,
                relation_type=rtype,
                importance=importance,
            )
            if rid:
                # Apply evidence scores if present
                rein = float(ref.get("reinforcement", 0.0)) if isinstance(ref, dict) else 0.0
                disp = float(ref.get("disputation", 0.0)) if isinstance(ref, dict) else 0.0
                if rein or disp:
                    refmod.apply_evidence(character, rid, delta_rein=rein, delta_disp=disp)
                count += 1
        except Exception as e:
            logger.warning("  [%s] reflection sync failed: %s", character, e)

    logger.info("  [%s] migrated %d reflections", character, count)
    return count


def migrate_persona(character: str) -> int:
    """Migrate persona.json → V5 self_model (as narrative entries)."""
    from v5 import self_model as sm
    persona_path = NEKO_MEMORY_DIR / character / "persona.json"
    if not persona_path.exists():
        logger.info("  [%s] no persona.json, skip", character)
        return 0

    with open(persona_path, "r", encoding="utf-8") as f:
        persona = json.load(f)

    if not isinstance(persona, dict):
        logger.warning("  [%s] persona.json not dict, skip", character)
        return 0

    count = 0
    try:
        model = sm.SelfModel.load()
        narrative_key = f"self_narrative.{character}"
        entries = []

        # Extract entity entries
        for entity_key in ("master", "neko", "relationship"):
            entity_data = persona.get(entity_key, {})
            facts_list = entity_data.get("facts", []) if isinstance(entity_data, dict) else []
            for entry in facts_list:
                content = entry.get("content", "") if isinstance(entry, dict) else str(entry)
                if content:
                    entries.append(f"[{entity_key}] {content}")
                    count += 1

        if entries:
            existing = model.get(narrative_key, "")
            new_text = "\n".join(entries)
            model[narrative_key] = (existing + "\n" + new_text).strip() if existing else new_text
            sm.SelfModel.save()
            logger.info("  [%s] migrated %d persona entries", character, count)
    except Exception as e:
        logger.warning("  [%s] persona migration failed: %s", character, e)

    return count


def migrate_user_directives(character: str) -> int:
    """Migrate user_directives.json (if exists) → V5 user_directives table."""
    from v5 import user_directives as ud
    directives_path = NEKO_MEMORY_DIR / character / "user_directives.json"
    if not directives_path.exists():
        return 0

    with open(directives_path, "r", encoding="utf-8") as f:
        directives = json.load(f)

    if not isinstance(directives, list):
        return 0

    count = 0
    for d in directives:
        text = d.get("directive_text", "") if isinstance(d, dict) else ""
        if text:
            ud.add_directive(character, text)
            count += 1
    logger.info("  [%s] migrated %d directives", character, count)
    return count


def main():
    chars = sys.argv[1:] if len(sys.argv) > 1 else discover_characters()
    if not chars:
        logger.info("No characters to migrate. Usage: %s [char1 char2 ...]", sys.argv[0])
        logger.info("Discovered characters: %s", discover_characters())
        return

    total = {"facts": 0, "reflections": 0, "personas": 0, "directives": 0}
    t0 = time.time()

    for char in chars:
        logger.info("Migrating character: %s", char)
        total["facts"] += migrate_facts(char)
        total["reflections"] += migrate_reflections(char)
        total["personas"] += migrate_persona(char)
        total["directives"] += migrate_user_directives(char)

    elapsed = time.time() - t0
    logger.info("Migration complete in %.1fs", elapsed)
    logger.info("Total: facts=%d reflections=%d personas=%d directives=%d",
                total["facts"], total["reflections"], total["personas"], total["directives"])


if __name__ == "__main__":
    main()
