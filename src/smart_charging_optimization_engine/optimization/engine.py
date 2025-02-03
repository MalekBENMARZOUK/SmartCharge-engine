from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

from ortools.linear_solver import pywraplp

from smart_charging_optimization_engine.config import settings
from smart_charging_optimization_engine.domain.results import (
    ObjectiveBreakdown,
    OptimizationResult,
    SiteSlotSummary,
    SolverStatus,
    SolveStatistics,
    VehicleSummary,
)
from smart_charging_optimization_engine.exceptions import OptimizationError
from smart_charging_optimization_engine.optimization._model_builder import (
    SiteVariables,
    add_site_constraints,
    build_site_objective_terms,
    compute_objective_breakdown,
    create_site_variables,
    extract_assignments,
    extract_site_summary,
    extract_vehicle_summaries,
)
from smart_charging_optimization_engine.optimization._solver_utils import (
    compute_optimality_gap,
    map_solver_status,
    require_finite,
    require_optional_finite,
)
from smart_charging_optimization_engine.services.result_analysis import (
    build_infeasibility_diagnostics,
    build_single_site_insights,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smart_charging_optimization_engine.domain.models import ChargingScenario


@dataclass(frozen=True)
class SolverConfig:
    backend: str = settings.default_solver_backend
    time_limit_seconds: float = settings.default_solver_time_limit_seconds


class SmartChargingOptimizer:
    def __init__(self, solver_config: SolverConfig | None = None) -> None:
        self._solver_config = solver_config or SolverConfig()

    @property
    def solver_config(self) -> SolverConfig:
        return self._solver_config

    def solve(
        self,
        scenario: ChargingScenario,
        required_charger_by_vehicle_slot: Mapping[tuple[str, int], str] | None = None,
    ) -> OptimizationResult:
        solver = self._create_solver()
        sv = self._create_variables(solver, scenario)
        self._add_constraints(
            solver,
            sv,
            scenario,
            required_charger_by_vehicle_slot=required_charger_by_vehicle_slot,
        )
        self._set_objective(solver, sv, scenario)
        status, solve_time = self._run_solver(solver)

        if status in {SolverStatus.infeasible, SolverStatus.not_solved}:
            return self._build_unsolved_result(scenario, status, solve_time)

        return self._extract_result(solver, sv, scenario, status, solve_time)

    def _create_solver(self) -> pywraplp.Solver:
        solver = pywraplp.Solver.CreateSolver(self._solver_config.backend)
        if solver is None:
            msg = f"Unsupported solver backend: {self._solver_config.backend}"
            raise OptimizationError(msg)
        solver.SetTimeLimit(int(self._solver_config.time_limit_seconds * 1000))
        return solver

    @staticmethod
    def _create_variables(
        solver: pywraplp.Solver,
        scenario: ChargingScenario,
    ) -> SiteVariables:
        return create_site_variables(
            solver,
            site=scenario.site,
            vehicles=scenario.vehicles,
            chargers=scenario.chargers,
            objective=scenario.objective,
        )

    @staticmethod
    def _add_constraints(
        solver: pywraplp.Solver,
        sv: SiteVariables,
        scenario: ChargingScenario,
        required_charger_by_vehicle_slot: Mapping[tuple[str, int], str] | None = None,
    ) -> None:
        add_site_constraints(
            solver,
            sv,
            site=scenario.site,
            vehicles=scenario.vehicles,
            chargers=scenario.chargers,
            objective=scenario.objective,
            required_charger_by_vehicle_slot=required_charger_by_vehicle_slot,
        )

    @staticmethod
    def _set_objective(
        solver: pywraplp.Solver,
        sv: SiteVariables,
        scenario: ChargingScenario,
    ) -> None:
        terms = build_site_objective_terms(
            sv,
            site=scenario.site,
            vehicles=scenario.vehicles,
            chargers=scenario.chargers,
            objective=scenario.objective,
            priority_rules=scenario.fleet_priority_rules,
        )
        solver.Minimize(
            terms["electricity_cost"]
            - terms["export_revenue"]
            + terms["unmet_penalty"]
            + terms["ramp_penalty"]
            + terms["demand_charge"]
            + terms["degradation"]
        )

    @staticmethod
    def _run_solver(solver: pywraplp.Solver) -> tuple[SolverStatus, float]:
        start = perf_counter()
        code = solver.Solve()
        elapsed = perf_counter() - start
        return map_solver_status(code), elapsed

    @staticmethod
    def _extract_result(
        solver: pywraplp.Solver,
        sv: SiteVariables,
        scenario: ChargingScenario,
        status: SolverStatus,
        solve_time: float,
    ) -> OptimizationResult:
        time_step_hours = scenario.site.time_step_minutes / 60.0

        assignments = extract_assignments(
            sv,
            scenario.site.site_id,
            scenario.vehicles,
            scenario.chargers,
            scenario.site.horizon_slots,
            time_step_hours,
        )
        vehicle_summaries = extract_vehicle_summaries(
            sv,
            scenario.site.site_id,
            scenario.vehicles,
            scenario.chargers,
            scenario.site.horizon_slots,
            time_step_hours,
        )
        site_summary = extract_site_summary(sv, scenario.site)

        objective_value = require_finite(solver.Objective().Value(), "objective value")
        best_bound = require_optional_finite(solver.Objective().BestBound(), "best bound")

        return OptimizationResult(
            status=status,
            scenario_name=scenario.metadata.scenario_name,
            assignments=assignments,
            vehicle_summaries=vehicle_summaries,
            site_summary=site_summary,
            objective_breakdown=compute_objective_breakdown(
                site_summary,
                scenario.vehicles,
                assignments,
                sv,
                scenario.objective,
                scenario.fleet_priority_rules,
                time_step_hours,
                objective_value,
            ),
            statistics=SolveStatistics(
                solve_time_seconds=solve_time,
                objective_value=objective_value,
                best_bound=best_bound,
                optimality_gap_percent=compute_optimality_gap(objective_value, best_bound),
            ),
            insights=build_single_site_insights(scenario, vehicle_summaries, site_summary),
        )

    @staticmethod
    def _build_unsolved_result(
        scenario: ChargingScenario,
        status: SolverStatus,
        solve_time: float,
    ) -> OptimizationResult:
        export_prices = scenario.site.export_price_per_kwh or [0.0] * scenario.site.horizon_slots
        export_limits = scenario.site.export_limit_kw or [0.0] * scenario.site.horizon_slots
        vehicle_summaries = [
            VehicleSummary(
                vehicle_id=v.vehicle_id,
                site_id=scenario.site.site_id,
                initial_energy_kwh=v.initial_energy_kwh,
                target_energy_kwh=v.target_energy_kwh,
                final_energy_kwh=v.initial_energy_kwh,
                unmet_energy_kwh=max(v.target_energy_kwh - v.initial_energy_kwh, 0.0),
                total_energy_delivered_kwh=0.0,
                total_energy_exported_kwh=0.0,
                total_net_energy_delta_kwh=0.0,
            )
            for v in scenario.vehicles
        ]
        site_summary = [
            SiteSlotSummary(
                site_id=scenario.site.site_id,
                slot=slot,
                total_power_kw=0.0,
                electricity_price_per_kwh=scenario.site.electricity_price_per_kwh[slot],
                site_power_limit_kw=scenario.site.power_limit_kw[slot],
                export_price_per_kwh=export_prices[slot],
                site_export_limit_kw=export_limits[slot],
            )
            for slot in range(scenario.site.horizon_slots)
        ]
        return OptimizationResult(
            status=status,
            scenario_name=scenario.metadata.scenario_name,
            assignments=[],
            vehicle_summaries=vehicle_summaries,
            site_summary=site_summary,
            objective_breakdown=ObjectiveBreakdown(
                electricity_cost=0.0,
                export_revenue=0.0,
                unmet_demand_penalty=0.0,
                load_smoothing_penalty=0.0,
                site_demand_charge_cost=0.0,
                battery_degradation_cost=0.0,
                total_cost=0.0,
            ),
            statistics=SolveStatistics(
                solve_time_seconds=solve_time,
                objective_value=0.0,
                best_bound=None,
            ),
            insights=build_single_site_insights(scenario, vehicle_summaries, site_summary),
            infeasibility_diagnostics=build_infeasibility_diagnostics(scenario)
            if status == SolverStatus.infeasible
            else [],
        )
