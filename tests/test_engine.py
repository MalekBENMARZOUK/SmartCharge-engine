from __future__ import annotations

from typing import TYPE_CHECKING

from smart_charging_optimization_engine.domain.results import SolverStatus
from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer

if TYPE_CHECKING:
    from smart_charging_optimization_engine.domain.models import ChargingScenario


def test_optimizer_returns_feasible_solution(fixture_scenario: ChargingScenario) -> None:
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_scenario)

    assert result.status in {SolverStatus.optimal, SolverStatus.feasible}
    assert len(result.site_summary) == fixture_scenario.site.horizon_slots
    assert all(
        slot.total_power_kw <= slot.site_power_limit_kw + 1e-6 for slot in result.site_summary
    )
    assert all(summary.unmet_energy_kwh <= 1e-6 for summary in result.vehicle_summaries)
    bus_assignments = [
        assignment for assignment in result.assignments if assignment.vehicle_id == "BUS-1"
    ]
    assert all(assignment.charger_id == "C1" for assignment in bus_assignments)
    assert result.objective_breakdown.site_demand_charge_cost >= 0.0


def test_infeasible_scenario_returns_unsolved_payload(fixture_scenario: ChargingScenario) -> None:
    impossible_payload = fixture_scenario.model_copy(deep=True)
    impossible_payload.vehicles[0].target_energy_kwh = impossible_payload.vehicles[
        0
    ].battery_capacity_kwh
    impossible_payload.vehicles[0].departure_slot = 1

    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(impossible_payload)

    assert result.status in {SolverStatus.optimal, SolverStatus.feasible}
    assert any(summary.unmet_energy_kwh > 0.0 for summary in result.vehicle_summaries)


def test_energy_conservation_per_vehicle(fixture_scenario: ChargingScenario) -> None:
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_scenario)

    for vs in result.vehicle_summaries:
        expected_final = vs.initial_energy_kwh + vs.total_net_energy_delta_kwh
        assert abs(vs.final_energy_kwh - expected_final) < 1e-4, (
            f"Vehicle {vs.vehicle_id}: expected final={expected_final}, got {vs.final_energy_kwh}"
        )


def test_soc_bounds_respected_per_slot(fixture_scenario: ChargingScenario) -> None:
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_scenario)

    for vehicle in fixture_scenario.vehicles:
        energy = vehicle.initial_energy_kwh
        for slot in range(fixture_scenario.site.horizon_slots):
            slot_assignments = [
                a
                for a in result.assignments
                if a.vehicle_id == vehicle.vehicle_id and a.slot == slot
            ]
            for a in slot_assignments:
                energy += a.energy_delta_kwh
            assert energy >= vehicle.minimum_energy_kwh - 1e-4, (
                f"Vehicle {vehicle.vehicle_id} slot {slot}: energy {energy} "
                f"< min {vehicle.minimum_energy_kwh}"
            )
            assert energy <= vehicle.battery_capacity_kwh + 1e-4, (
                f"Vehicle {vehicle.vehicle_id} slot {slot}: energy {energy} "
                f"> cap {vehicle.battery_capacity_kwh}"
            )


def test_no_negative_charge_power(fixture_scenario: ChargingScenario) -> None:
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_scenario)

    for a in result.assignments:
        assert a.charge_power_kw >= -1e-6, (
            f"Negative charge_power_kw={a.charge_power_kw} for {a.vehicle_id} slot {a.slot}"
        )


def test_site_power_limit_respected(fixture_scenario: ChargingScenario) -> None:
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_scenario)

    for slot_summary in result.site_summary:
        assert slot_summary.total_power_kw <= slot_summary.site_power_limit_kw + 1e-4, (
            f"Slot {slot_summary.slot}: power {slot_summary.total_power_kw} "
            f"> limit {slot_summary.site_power_limit_kw}"
        )


def test_export_within_limits(fixture_v2g_scenario: ChargingScenario) -> None:
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_v2g_scenario)

    for slot_summary in result.site_summary:
        if slot_summary.total_power_kw < 0:
            export_kw = -slot_summary.total_power_kw
            limit = slot_summary.site_export_limit_kw or 0.0
            assert export_kw <= limit + 1e-4, (
                f"Slot {slot_summary.slot}: export {export_kw} > limit {limit}"
            )
