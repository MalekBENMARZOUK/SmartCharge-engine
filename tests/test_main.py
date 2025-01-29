from __future__ import annotations

import runpy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def test_main_invokes_cli_app(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"value": False}

    def fake_app() -> None:
        called["value"] = True

    monkeypatch.setattr("smart_charging_optimization_engine.cli.app", fake_app)

    runpy.run_module("smart_charging_optimization_engine.__main__", run_name="__main__")

    assert called["value"] is True
