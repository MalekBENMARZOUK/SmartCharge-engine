from __future__ import annotations

import pytest
from pydantic import ValidationError

from smart_charging_optimization_engine.domain.models import (
    ChargingScenario,
    PortfolioScenario,
    TelemetryVehicleState,
)


def test_overlapping_availability_windows_are_rejected() -> None:
    payload = {
        "metadata": {"scenario_name": "invalid", "description": ""},
        "site": {
            "time_step_minutes": 15,
            "horizon_slots": 4,
            "power_limit_kw": [100, 100, 100, 100],
            "electricity_price_per_kwh": [0.1, 0.1, 0.1, 0.1],
        },
        "chargers": [{"charger_id": "C1", "max_power_kw": 50, "efficiency": 0.95}],
        "vehicles": [
            {
                "vehicle_id": "V1",
                "battery_capacity_kwh": 200,
                "initial_energy_kwh": 80,
                "target_energy_kwh": 120,
                "minimum_energy_kwh": 60,
                "max_charging_power_kw": 50,
                "availability_windows": [
                    {"start_slot": 0, "end_slot": 2},
                    {"start_slot": 1, "end_slot": 3},
                ],
                "departure_slot": 3,
                "priority": "normal",
            }
        ],
    }

    with pytest.raises(ValidationError):
        ChargingScenario.model_validate(payload)


def test_connected_vehicle_state_requires_charger_id() -> None:
    with pytest.raises(ValidationError):
        TelemetryVehicleState.model_validate(
            {
                "vehicle_id": "V1",
                "observed_energy_kwh": 25.0,
                "connected": True,
            }
        )


def test_portfolio_rejects_inconsistent_v2g_configuration() -> None:
    payload = {
        "metadata": {"scenario_name": "portfolio", "description": ""},
        "sites": [
            {
                "site": {
                    "site_id": "A",
                    "time_step_minutes": 15,
                    "horizon_slots": 2,
                    "power_limit_kw": [100, 100],
                    "electricity_price_per_kwh": [0.2, 0.2],
                },
                "chargers": [{"charger_id": "C1", "max_power_kw": 50, "site_id": "A"}],
                "vehicles": [
                    {
                        "vehicle_id": "V1",
                        "battery_capacity_kwh": 100,
                        "initial_energy_kwh": 40,
                        "target_energy_kwh": 60,
                        "minimum_energy_kwh": 30,
                        "max_charging_power_kw": 40,
                        "availability_windows": [{"start_slot": 0, "end_slot": 2}],
                        "departure_slot": 2,
                    }
                ],
                "objective": {"allow_vehicle_to_grid": False},
            },
            {
                "site": {
                    "site_id": "B",
                    "time_step_minutes": 15,
                    "horizon_slots": 2,
                    "power_limit_kw": [100, 100],
                    "electricity_price_per_kwh": [0.2, 0.2],
                },
                "chargers": [{"charger_id": "C2", "max_power_kw": 50, "site_id": "B"}],
                "vehicles": [
                    {
                        "vehicle_id": "V2",
                        "battery_capacity_kwh": 100,
                        "initial_energy_kwh": 40,
                        "target_energy_kwh": 60,
                        "minimum_energy_kwh": 30,
                        "max_charging_power_kw": 40,
                        "availability_windows": [{"start_slot": 0, "end_slot": 2}],
                        "departure_slot": 2,
                    }
                ],
                "objective": {"allow_vehicle_to_grid": True},
            },
        ],
    }

    with pytest.raises(ValidationError):
        PortfolioScenario.model_validate(payload)
