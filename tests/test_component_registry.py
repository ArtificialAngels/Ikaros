"""Tests for the components registry (Task 3.7 line3-dsh-base).

Covers:
  * ``load_components`` returns the 4 registered components
    (dsh / conversation-tree / embedding / herdr).
  * ``get_component("dsh")`` returns the canonical spec.
  * ``config/components.yaml`` parses cleanly via ``yaml.safe_load``.
  * All required fields (id/name/category/port/process_marker) are
    present and well-typed in every entry.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# The `core` package is implicit-namespace (no __init__.py) and is shadowed
# by E:\Ikaros\core on sys.path via sitecustomize. Make the project root win.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest  # noqa: E402

from core.components.registry import (  # noqa: E402
    REQUIRED_FIELDS,
    ComponentSpec,
    get_component,
    list_components,
    load_components,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = PROJECT_ROOT / "config" / "components.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def specs() -> list[ComponentSpec]:
    """Load the on-disk registry once per module."""
    return load_components(REGISTRY_PATH)


# ---------------------------------------------------------------------------
# load_components: shape
# ---------------------------------------------------------------------------


def test_load_components_returns_four_components(specs: list[ComponentSpec]) -> None:
    """Task 3.6 ships exactly four components."""
    assert len(specs) == 4, f"expected 4 components, got {len(specs)}"


def test_load_components_ids(specs: list[ComponentSpec]) -> None:
    """The four ids are the canonical line3 set."""
    ids = {s.id for s in specs}
    assert ids == {"dsh", "conversation-tree", "embedding", "herdr"}


def test_load_components_ids_unique(specs: list[ComponentSpec]) -> None:
    ids = [s.id for s in specs]
    assert len(ids) == len(set(ids)), "component ids must be unique"


# ---------------------------------------------------------------------------
# get_component: dsh canonical lookup
# ---------------------------------------------------------------------------


def test_get_component_dsh_returns_spec(specs: list[ComponentSpec]) -> None:
    target = next(s for s in specs if s.id == "dsh")
    found = get_component("dsh")
    assert found is not None
    assert found.id == "dsh"
    assert found.port == 3080
    assert found.process_marker == "dsh"
    assert found.category == "tool"
    assert found.lifecycle.get("start_script") == "bin/start-dsh-ikaros.bat"
    assert found.lifecycle.get("restart_script") == "bin/restart-dsh-ikaros.ps1"
    assert found.lifecycle.get("watchdog") == "self"
    assert found.dsh_integration.get("overlay") == "core/ikaros-dsh/cordis.patch.yml"
    assert "ikaros-v5-memory" in found.dsh_integration.get("mcp_servers", [])
    assert "embedding" in found.dependencies


def test_get_component_unknown_returns_none() -> None:
    """Unknown ids return None, not raise."""
    # list_components() pulls from cache; populated by the specs fixture above.
    list_components()
    assert get_component("does-not-exist") is None


# ---------------------------------------------------------------------------
# YAML schema validity
# ---------------------------------------------------------------------------


def test_yaml_parses_without_error() -> None:
    """``yaml.safe_load`` on the registry file must not raise."""
    with REGISTRY_PATH.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    assert isinstance(loaded, dict)
    assert isinstance(loaded.get("components"), list)
    assert len(loaded["components"]) >= 1


def test_required_fields_present(specs: list[ComponentSpec]) -> None:
    """Every spec has all required fields populated.

    ``port`` may legitimately be ``None`` for non-TCP transports (e.g.
    Herdr's named pipe). The other required fields (id/name/category/
    process_marker) must be non-empty strings.
    """
    for spec in specs:
        for field_name in REQUIRED_FIELDS:
            value = getattr(spec, field_name)
            # Missing key on the dataclass would already AttributeError, so
            # reaching this line proves the field exists.
            if field_name == "port":
                # Port is allowed to be None (non-TCP transports).
                assert value is None or isinstance(value, int), (
                    f"{spec.id!r} port must be int or None, got {value!r}"
                )
            else:
                assert value is not None, (
                    f"{spec.id!r} missing required field {field_name!r}"
                )
                assert value != "", (
                    f"{spec.id!r} has empty {field_name!r}"
                )


def test_required_fields_field_types(specs: list[ComponentSpec]) -> None:
    """Spot-check field types after from_dict."""
    for spec in specs:
        assert isinstance(spec.id, str)
        assert isinstance(spec.name, str)
        assert isinstance(spec.category, str)
        assert spec.port is None or isinstance(spec.port, int)
        assert isinstance(spec.process_marker, str)
        assert isinstance(spec.dependencies, list)
        assert isinstance(spec.config_schema, dict)
        assert isinstance(spec.healthcheck, dict)
        assert isinstance(spec.lifecycle, dict)
        assert isinstance(spec.dsh_integration, dict)


# ---------------------------------------------------------------------------
# Architecture-doc port alignment (docs/ARCHITECTURE.md §1.2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "component_id,expected_port",
    [
        ("dsh", 3080),
        ("conversation-tree", 48920),
        ("embedding", 8587),
        # herdr has no TCP port (named pipe) -- handled separately below.
    ],
)
def test_port_matches_architecture(
    specs: list[ComponentSpec],
    component_id: str,
    expected_port: int,
) -> None:
    spec = next(s for s in specs if s.id == component_id)
    assert spec.port == expected_port


def test_herdr_has_no_tcp_port(specs: list[ComponentSpec]) -> None:
    """Herdr uses a Windows named pipe; the registry must record port=None."""
    herdr = next(s for s in specs if s.id == "herdr")
    assert herdr.port is None
    assert herdr.healthcheck.get("type") == "pipe"


# ---------------------------------------------------------------------------
# to_dict round-trip
# ---------------------------------------------------------------------------


def test_to_dict_round_trip(specs: list[ComponentSpec]) -> None:
    """``to_dict()`` preserves enough to reconstruct the spec."""
    for spec in specs:
        rebuilt = ComponentSpec.from_dict(spec.to_dict())
        assert rebuilt == spec