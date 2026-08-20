"""Component registry loader.

Reads ``config/components.yaml`` and exposes typed ``ComponentSpec`` records.
The loader is the single entry point for everything else in the project that
needs to know "which components exist, on which port, started by which script".

Design notes
------------
* YAML schema is intentionally flat per-component (no nested inheritance) so
  diffs stay reviewable. Categories are a free-form string for now
  (``memory / ui / runtime / tool / embedding``) -- future schema versions
  may tighten this into an enum.
* ``port`` is typed ``int | None`` because Herdr runs over a Windows named
  pipe, not TCP. ``None`` means "no TCP port"; do not invent a default.
* ``load_components`` caches the parsed list at module level. Tests that
  need a fresh load can call ``load_components(path=...)`` with an explicit
  path; production code should use ``list_components()``.

Public API
----------
* ``ComponentSpec`` -- the dataclass.
* ``load_components`` -- parse + validate YAML into a list.
* ``get_component`` -- lookup one spec by id.
* ``list_components`` -- cached accessor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Required-field schema
# ---------------------------------------------------------------------------

REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "category",
    "port",
    "process_marker",
)

VALID_CATEGORIES: frozenset[str] = frozenset(
    {"memory", "ui", "runtime", "tool", "embedding"}
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ComponentSpec:
    """Typed view of a single entry in ``config/components.yaml``.

    Attributes
    ----------
    id
        Stable kebab-case identifier (e.g. ``"dsh"``, ``"conversation-tree"``).
        Used as the lookup key in ``get_component``.
    name
        Human-readable display name (e.g. ``"DeepSeek Harness"``).
    category
        Coarse role tag. One of: ``memory``, ``ui``, ``runtime``, ``tool``,
        ``embedding``. Free-form today; reserved as enum-like for the
        future validator.
    port
        TCP port the service listens on, or ``None`` for non-TCP transports
        (e.g. Herdr's named pipe).
    process_marker
        Substring used to detect the running process in the OS process
        table (Windows ``tasklist`` / POSIX ``pgrep``).
    dependencies
        Component IDs that must be running before this one starts. Order
        is informational; topological scheduling is the caller's job.
    config_schema
        Reserved for a future JSON-Schema fragment per component. Empty
        dict for now.
    healthcheck
        Dict with at least ``type`` (``port`` / ``pipe`` / ``http``) and
        ``endpoint``. Shape is intentionally loose for now.
    lifecycle
        Dict with keys ``start_script`` / ``stop_script`` /
        ``restart_script`` (each may be ``null``) and ``watchdog``
        (``self`` / ``none`` / ``central``).
    dsh_integration
        Dict describing the dsh overlay (``overlay`` path or ``null``) and
        the MCP servers it depends on (``mcp_servers`` list, may be empty).
    """

    id: str
    name: str
    category: str
    port: int | None
    process_marker: str
    dependencies: list[str] = field(default_factory=list)
    config_schema: dict[str, Any] = field(default_factory=dict)
    healthcheck: dict[str, Any] = field(default_factory=dict)
    lifecycle: dict[str, Any] = field(default_factory=dict)
    dsh_integration: dict[str, Any] = field(default_factory=dict)

    # ---- (de)serialization ----------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ComponentSpec":
        """Build a ``ComponentSpec`` from a raw YAML mapping.

        Performs schema validation: raises ``ValueError`` if a required
        field is missing. Optional fields default to empty containers.
        """
        missing = [k for k in REQUIRED_FIELDS if k not in data]
        if missing:
            raise ValueError(
                f"ComponentSpec missing required field(s) {missing!r} "
                f"in entry: {data.get('id', '<no id>')!r}"
            )

        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            category=str(data["category"]),
            port=(
                int(data["port"])
                if data["port"] is not None
                else None
            ),
            process_marker=str(data["process_marker"]),
            dependencies=list(data.get("dependencies") or []),
            config_schema=dict(data.get("config_schema") or {}),
            healthcheck=dict(data.get("healthcheck") or {}),
            lifecycle=dict(data.get("lifecycle") or {}),
            dsh_integration=dict(data.get("dsh_integration") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-/YAML-friendly dict mirroring the on-disk schema.

        ``port`` is preserved as ``int`` or ``None`` (matches YAML).
        """
        return asdict(self)


# ---------------------------------------------------------------------------
# Loaders / accessors
# ---------------------------------------------------------------------------

# Module-level cache. Tests can call load_components(path=...) with an
# explicit path to bypass it; production callers should prefer
# list_components() / get_component().
_components_cache: list[ComponentSpec] | None = None
_components_cache_path: Path | None = None


def load_components(
    path: str | Path = "config/components.yaml",
) -> list[ComponentSpec]:
    """Load and validate the component registry YAML.

    Parameters
    ----------
    path
        Path to the YAML file. Default is the canonical
        ``config/components.yaml`` relative to the project root (i.e.
        where ``bin/ikaros-env.bat`` would resolve ``IKAROS_ROOT``).

    Returns
    -------
    list[ComponentSpec]
        One spec per entry under the top-level ``components:`` key.

    Raises
    ------
    FileNotFoundError
        ``path`` does not exist.
    yaml.YAMLError
        The file is not valid YAML.
    ValueError
        A component entry is missing required fields.
    """
    yaml_path = Path(path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"components registry not found: {yaml_path}")

    with yaml_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise ValueError(
            f"components YAML root must be a mapping, got {type(raw).__name__}"
        )

    entries = raw.get("components")
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError(
            "'components' must be a list, got "
            f"{type(entries).__name__}"
        )

    specs: list[ComponentSpec] = []
    for entry in entries:
        specs.append(ComponentSpec.from_dict(entry))

    # Refresh module cache only when caller uses the default path so that
    # list_components() / get_component() reflect the latest on-disk
    # registry in production. Explicit-path loads are treated as
    # one-shot (cache is intentionally NOT poisoned).
    global _components_cache, _components_cache_path
    if yaml_path == Path("config/components.yaml"):
        _components_cache = specs
        _components_cache_path = yaml_path

    return specs


def get_component(component_id: str) -> ComponentSpec | None:
    """Return the spec for ``component_id`` or ``None`` if not registered."""
    for spec in list_components():
        if spec.id == component_id:
            return spec
    return None


def list_components() -> list[ComponentSpec]:
    """Return all registered components.

    Uses a module-level cache; the cache is populated lazily by
    ``load_components`` when called with the default path. To force a
    fresh read, call ``load_components("config/components.yaml")``
    explicitly.
    """
    if _components_cache is None:
        load_components()
    assert _components_cache is not None  # for type-checkers
    return list(_components_cache)


# ---------------------------------------------------------------------------
# Self-check (handy when running this file directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import json

    specs = load_components()
    print(
        json.dumps(
            {"count": len(specs), "ids": [s.id for s in specs]},
            indent=2,
        )
    )