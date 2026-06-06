"""Install/manage Hermes native skills.

Skills are Python files dropped in `hermes/data/skills/<name>.py` that expose
a `register(registry: SkillsRegistry)` function. They're loaded by `hermes.skills`
on startup and become available as tools the LLM can call.

This script provides:
    - install <name|url>    download + install from registry or URL
    - list                  show installed skills
    - remove <name>         uninstall
    - publish <name>        upload local skill to a registry (advanced, requires auth)

Registry: hermes/data/skills/registry.json
    Format: [{"name": "weather", "url": "https://...", "sha256": "...", "desc": "..."}]

Examples:
    python hermes/scripts/install_skill.py list
    python hermes/scripts/install_skill.py install weather
    python hermes/scripts/install_skill.py install https://gist.githubusercontent.com/.../weather.py
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

HERMES_ROOT = Path(r'E:\Hermes Agent')
SKILLS_DIR = HERMES_ROOT / 'hermes' / 'data' / 'skills'
REGISTRY_PATH = SKILLS_DIR / 'registry.json'

# Default registry: just an example. Users edit to add their own.
DEFAULT_REGISTRY = [
    # Example entry - uncomment to test:
    # {
    #     "name": "weather",
    #     "url": "https://raw.githubusercontent.com/your-org/hermes-skills/main/weather.py",
    #     "sha256": "abc123...",
    #     "desc": "Get current weather for a city (uses wttr.in)"
    # },
]


def load_registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        return DEFAULT_REGISTRY
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[warn] couldn't parse {REGISTRY_PATH}: {e}")
        return DEFAULT_REGISTRY


def save_registry(entries: list[dict]):
    REGISTRY_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def is_safe_skill_name(name: str) -> bool:
    """Only allow alphanumeric + underscore (no path traversal)."""
    return bool(re.match(r'^[a-z][a-z0-9_]{0,40}$', name))


def fetch_url(url: str) -> str:
    """Download URL contents as text."""
    print(f"  GET {url}")
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode("utf-8")


def cmd_list():
    print("Installed skills (in hermes/data/skills/):")
    if not SKILLS_DIR.exists():
        print("  (none - directory doesn't exist)")
        return
    for f in sorted(SKILLS_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        size = f.stat().st_size
        # Try to read description from first docstring
        desc = ""
        try:
            content = f.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.strip().startswith('"""') and len(line.strip()) > 5:
                    desc = line.strip().strip('"').strip("'")[:60]
                    break
        except Exception:
            pass
        print(f"  - {f.name:30s} {size:>5} bytes  {desc}")
    print()
    print("Registry entries:")
    entries = load_registry()
    if not entries:
        print("  (none - add entries to hermes/data/skills/registry.json)")
    for e in entries:
        print(f"  - {e.get('name'):20s}  {e.get('desc', '')}")
        print(f"      url: {e.get('url')}")


def cmd_install(target: str):
    """Install a skill by registry name or direct URL."""
    if not SKILLS_DIR.exists():
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    # Is target a URL?
    if target.startswith("http://") or target.startswith("https://"):
        url = target
        # Try to extract name from URL (last path component without .py)
        name = Path(target.split("?")[0]).stem
        if not is_safe_skill_name(name):
            print(f"[error] URL filename {name!r} doesn't match safe pattern (a-z0-9_)")
            sys.exit(1)
    else:
        # Look up in registry
        entries = load_registry()
        match = next((e for e in entries if e.get("name") == target), None)
        if not match:
            print(f"[error] {target!r} not in registry.")
            print("Add it to hermes/data/skills/registry.json first.")
            print("Or pass a direct URL: hermes skill install https://...")
            sys.exit(1)
        url = match["url"]
        name = match["name"]
        expected_sha = match.get("sha256")

    print(f"Installing skill: {name}")
    print(f"  from: {url}")

    # Fetch
    try:
        code = fetch_url(url)
    except Exception as e:
        print(f"[error] download failed: {e}")
        sys.exit(1)

    # Verify hash if registry provided one
    if 'expected_sha' in dir() and expected_sha:
        actual_sha = hashlib.sha256(code.encode("utf-8")).hexdigest()
        if actual_sha != expected_sha:
            print(f"[error] SHA mismatch!")
            print(f"  expected: {expected_sha}")
            print(f"  actual:   {actual_sha}")
            sys.exit(1)
        print("  SHA verified ✓")

    # Basic safety check: must have a register function
    if "def register(" not in code:
        print("[error] skill code doesn't contain 'def register(' function")
        print("        Hermes skills must expose: def register(registry: SkillsRegistry)")
        sys.exit(1)

    # Save
    dest = SKILLS_DIR / f"{name}.py"
    dest.write_text(code, encoding="utf-8")
    print(f"  saved: {dest}")
    print()
    print(f"[OK] skill {name!r} installed. Restart hermes-all.bat to load.")


def cmd_remove(name: str):
    if not is_safe_skill_name(name):
        print(f"[error] invalid skill name: {name}")
        sys.exit(1)
    target = SKILLS_DIR / f"{name}.py"
    if not target.exists():
        print(f"[error] skill not installed: {name}")
        sys.exit(1)
    target.unlink()
    print(f"[OK] removed {name}")


def cmd_publish(name: str, url: str, sha: str = "", desc: str = ""):
    if not is_safe_skill_name(name):
        print(f"[error] invalid skill name: {name}")
        sys.exit(1)
    entries = load_registry()
    if any(e.get("name") == name for e in entries):
        print(f"[error] {name!r} already in registry. Remove first.")
        sys.exit(1)
    entries.append({
        "name": name,
        "url": url,
        "sha256": sha,
        "desc": desc,
    })
    save_registry(entries)
    print(f"[OK] {name!r} added to registry")


def main():
    ap = argparse.ArgumentParser(description="Manage Hermes native skills")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list installed + registry skills")

    p_install = sub.add_parser("install", help="install skill (name from registry or URL)")
    p_install.add_argument("target", help="skill name OR direct URL")

    p_remove = sub.add_parser("remove", help="uninstall a skill")
    p_remove.add_argument("name")

    p_publish = sub.add_parser("publish", help="add a skill to registry")
    p_publish.add_argument("name")
    p_publish.add_argument("url")
    p_publish.add_argument("--sha", default="", help="expected SHA256 of skill code")
    p_publish.add_argument("--desc", default="", help="description")

    args = ap.parse_args()
    if args.cmd == "list":
        cmd_list()
    elif args.cmd == "install":
        cmd_install(args.target)
    elif args.cmd == "remove":
        cmd_remove(args.name)
    elif args.cmd == "publish":
        cmd_publish(args.name, args.url, args.sha, args.desc)


if __name__ == "__main__":
    main()
