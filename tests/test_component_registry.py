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


def test_load_components_returns_three_components(specs: list[ComponentSpec]) -> None:
    """2026-08-23 herdr/pi 退役后恰有三枚组件."""
    assert len(specs) == 3, f"expected 3 components, got {len(specs)}"


def test_load_components_ids(specs: list[ComponentSpec]) -> None:
    """三枚 id = dsh / conversation-tree / embedding."""
    ids = {s.id for s in specs}
    assert ids == {"dsh", "conversation-tree", "embedding"}


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
    # 2026-09-05 重构: start_script 改用 ikarosctl (之前 bin/start-dsh-ikaros.bat 已删)
    assert found.lifecycle.get("start_script") == "python core/ikarosctl.py web"
    assert found.lifecycle.get("restart_script") == "python core/ikarosctl.py dsh restart"
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
        ("embedding", 8587),
        # conversation-tree 改为动态端口 (见下方 test_conversation_tree_dynamic_port),
        # 不再参与固定端口比对。
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


def test_conversation_tree_dynamic_port(specs: list[ComponentSpec]) -> None:
    """conversation-tree 无固定端口 —— 2026-08-23 起改用动态端口。

    ⚠️ 为什么要单独断言而不是塞进上面的固定端口表:
        `server.py --port 0` 由 OS 分配端口, 实际值写 `tmp/ct-port.json`,
        探测侧走 `healthcheck.type: port_file`。把 48920 硬编码进注册表
        会让「注册表 / 启动脚本 / 探测方式」三处互相矛盾 —— 注册表说 48920,
        启动脚本开的是随机端口, 探测却去读文件。
        三者必须一致地描述「动态」这件事, 所以这里断言的是**动态这一约定本身**:
        port 为空 + 探测走端口文件 + 启动脚本带 --port 0。
    """
    spec = next(s for s in specs if s.id == "conversation-tree")
    assert spec.port is None, (
        "conversation-tree 应为动态端口 (port: null); "
        "若已改回固定端口, 请同步 healthcheck 与 lifecycle.start_script 并移回固定端口表"
    )
    assert spec.healthcheck["type"] == "port_file", (
        f"动态端口必须走端口文件探测, 实际: {spec.healthcheck.get('type')}"
    )
    assert "--port 0" in (spec.lifecycle.get("start_script") or ""), (
        f"启动脚本应带 --port 0, 实际: {spec.lifecycle.get('start_script')}"
    )


# 2026-08-23: herdr (named-pipe) 组件已随 pi 底座退役, 相关断言移除

# ---------------------------------------------------------------------------
# to_dict round-trip
# ---------------------------------------------------------------------------


def test_to_dict_round_trip(specs: list[ComponentSpec]) -> None:
    """``to_dict()`` preserves enough to reconstruct the spec."""
    for spec in specs:
        rebuilt = ComponentSpec.from_dict(spec.to_dict())
        assert rebuilt == spec