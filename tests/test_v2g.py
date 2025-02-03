from __future__ import annotations

from typing import TYPE_CHECKING

from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer

if TYPE_CHECKING:
    from smart_charging_optimization_engine.domain.models import ChargingScenario


def test_optimizer_dispatches_vehicle_to_grid_when_profitable(
    fixture_v2g_scenario: ChargingScenario,
) -> None:
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_v2g_scenario)

    assert result.objective_breakdown.export_revenue > 0.0
    assert any(assignment.is_vehicle_to_grid for assignment in result.assignments)
    assert any(slot.total_power_kw < 0.0 for slot in result.site_summary)
