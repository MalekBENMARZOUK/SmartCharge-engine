from __future__ import annotations

import pytest

from smart_charging_optimization_engine.domain.models import (
    Charger,
    ChargingScenario,
    ObjectiveConfig,
    RollingHorizonRequest,
    ScenarioMetadata,
    SiteProfile,
    TelemetrySnapshot,
    TelemetryVehicleState,
    TimeWindow,
    Vehicle,
)
from smart_charging_optimization_engine.services.rolling_horizon import (
    RollingHorizonOptimizer,
)


def test_rolling_horizon_reoptimization_rebases_slots(
    fixture_scenario: ChargingScenario,
    fixture_telemetry: TelemetrySnapshot,
) -> None:
    optimizer = RollingHorizonOptimizer()
    result = optimizer.reoptimize(
        RollingHorizonRequest(
            scenario=fixture_scenario,
            telemetry=fixture_telemetry,
        )
    )

    assert result.telemetry_snapshot_id == fixture_telemetry.snapshot_id
    assert all(slot.slot >= fixture_telemetry.current_slot for slot in result.site_summary)
    assert all(
        assignment.slot >= fixture_telemetry.current_slot for assignment in result.assignments
    )


def test_rolling_horizon_rejects_unknown_vehicle_state(
    fixture_scenario: ChargingScenario,
    fixture_telemetry: TelemetrySnapshot,
) -> None:
    optimizer = RollingHorizonOptimizer()
    invalid_telemetry = fixture_telemetry.model_copy(
        update={
            "vehicle_states": [
                *fixture_telemetry.vehicle_states,
                TelemetryVehicleState(
                    vehicle_id="unknown-vehicle",
                    observed_energy_kwh=10.0,
                    connected=True,
                    connected_charger_id=fixture_scenario.chargers[0].charger_id,
                ),
            ]
        },
        deep=True,
    )

    with pytest.raises(ValueError, match="unknown vehicles"):
        optimizer.reoptimize(
            RollingHorizonRequest(
                scenario=fixture_scenario,
                telemetry=invalid_telemetry,
            )
        )


def test_rolling_horizon_trims_export_lists(
    fixture_scenario: ChargingScenario,
    fixture_telemetry: TelemetrySnapshot,
) -> None:
    horizon = fixture_scenario.site.horizon_slots
    export_limits = [float(i + 1) for i in range(horizon)]
    export_prices = [0.01 * (i + 1) for i in range(horizon)]
    scenario_with_exports = fixture_scenario.model_copy(deep=True)
    scenario_with_exports.site.export_limit_kw = export_limits
    scenario_with_exports.site.export_price_per_kwh = export_prices

    optimizer = RollingHorizonOptimizer()
    current_slot = fixture_telemetry.current_slot
    remaining = horizon - current_slot

    trimmed, _, _ = optimizer._build_remaining_horizon_scenario(
        scenario_with_exports,
        fixture_telemetry,
    )
    assert trimmed.site.horizon_slots == remaining
    assert len(trimmed.site.power_limit_kw) == remaining
    assert len(trimmed.site.electricity_price_per_kwh) == remaining
    assert trimmed.site.export_limit_kw is not None
    assert len(trimmed.site.export_limit_kw) == remaining
    assert trimmed.site.export_limit_kw == export_limits[current_slot:]
    assert trimmed.site.export_price_per_kwh is not None
    assert len(trimmed.site.export_price_per_kwh) == remaining
    assert trimmed.site.export_price_per_kwh == export_prices[current_slot:]


def test_rolling_horizon_preserves_connected_charger_for_current_slot() -> None:
    scenario = ChargingScenario(
        metadata=ScenarioMetadata(scenario_name="rolling-lock"),
        site=SiteProfile(
            time_step_minutes=60,
            horizon_slots=2,
            power_limit_kw=[120.0, 120.0],
            electricity_price_per_kwh=[0.0, 0.0],
        ),
        chargers=[
            Charger(charger_id="slow", max_power_kw=20.0),
            Charger(charger_id="fast", max_power_kw=80.0),
        ],
        vehicles=[
            Vehicle(
                vehicle_id="vehicle-1",
                battery_capacity_kwh=100.0,
                initial_energy_kwh=10.0,
                target_energy_kwh=100.0,
                minimum_energy_kwh=10.0,
                max_charging_power_kw=80.0,
                availability_windows=[TimeWindow(start_slot=0, end_slot=2)],
                departure_slot=2,
            )
        ],
        objective=ObjectiveConfig(unmet_demand_penalty_per_kwh=1000.0),
    )
    telemetry = TelemetrySnapshot(
        snapshot_id="snapshot-1",
        current_slot=0,
        vehicle_states=[
            TelemetryVehicleState(
                vehicle_id="vehicle-1",
                observed_energy_kwh=10.0,
                connected=True,
                connected_charger_id="slow",
            )
        ],
    )

    optimizer = RollingHorizonOptimizer()
    result = optimizer.reoptimize(RollingHorizonRequest(scenario=scenario, telemetry=telemetry))

    slot_zero_assignments = [
        assignment for assignment in result.assignments if assignment.slot == 0
    ]
    assert slot_zero_assignments
    assert {assignment.charger_id for assignment in slot_zero_assignments} == {"slow"}


def test_rolling_horizon_rejects_connected_incompatible_charger() -> None:
    scenario = ChargingScenario(
        metadata=ScenarioMetadata(scenario_name="rolling-incompatible"),
        site=SiteProfile(
            time_step_minutes=60,
            horizon_slots=2,
            power_limit_kw=[60.0, 60.0],
            electricity_price_per_kwh=[0.0, 0.0],
        ),
        chargers=[
            Charger(charger_id="charger-a", max_power_kw=60.0),
            Charger(charger_id="charger-b", max_power_kw=60.0),
        ],
        vehicles=[
            Vehicle(
                vehicle_id="vehicle-1",
                battery_capacity_kwh=80.0,
                initial_energy_kwh=10.0,
                target_energy_kwh=50.0,
                minimum_energy_kwh=10.0,
                max_charging_power_kw=60.0,
                availability_windows=[TimeWindow(start_slot=0, end_slot=2)],
                departure_slot=2,
                compatible_charger_ids=["charger-a"],
            )
        ],
    )
    telemetry = TelemetrySnapshot(
        snapshot_id="snapshot-2",
        current_slot=0,
        vehicle_states=[
            TelemetryVehicleState(
                vehicle_id="vehicle-1",
                observed_energy_kwh=10.0,
                connected=True,
                connected_charger_id="charger-b",
            )
        ],
    )

    optimizer = RollingHorizonOptimizer()

    with pytest.raises(ValueError, match="compatible_charger_ids"):
        optimizer.reoptimize(RollingHorizonRequest(scenario=scenario, telemetry=telemetry))
