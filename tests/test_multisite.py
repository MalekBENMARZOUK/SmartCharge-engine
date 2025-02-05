from __future__ import annotations

from typing import TYPE_CHECKING

from smart_charging_optimization_engine.domain.results import SolverStatus
from smart_charging_optimization_engine.optimization.multisite import (
    MultiSiteSmartChargingOptimizer,
)

if TYPE_CHECKING:
    from smart_charging_optimization_engine.domain.models import PortfolioScenario


def test_multisite_optimizer_respects_network_limit(
    fixture_portfolio: PortfolioScenario,
) -> None:
    optimizer = MultiSiteSmartChargingOptimizer()
    result = optimizer.solve(fixture_portfolio)

    assert result.status in {SolverStatus.optimal, SolverStatus.feasible}
    assert len(result.site_results) == 2
    assert all(
        item.total_power_kw <= item.network_power_limit_kw + 1e-6
        for item in result.network_summary
        if item.network_power_limit_kw is not None
    )
