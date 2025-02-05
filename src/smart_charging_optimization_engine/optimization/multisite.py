from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING, Any

from ortools.linear_solver import pywraplp

from smart_charging_optimization_engine.domain.results import (
    MultiSiteOptimizationResult,
    NetworkSlotSummary,
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
from smart_charging_optimization_engine.optimization._solver_utils import (
    expr as _expr,
)
from smart_charging_optimization_engine.optimization._solver_utils import (
    sum_expr as _sum_expr,
)
from smart_charging_optimization_engine.optimization.engine import SolverConfig
from smart_charging_optimization_engine.services.result_analysis import (
    build_infeasibility_diagnostics,
    build_multisite_insights,
    build_single_site_insights,
)

if TYPE_CHECKING:
    from smart_charging_optimization_engine.domain.models import (
        PortfolioScenario,
    )


class MultiSiteSmartChargingOptimizer:
    def __init__(self, solver_config: SolverConfig | None = None) -> None:
        self._solver_config = solver_config or SolverConfig()

    @property
    def solver_config(self) -> SolverConfig:
        return self._solver_config

    def solve(self, scenario: PortfolioScenario) -> MultiSiteOptimizationResult:
        solver = self._create_solver()

        horizon_slots = scenario.sites[0].site.horizon_slots
        slots = range(horizon_slots)

        site_vars: dict[str, SiteVariables] = {}
        for site_scenario in scenario.sites:
            site_id = site_scenario.site.site_id
            sv = create_site_variables(
                solver,
                site_scenario.site,
                site_scenario.vehicles,
                site_scenario.chargers,
                site_scenario.objective,
                prefix=f"{site_id}_",
            )
            add_site_constraints(
                solver,
                sv,
                site_scenario.site,
                site_scenario.vehicles,
                site_scenario.chargers,
                site_scenario.objective,
                prefix=f"{site_id}_",
            )
            site_vars[site_id] = sv

        network_peak_var, network_net_power_vars = self._add_network_constraints(
            solver,
            scenario,
            site_vars,
            slots,
        )

        self._set_objective(solver, scenario, site_vars, network_peak_var)

        status, solve_time = self._run_solver(solver)

        if status in {SolverStatus.infeasible, SolverStatus.not_solved}:
            return self._build_unsolved_result(scenario, status, solve_time)

        return self._extract_result(
            solver,
            scenario,
            site_vars,
            network_net_power_vars,
            network_peak_var,
            status,
            solve_time,
        )

    def _create_solver(self) -> pywraplp.Solver:
        solver = pywraplp.Solver.CreateSolver(self._solver_config.backend)
        if solver is None:
            msg = f"Unsupported solver backend: {self._solver_config.backend}"
            raise OptimizationError(msg)
        solver.SetTimeLimit(int(self._solver_config.time_limit_seconds * 1000))
        return solver

    @staticmethod
    def _add_network_constraints(
        solver: pywraplp.Solver,
        scenario: PortfolioScenario,
        site_vars: dict[str, SiteVariables],
        slots: range,
    ) -> tuple[pywraplp.Variable | None, dict[int, pywraplp.Variable]]:
        network_export_limits = [
            sum(
                site.site.export_limit_kw[slot] if site.site.export_limit_kw is not None else 0.0
                for site in scenario.sites
            )
            for slot in slots
        ]

        network_peak_var: pywraplp.Variable | None = None
        if scenario.network.demand_charge_per_kw > 0.0:
            network_peak_var = solver.NumVar(0.0, solver.infinity(), "network_peak_power")

        network_net_power_vars: dict[int, pywraplp.Variable] = {}
        for slot in slots:
            network_import_limit = (
                scenario.network.power_limit_kw[slot]
                if scenario.network.power_limit_kw is not None
                else solver.infinity()
            )
            network_import = solver.NumVar(0.0, network_import_limit, f"network_import_{slot}")
            network_export = solver.NumVar(
                0.0,
                network_export_limits[slot],
                f"network_export_{slot}",
            )
            network_net = solver.NumVar(
                -network_export_limits[slot],
                network_import_limit,
                f"network_net_{slot}",
            )
            network_state = solver.BoolVar(f"network_import_state_{slot}")
            network_net_power_vars[slot] = network_net

            solver.Add(
                network_net
                == _sum_expr(
                    site_vars[site.site.site_id].net_power[slot] for site in scenario.sites
                )
            )
            solver.Add(network_import - network_export == network_net)
            solver.Add(network_import <= network_import_limit * network_state)
            solver.Add(network_export <= network_export_limits[slot] * (1 - network_state))

            if network_peak_var is not None:
                solver.Add(network_peak_var >= network_import)

        return network_peak_var, network_net_power_vars

    @staticmethod
    def _set_objective(
        solver: pywraplp.Solver,
        scenario: PortfolioScenario,
        site_vars: dict[str, SiteVariables],
        network_peak_var: pywraplp.Variable | None,
    ) -> None:
        total_terms: dict[str, Any] = {
            "electricity_cost": _expr(0.0),
            "export_revenue": _expr(0.0),
            "unmet_penalty": _expr(0.0),
            "ramp_penalty": _expr(0.0),
            "demand_charge": _expr(0.0),
            "degradation": _expr(0.0),
        }
        for site_scenario in scenario.sites:
            terms = build_site_objective_terms(
                site_vars[site_scenario.site.site_id],
                site=site_scenario.site,
                vehicles=site_scenario.vehicles,
                chargers=site_scenario.chargers,
                objective=site_scenario.objective,
                priority_rules=site_scenario.fleet_priority_rules,
            )
            for key in total_terms:
                total_terms[key] = _expr(total_terms[key]) + _expr(terms[key])

        network_demand_charge = (
            _expr(network_peak_var) * scenario.network.demand_charge_per_kw
            if network_peak_var is not None
            else 0.0
        )

        solver.Minimize(
            total_terms["electricity_cost"]
            - total_terms["export_revenue"]
            + total_terms["unmet_penalty"]
            + total_terms["ramp_penalty"]
            + total_terms["demand_charge"]
            + network_demand_charge
            + total_terms["degradation"]
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
        scenario: PortfolioScenario,
        site_vars: dict[str, SiteVariables],
        network_net_power_vars: dict[int, pywraplp.Variable],
        network_peak_var: pywraplp.Variable | None,
        overall_status: SolverStatus,
        solve_time: float,
    ) -> MultiSiteOptimizationResult:
        horizon_slots = scenario.sites[0].site.horizon_slots
        time_step_hours = scenario.sites[0].site.time_step_minutes / 60.0
        slots = range(horizon_slots)

        site_results: list[OptimizationResult] = []
        for site_scenario in scenario.sites:
            site_id = site_scenario.site.site_id
            sv = site_vars[site_id]

            assignments = extract_assignments(
                sv,
                site_id,
                site_scenario.vehicles,
                site_scenario.chargers,
                horizon_slots,
                time_step_hours,
            )
            vehicle_summaries = extract_vehicle_summaries(
                sv,
                site_id,
                site_scenario.vehicles,
                site_scenario.chargers,
                horizon_slots,
                time_step_hours,
            )
            site_summary = extract_site_summary(sv, site_scenario.site)

            site_obj = compute_objective_breakdown(
                site_summary,
                site_scenario.vehicles,
                assignments,
                sv,
                site_scenario.objective,
                site_scenario.fleet_priority_rules,
                time_step_hours,
                0.0,
            )
            site_total = (
                site_obj.electricity_cost
                - site_obj.export_revenue
                + site_obj.unmet_demand_penalty
                + site_obj.load_smoothing_penalty
                + site_obj.site_demand_charge_cost
                + site_obj.battery_degradation_cost
            )
            site_obj = site_obj.model_copy(update={"total_cost": site_total})

            site_results.append(
                OptimizationResult(
                    status=overall_status,
                    scenario_name=f"{scenario.metadata.scenario_name}:{site_id}",
                    assignments=assignments,
                    vehicle_summaries=vehicle_summaries,
                    site_summary=site_summary,
                    objective_breakdown=site_obj,
                    statistics=SolveStatistics(
                        solve_time_seconds=0.0,
                        objective_value=site_total,
                        best_bound=None,
                    ),
                )
            )

        network_summary = [
            NetworkSlotSummary(
                slot=slot,
                total_power_kw=network_net_power_vars[slot].solution_value(),
                network_power_limit_kw=(
                    scenario.network.power_limit_kw[slot]
                    if scenario.network.power_limit_kw is not None
                    else None
                ),
            )
            for slot in slots
        ]

        objective_value = require_finite(solver.Objective().Value(), "objective value")
        best_bound = require_optional_finite(solver.Objective().BestBound(), "best bound")

        network_demand_charge_cost = (
            max((max(s.total_power_kw, 0.0) for s in network_summary), default=0.0)
            * scenario.network.demand_charge_per_kw
        )

        multisite_result = MultiSiteOptimizationResult(
            status=overall_status,
            scenario_name=scenario.metadata.scenario_name,
            site_results=site_results,
            network_summary=network_summary,
            objective_breakdown=ObjectiveBreakdown(
                electricity_cost=require_finite(
                    sum(r.objective_breakdown.electricity_cost for r in site_results),
                    "electricity cost",
                ),
                export_revenue=require_finite(
                    sum(r.objective_breakdown.export_revenue for r in site_results),
                    "export revenue",
                ),
                unmet_demand_penalty=require_finite(
                    sum(r.objective_breakdown.unmet_demand_penalty for r in site_results),
                    "unmet demand penalty",
                ),
                load_smoothing_penalty=require_finite(
                    sum(r.objective_breakdown.load_smoothing_penalty for r in site_results),
                    "load smoothing penalty",
                ),
                site_demand_charge_cost=require_finite(
                    sum(r.objective_breakdown.site_demand_charge_cost for r in site_results),
                    "site demand charge cost",
                ),
                network_demand_charge_cost=require_finite(
                    network_demand_charge_cost,
                    "network demand charge cost",
                ),
                battery_degradation_cost=require_finite(
                    sum(r.objective_breakdown.battery_degradation_cost for r in site_results),
                    "battery degradation cost",
                ),
                total_cost=objective_value,
            ),
            statistics=SolveStatistics(
                solve_time_seconds=solve_time,
                objective_value=objective_value,
                best_bound=best_bound,
                optimality_gap_percent=compute_optimality_gap(objective_value, best_bound),
            ),
        )

        for site_scenario, site_result in zip(
            scenario.sites,
            multisite_result.site_results,
            strict=True,
        ):
            site_result.insights = build_single_site_insights(
                site_scenario,
                site_result.vehicle_summaries,
                site_result.site_summary,
                network_summary=multisite_result.network_summary,
            )
        multisite_result.insights = build_multisite_insights(scenario, multisite_result)
        return multisite_result

    @staticmethod
    def _build_unsolved_result(
        scenario: PortfolioScenario,
        status: SolverStatus,
        solve_time: float,
    ) -> MultiSiteOptimizationResult:
        horizon_slots = scenario.sites[0].site.horizon_slots
        site_results: list[OptimizationResult] = []
        for site_scenario in scenario.sites:
            export_prices = site_scenario.site.export_price_per_kwh or [0.0] * horizon_slots
            export_limits = site_scenario.site.export_limit_kw or [0.0] * horizon_slots
            vehicle_summaries = [
                VehicleSummary(
                    site_id=site_scenario.site.site_id,
                    vehicle_id=v.vehicle_id,
                    initial_energy_kwh=v.initial_energy_kwh,
                    target_energy_kwh=v.target_energy_kwh,
                    final_energy_kwh=v.initial_energy_kwh,
                    unmet_energy_kwh=max(v.target_energy_kwh - v.initial_energy_kwh, 0.0),
                    total_energy_delivered_kwh=0.0,
                    total_energy_exported_kwh=0.0,
                    total_net_energy_delta_kwh=0.0,
                )
                for v in site_scenario.vehicles
            ]
            site_summary = [
                SiteSlotSummary(
                    site_id=site_scenario.site.site_id,
                    slot=slot,
                    total_power_kw=0.0,
                    electricity_price_per_kwh=site_scenario.site.electricity_price_per_kwh[slot],
                    site_power_limit_kw=site_scenario.site.power_limit_kw[slot],
                    export_price_per_kwh=export_prices[slot],
                    site_export_limit_kw=export_limits[slot],
                )
                for slot in range(horizon_slots)
            ]
            site_results.append(
                OptimizationResult(
                    status=status,
                    scenario_name=f"{scenario.metadata.scenario_name}:{site_scenario.site.site_id}",
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
                        solve_time_seconds=0.0,
                        objective_value=0.0,
                        best_bound=None,
                    ),
                    infeasibility_diagnostics=(
                        build_infeasibility_diagnostics(site_scenario)
                        if status == SolverStatus.infeasible
                        else []
                    ),
                )
            )
        network_summary = [
            NetworkSlotSummary(
                slot=slot,
                total_power_kw=0.0,
                network_power_limit_kw=(
                    scenario.network.power_limit_kw[slot]
                    if scenario.network.power_limit_kw is not None
                    else None
                ),
            )
            for slot in range(horizon_slots)
        ]
        return MultiSiteOptimizationResult(
            status=status,
            scenario_name=scenario.metadata.scenario_name,
            site_results=site_results,
            network_summary=network_summary,
            objective_breakdown=ObjectiveBreakdown(
                electricity_cost=0.0,
                export_revenue=0.0,
                unmet_demand_penalty=0.0,
                load_smoothing_penalty=0.0,
                site_demand_charge_cost=0.0,
                network_demand_charge_cost=0.0,
                battery_degradation_cost=0.0,
                total_cost=0.0,
            ),
            statistics=SolveStatistics(
                solve_time_seconds=solve_time,
                objective_value=0.0,
                best_bound=None,
            ),
        )
