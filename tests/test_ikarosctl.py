"""Tests for the unified Ikaros launcher dispatcher."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_ikarosctl(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "core"))
    sys.modules.pop("ikarosctl", None)
    return importlib.import_module("ikarosctl")


def test_resolve_ikaros_root_uses_script_location_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ikarosctl = _load_ikarosctl(monkeypatch)
    monkeypatch.delenv("IKAROS_ROOT", raising=False)

    assert ikarosctl.resolve_ikaros_root() == PROJECT_ROOT


def test_resolve_ikaros_root_prefers_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ikarosctl = _load_ikarosctl(monkeypatch)
    configured = PROJECT_ROOT.parent / "configured-ikaros-root"
    monkeypatch.setenv("IKAROS_ROOT", str(configured))

    assert ikarosctl.resolve_ikaros_root() == configured

    if os.name == "nt":
        msys_configured = f"/{configured.drive[0].lower()}/{str(configured)[3:]}"
        monkeypatch.setenv("IKAROS_ROOT", msys_configured)
        assert ikarosctl.resolve_ikaros_root() == configured


def test_dispatch_rejects_unknown_subcommand(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ikarosctl = _load_ikarosctl(monkeypatch)

    with pytest.raises(ikarosctl.LauncherError, match="unknown subcommand: nope"):
        ikarosctl.dispatch(["nope"])

    assert capsys.readouterr().out == ""


def test_dispatch_web_uses_registry_start_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ikarosctl = _load_ikarosctl(monkeypatch)
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_start(root: Path, component_id: str, args: tuple[str, ...] = ()) -> int:
        calls.append((component_id, args))
        return 0

    monkeypatch.setattr(ikarosctl, "start_component", fake_start)

    assert ikarosctl.dispatch(["web"]) == 0
    assert calls == [("dsh", ("web",))]


def test_doctor_and_status_read_four_registry_components(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ikarosctl = _load_ikarosctl(monkeypatch)
    monkeypatch.setattr(
        ikarosctl,
        "load_components",
        lambda root=PROJECT_ROOT: [
            ikarosctl.ComponentSpec(
                id=component_id,
                name=component_id,
                category="tool",
                port=3000 + index,
                process_marker=component_id,
            )
            for index, component_id in enumerate(
                ["dsh", "conversation-tree", "embedding", "herdr"]
            )
        ],
    )

    ikarosctl.doctor(PROJECT_ROOT)
    doctor_output = capsys.readouterr().out
    for component_id in ["dsh", "conversation-tree", "embedding", "herdr"]:
        assert component_id in doctor_output

    ikarosctl.status(PROJECT_ROOT)
    status_output = capsys.readouterr().out
    for component_id in ["dsh", "conversation-tree", "embedding", "herdr"]:
        assert component_id in status_output


def test_dispatch_stop_rejects_unconfigured_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ikarosctl = _load_ikarosctl(monkeypatch)
    monkeypatch.setattr(
        ikarosctl,
        "load_components",
        lambda root=PROJECT_ROOT: [
            ikarosctl.ComponentSpec(
                id="dsh",
                name="dsh",
                category="tool",
                port=3080,
                process_marker="dsh",
                lifecycle={
                    "start_script": "bin/start-dsh-ikaros.bat",
                    "stop_script": "bin/restart-dsh-ikaros.ps1",
                },
            )
        ],
    )
    with pytest.raises(ikarosctl.LauncherError, match="not configured"):
        ikarosctl.dispatch(["stop", "not-registered"])
