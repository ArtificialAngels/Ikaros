"""
Skills system for Hermes.

Each skill is a Python module that exports a `run(args: dict) -> str` function.
Skills can be loaded from the custom_dir.
"""
from __future__ import annotations
import importlib.util
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes.skills")


class Skill:
    def __init__(self, name: str, description: str, run_fn, path: Path | None = None):
        self.name = name
        self.description = description
        self.run = run_fn
        self.path = path

    def __repr__(self):
        return f"<Skill {self.name}>"


# ---- Built-in skills ----

def _skill_time(args: dict) -> str:
    """Get current time."""
    from datetime import datetime
    fmt = args.get("format", "%Y-%m-%d %H:%M:%S")
    return datetime.now().strftime(fmt)


def _skill_calc(args: dict) -> str:
    """Evaluate a simple math expression. Use with caution."""
    expr = args.get("expression", "")
    allowed = set("0123456789+-*/.() ")
    if not all(c in allowed for c in expr):
        return "Error: only numbers and + - * / . ( ) allowed"
    try:
        return str(eval(expr, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"Error: {e}"


def _skill_echo(args: dict) -> str:
    """Echo back the input."""
    return str(args.get("text", ""))


def _skill_status(args: dict) -> str:
    """Return system status (set by agent at runtime)."""
    return "OK"  # Will be replaced by agent's status


BUILTIN_SKILLS: dict[str, Skill] = {
    "time": Skill("time", "Get current time", _skill_time),
    "calc": Skill("calc", "Evaluate a math expression", _skill_calc),
    "echo": Skill("echo", "Echo input", _skill_echo),
}


class SkillRegistry:
    def __init__(self, custom_dir: Path):
        self.custom_dir = Path(custom_dir)
        self.custom_dir.mkdir(parents=True, exist_ok=True)
        self.skills: dict[str, Skill] = dict(BUILTIN_SKILLS)
        self._load_custom()

    def _load_custom(self):
        for f in self.custom_dir.glob("*.py"):
            if f.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"hermes_skill_{f.stem}", f)
                if not spec or not spec.loader:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                name = getattr(mod, "SKILL_NAME", f.stem)
                desc = getattr(mod, "SKILL_DESCRIPTION", f"Custom skill: {name}")
                run = getattr(mod, "run", None)
                if not callable(run):
                    logger.warning(f"Skill {f.name} has no `run` function")
                    continue
                self.skills[name] = Skill(name, desc, run, f)
                logger.info(f"Loaded custom skill: {name}")
            except Exception as e:
                logger.warning(f"Failed to load skill {f.name}: {e}")

    def list(self) -> list[dict[str, str]]:
        return [{"name": s.name, "description": s.description} for s in self.skills.values()]

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def call(self, name: str, args: dict | None = None) -> str:
        skill = self.skills.get(name)
        if not skill:
            return f"Unknown skill: {name}"
        try:
            return skill.run(args or {})
        except Exception as e:
            return f"Skill error: {e}"

    def describe_for_llm(self) -> str:
        """Return a description of available skills for the system prompt."""
        lines = ["Available skills:"]
        for s in self.skills.values():
            lines.append(f"  - {s.name}: {s.description}")
        return "\n".join(lines)
