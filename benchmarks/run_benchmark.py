from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from random import Random
from time import perf_counter

from smart_charging_optimization_engine.domain.models import ChargingScenario
from smart_charging_optimization_engine.optimization.engine import (
    SmartChargingOptimizer,
    SolverConfig,
)


def build_synthetic_scenario(
    seed: int,
    vehicles: int,
    chargers: int,
    horizon_slots: int,
) -> ChargingScenario:
    rng = Random(seed)  # noqa: S311
    prices = [0.14 + (0.10 if 28 <= slot <= 40 else 0.0) for slot in range(horizon_slots)]
    site_limit = [600.0 if 26 <= slot <= 42 else 850.0 for slot in range(horizon_slots)]

    payload = {
        "metadata": {
            "scenario_name": f"benchmark_seed_{seed}",
            "description": "Synthetic benchmark scenario",
        },
        "site": {
            "time_step_minutes": 15,
            "horizon_slots": horizon_slots,
            "power_limit_kw": site_limit,
            "electricity_price_per_kwh": prices,
        },
        "chargers": [
            {
                "charger_id": f"C{charger_index + 1:02d}",
                "max_power_kw": 150.0 if charger_index % 3 == 0 else 120.0,
                "efficiency": 0.95,
            }
            for charger_index in range(chargers)
        ],
        "vehicles": [],
        "objective": {
            "unmet_demand_penalty_per_kwh": 180.0,
            "load_smoothing_penalty_per_kw_change": 0.35,
        },
    }

    for vehicle_index in range(vehicles):
        arrival = rng.randint(0, max(0, horizon_slots // 3))
        departure = rng.randint(max(arrival + 8, horizon_slots // 2), horizon_slots)
        battery_capacity = rng.choice([350.0, 420.0, 500.0])
        initial_energy = rng.uniform(0.15, 0.55) * battery_capacity
        target_energy = rng.uniform(0.70, 0.95) * battery_capacity
        payload["vehicles"].append(
            {
                "vehicle_id": f"V{vehicle_index + 1:03d}",
                "battery_capacity_kwh": round(battery_capacity, 2),
                "initial_energy_kwh": round(initial_energy, 2),
                "target_energy_kwh": round(target_energy, 2),
                "minimum_energy_kwh": round(0.10 * battery_capacity, 2),
                "max_charging_power_kw": rng.choice([80.0, 100.0, 120.0]),
                "availability_windows": [{"start_slot": arrival, "end_slot": departure}],
                "departure_slot": departure,
                "priority": rng.choice(["critical", "high", "normal", "normal", "low"]),
            }
        )

    return ChargingScenario.model_validate(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run optimization benchmarks.")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--vehicles", type=int, default=40)
    parser.add_argument("--chargers", type=int, default=12)
    parser.add_argument("--horizon-slots", type=int, default=96)
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument("--output", type=Path, default=Path("artifacts/benchmark_summary.json"))
    args = parser.parse_args()

    solver = SmartChargingOptimizer(SolverConfig(time_limit_seconds=args.time_limit))
    durations: list[float] = []
    objective_values: list[float] = []
    statuses: list[str] = []

    for seed in range(args.runs):
        scenario = build_synthetic_scenario(seed, args.vehicles, args.chargers, args.horizon_slots)
        start = perf_counter()
        result = solver.solve(scenario)
        durations.append(perf_counter() - start)
        objective_values.append(result.objective_breakdown.total_cost)
        statuses.append(result.status.value)

    summary = {
        "runs": args.runs,
        "vehicles": args.vehicles,
        "chargers": args.chargers,
        "horizon_slots": args.horizon_slots,
        "mean_duration_seconds": statistics.mean(durations),
        "p95_duration_seconds": sorted(durations)[max(0, int(0.95 * len(durations)) - 1)],
        "mean_total_cost": statistics.mean(objective_values),
        "statuses": statuses,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
