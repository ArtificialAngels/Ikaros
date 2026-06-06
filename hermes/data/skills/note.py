"""Sample custom skill: notes (save/load short notes to a local file)"""


import os
from pathlib import Path

SKILL_NAME = "note"
SKILL_DESCRIPTION = "Save or retrieve a short note (action='save'|'get'|'list', key=..., value=...)"

NOTES_DIR = Path(os.environ.get("HERMES_DATA_DIR", "/data")) / "notes"
NOTES_DIR.mkdir(parents=True, exist_ok=True)


def run(args: dict) -> str:
    action = args.get("action", "get")
    key = args.get("key", "")
    value = args.get("value", "")

    if action == "save":
        if not key or not value:
            return "Error: 'key' and 'value' required for save"
        (NOTES_DIR / f"{key}.txt").write_text(value, encoding="utf-8")
        return f"✓ Saved note: {key}"
    elif action == "get":
        if not key:
            return "Error: 'key' required for get"
        p = NOTES_DIR / f"{key}.txt"
        if p.exists():
            return p.read_text(encoding="utf-8")
        return f"Note not found: {key}"
    elif action == "list":
        notes = [f.stem for f in NOTES_DIR.glob("*.txt")]
        return "Notes: " + ", ".join(notes) if notes else "No notes yet"
    elif action == "delete":
        p = NOTES_DIR / f"{key}.txt"
        if p.exists():
            p.unlink()
            return f"✓ Deleted: {key}"
        return f"Not found: {key}"
    return f"Unknown action: {action}"
