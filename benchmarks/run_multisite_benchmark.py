from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from random import Random
from time import perf_counter

from smart_charging_optimization_engine.domain.models import PortfolioScenario
from smart_charging_optimization_engine.optimization.engine import SolverConfig
from smart_charging_optimization_engine.optimization.multisite import (
    MultiSiteSmartChargingOptimizer,
)


def build_multisite_portfolio(seed: int, sites: int, vehicles_per_site: int) -> PortfolioScenario:
    rng = Random(seed)  # noqa: S311
    horizon_slots = 48
    site_payloads: list[dict[str, object]] = []
    network_limit = [float(350 + (30 if 16 <= slot <= 24 else 0)) for slot in range(horizon_slots)]

    for site_index in range(sites):
        site_id = f"depot-{site_index + 1:02d}"
        chargers = [
            {
                "charger_id": f"{site_id}-C{charger_index + 1:02d}",
                "site_id": site_id,
                "max_power_kw": 80.0 if charger_index % 2 == 0 else 60.0,
                "efficiency": 0.95,
            }
            for charger_index in range(4)
        ]
        vehicles: list[dict[str, object]] = []
        for vehicle_index in range(vehicles_per_site):
            arrival = rng.randint(0, 12)
            departure = rng.randint(max(arrival + 6, 24), horizon_slots)
            charger_choices = [charger["charger_id"] for charger in chargers]
            compatible = rng.sample(charger_choices, k=rng.randint(1, len(charger_choices)))
            vehicles.append(
                {
                    "vehicle_id": f"{site_id}-V{vehicle_index + 1:03d}",
                    "battery_capacity_kwh": rng.choice([260.0, 320.0, 420.0]),
                    "initial_energy_kwh": rng.uniform(80.0, 180.0),
                    "target_energy_kwh": rng.uniform(180.0, 260.0),
                    "minimum_energy_kwh": 60.0,
                    "max_charging_power_kw": rng.choice([50.0, 60.0, 80.0]),
                    "availability_windows": [{"start_slot": arrival, "end_slot": departure}],
                    "departure_slot": departure,
                    "priority": rng.choice(["critical", "high", "normal", "low"]),
                    "compatible_charger_ids": compatible,
                }
            )

        site_payloads.append(
            {
                "site": {
                    "site_id": site_id,
                    "time_step_minutes": 30,
                    "horizon_slots": horizon_slots,
                    "power_limit_kw": [220.0] * horizon_slots,
                    "electricity_price_per_kwh": [
                        0.11 + (0.06 if 18 <= slot <= 30 else 0.0) for slot in range(horizon_slots)
                    ],
                },
                "chargers": chargers,
                "vehicles": vehicles,
                "objective": {
                    "unmet_demand_penalty_per_kwh": 190.0,
                    "load_smoothing_penalty_per_kw_change": 0.15,
                    "site_demand_charge_per_kw": 0.18,
                },
            }
        )

    return PortfolioScenario.model_validate(
        {
            "metadata": {
                "scenario_name": f"multisite_benchmark_seed_{seed}",
                "description": "Synthetic coordinated multi-site portfolio benchmark",
            },
            "sites": site_payloads,
            "network": {
                "power_limit_kw": network_limit,
                "demand_charge_per_kw": 0.20,
            },
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run coordinated multi-site benchmarks.")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--sites", type=int, default=3)
    parser.add_argument("--vehicles-per-site", type=int, default=12)
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/multisite_benchmark_summary.json"),
    )
    args = parser.parse_args()

    optimizer = MultiSiteSmartChargingOptimizer(SolverConfig(time_limit_seconds=args.time_limit))
    durations: list[float] = []
    objective_values: list[float] = []
    statuses: list[str] = []

    for seed in range(args.runs):
        scenario = build_multisite_portfolio(seed, args.sites, args.vehicles_per_site)
        start = perf_counter()
        result = optimizer.solve(scenario)
        durations.append(perf_counter() - start)
        objective_values.append(result.objective_breakdown.total_cost)
        statuses.append(result.status.value)

    summary = {
        "runs": args.runs,
        "sites": args.sites,
        "vehicles_per_site": args.vehicles_per_site,
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
