from __future__ import annotations

from typing import TYPE_CHECKING

from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
from smart_charging_optimization_engine.visualization.plots import (
    write_power_profile_plot,
    write_vehicle_schedule_plot,
)

if TYPE_CHECKING:
    from pathlib import Path

    from smart_charging_optimization_engine.domain.models import ChargingScenario


def test_write_power_profile_plot(fixture_scenario: ChargingScenario, tmp_path: Path) -> None:
    result = SmartChargingOptimizer().solve(fixture_scenario)
    output = write_power_profile_plot(result, tmp_path / "plots")
    assert output.exists()
    assert output.suffix == ".html"
    assert output.stat().st_size > 0


def test_write_vehicle_schedule_plot(fixture_scenario: ChargingScenario, tmp_path: Path) -> None:
    result = SmartChargingOptimizer().solve(fixture_scenario)
    output = write_vehicle_schedule_plot(result, tmp_path / "plots")
    assert output.exists()
    assert output.suffix == ".html"
    assert output.stat().st_size > 0
