from __future__ import annotations

from typing import TYPE_CHECKING

from smart_charging_optimization_engine.domain.models import (
    ChargingScenario,
    RollingHorizonRequest,
    TelemetrySnapshot,
    TimeWindow,
    Vehicle,
)
from smart_charging_optimization_engine.optimization.engine import (
    SmartChargingOptimizer,
    SolverConfig,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smart_charging_optimization_engine.domain.results import OptimizationResult


class RollingHorizonOptimizer:
    def __init__(self, optimizer: SmartChargingOptimizer | None = None) -> None:
        self._optimizer = optimizer or SmartChargingOptimizer()

    @property
    def solver_config(self) -> SolverConfig:
        return self._optimizer.solver_config

    def reoptimize(self, request: RollingHorizonRequest) -> OptimizationResult:
        scenario, current_slot, required_charger_by_vehicle_slot = (
            self._build_remaining_horizon_scenario(
                request.scenario,
                request.telemetry,
            )
        )
        result = self._optimizer.solve(
            scenario,
            required_charger_by_vehicle_slot=required_charger_by_vehicle_slot,
        )
        return self._rebase_result_slots(result, current_slot, request.telemetry.snapshot_id)

    def _build_remaining_horizon_scenario(
        self,
        scenario: ChargingScenario,
        telemetry: TelemetrySnapshot,
    ) -> tuple[ChargingScenario, int, dict[tuple[str, int], str]]:
        current_slot = telemetry.current_slot
        trimmed = scenario.model_copy(deep=True)
        horizon_slots = trimmed.site.horizon_slots
        if current_slot >= horizon_slots:
            msg = "telemetry.current_slot must be strictly smaller than the scenario horizon"
            raise ValueError(msg)

        vehicle_by_id = {vehicle.vehicle_id: vehicle for vehicle in trimmed.vehicles}
        charger_ids = {charger.charger_id for charger in trimmed.chargers}
        vehicle_state_by_id = {
            vehicle_state.vehicle_id: vehicle_state for vehicle_state in telemetry.vehicle_states
        }
        self._validate_telemetry_against_scenario(vehicle_by_id, charger_ids, telemetry)
        required_charger_by_vehicle_slot: dict[tuple[str, int], str] = {}
        remaining_horizon = horizon_slots - current_slot
        trimmed.site.power_limit_kw = trimmed.site.power_limit_kw[current_slot:]
        trimmed.site.electricity_price_per_kwh = trimmed.site.electricity_price_per_kwh[
            current_slot:
        ]
        if trimmed.site.export_limit_kw is not None:
            trimmed.site.export_limit_kw = trimmed.site.export_limit_kw[current_slot:]
        if trimmed.site.export_price_per_kwh is not None:
            trimmed.site.export_price_per_kwh = trimmed.site.export_price_per_kwh[current_slot:]
        trimmed.site.horizon_slots = remaining_horizon

        if telemetry.power_limit_override_kw is not None:
            if len(telemetry.power_limit_override_kw) != remaining_horizon:
                msg = "telemetry.power_limit_override_kw must match the remaining horizon length"
                raise ValueError(msg)
            trimmed.site.power_limit_kw = telemetry.power_limit_override_kw
        if telemetry.electricity_price_override_per_kwh is not None:
            if len(telemetry.electricity_price_override_per_kwh) != remaining_horizon:
                msg = (
                    "telemetry.electricity_price_override_per_kwh must match the "
                    "remaining horizon length"
                )
                raise ValueError(msg)
            trimmed.site.electricity_price_per_kwh = telemetry.electricity_price_override_per_kwh

        remaining_vehicles: list[Vehicle] = []
        for vehicle in trimmed.vehicles:
            if vehicle.departure_slot <= current_slot:
                continue
            telemetry_state = vehicle_state_by_id.get(vehicle.vehicle_id)
            if telemetry_state is not None:
                vehicle.initial_energy_kwh = telemetry_state.observed_energy_kwh

            future_windows: list[TimeWindow] = []
            for window in vehicle.availability_windows:
                start_slot = max(window.start_slot, current_slot)
                end_slot = min(window.end_slot, horizon_slots)
                if end_slot > start_slot:
                    adjusted_start = start_slot - current_slot
                    adjusted_end = end_slot - current_slot
                    if telemetry_state is not None and not telemetry_state.connected:
                        if adjusted_start == 0 and adjusted_end > 1:
                            adjusted_start = 1
                        elif adjusted_start == 0 and adjusted_end == 1:
                            continue
                    future_windows.append(
                        TimeWindow(start_slot=adjusted_start, end_slot=adjusted_end)
                    )

            if not future_windows:
                continue

            vehicle.availability_windows = future_windows
            vehicle.departure_slot -= current_slot
            if (
                telemetry_state is not None
                and telemetry_state.connected_charger_id is not None
                and any(window.start_slot == 0 for window in future_windows)
            ):
                required_charger_by_vehicle_slot[(vehicle.vehicle_id, 0)] = (
                    telemetry_state.connected_charger_id
                )
            remaining_vehicles.append(vehicle)

        if not remaining_vehicles:
            msg = "No vehicles remain available for the rolling-horizon window"
            raise ValueError(msg)

        trimmed.vehicles = remaining_vehicles
        trimmed.metadata.scenario_name = (
            f"{scenario.metadata.scenario_name}_reopt_slot_{current_slot}"
        )
        return trimmed, current_slot, required_charger_by_vehicle_slot

    @staticmethod
    def _validate_telemetry_against_scenario(
        vehicle_by_id: Mapping[str, Vehicle],
        charger_ids: set[str],
        telemetry: TelemetrySnapshot,
    ) -> None:
        telemetry_vehicle_ids = {
            vehicle_state.vehicle_id for vehicle_state in telemetry.vehicle_states
        }
        unknown_vehicle_ids = sorted(telemetry_vehicle_ids - set(vehicle_by_id))
        if unknown_vehicle_ids:
            msg = f"Telemetry references unknown vehicles: {unknown_vehicle_ids}"
            raise ValueError(msg)

        for vehicle_state in telemetry.vehicle_states:
            vehicle = vehicle_by_id[vehicle_state.vehicle_id]
            if vehicle_state.observed_energy_kwh > vehicle.battery_capacity_kwh:
                msg = f"Telemetry energy for vehicle {vehicle.vehicle_id} exceeds battery capacity"
                raise ValueError(msg)
            if (
                vehicle_state.connected_charger_id is not None
                and vehicle_state.connected_charger_id not in charger_ids
            ):
                msg = (
                    f"Telemetry references unknown charger "
                    f"{vehicle_state.connected_charger_id} for vehicle {vehicle.vehicle_id}"
                )
                raise ValueError(msg)
            if (
                vehicle_state.connected_charger_id is not None
                and vehicle.compatible_charger_ids is not None
                and vehicle_state.connected_charger_id not in vehicle.compatible_charger_ids
            ):
                msg = (
                    f"Telemetry charger {vehicle_state.connected_charger_id} for vehicle "
                    f"{vehicle.vehicle_id} is not in compatible_charger_ids"
                )
                raise ValueError(msg)

    @staticmethod
    def _rebase_result_slots(
        result: OptimizationResult,
        current_slot: int,
        snapshot_id: str,
    ) -> OptimizationResult:
        rebased = result.model_copy(deep=True)
        rebased.telemetry_snapshot_id = snapshot_id
        for assignment in rebased.assignments:
            assignment.slot += current_slot
        for slot_summary in rebased.site_summary:
            slot_summary.slot += current_slot
        return rebased
