from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from smart_charging_optimization_engine.domain.runs import (
    OptimizationRun,
    OptimizationRunDigest,
    RunInputReferences,
    RunKind,
    RunSummary,
)
from smart_charging_optimization_engine.services.result_analysis import (
    compare_multisite_results,
    compare_single_site_results,
)

if TYPE_CHECKING:
    from smart_charging_optimization_engine.domain.models import ChargingScenario, PortfolioScenario
    from smart_charging_optimization_engine.domain.results import (
        MultiSiteOptimizationResult,
        OptimizationResult,
        RunComparison,
    )
    from smart_charging_optimization_engine.optimization.engine import SolverConfig
    from smart_charging_optimization_engine.storage.base import StateRepository


class OptimizationRunService:
    def __init__(self, repository: StateRepository) -> None:
        self._repository = repository

    def record_single_site_run(
        self,
        run_kind: RunKind,
        scenario: ChargingScenario,
        result: OptimizationResult,
        solver_config: SolverConfig,
        telemetry_snapshot_id: str | None = None,
        source_run_id: str | None = None,
    ) -> OptimizationRun:
        run_id = self._build_run_id(run_kind)
        result_with_run_id = result.model_copy(update={"run_id": run_id}, deep=True)
        run = OptimizationRun(
            run_id=run_id,
            run_kind=run_kind,
            created_at=datetime.now(tz=UTC),
            scenario_name=scenario.metadata.scenario_name,
            status=result.status,
            input_references=RunInputReferences(
                scenario_id=scenario.metadata.scenario_id,
                telemetry_snapshot_id=telemetry_snapshot_id,
                source_run_id=source_run_id,
            ),
            solver_backend=solver_config.backend,
            solver_time_limit_seconds=solver_config.time_limit_seconds,
            summary=self._build_single_site_summary(result_with_run_id),
            result=result_with_run_id,
        )
        self._repository.save_run(run_id, run)
        return run

    def record_multisite_run(
        self,
        scenario: PortfolioScenario,
        result: MultiSiteOptimizationResult,
        solver_config: SolverConfig,
    ) -> OptimizationRun:
        run_id = self._build_run_id(RunKind.multisite)
        result_with_run_id = result.model_copy(update={"run_id": run_id}, deep=True)
        run = OptimizationRun(
            run_id=run_id,
            run_kind=RunKind.multisite,
            created_at=datetime.now(tz=UTC),
            scenario_name=scenario.metadata.scenario_name,
            status=result.status,
            input_references=RunInputReferences(scenario_id=scenario.metadata.scenario_id),
            solver_backend=solver_config.backend,
            solver_time_limit_seconds=solver_config.time_limit_seconds,
            summary=self._build_multisite_summary(result_with_run_id),
            multisite_result=result_with_run_id,
        )
        self._repository.save_run(run_id, run)
        return run

    def get_run(self, run_id: str) -> OptimizationRun:
        return self._repository.load_run(run_id)

    def list_runs(
        self,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[OptimizationRunDigest]:
        run_ids = self._repository.list_runs()
        run_ids.sort(key=self._run_id_sort_key, reverse=True)
        selected_run_ids = run_ids[offset:] if limit is None else run_ids[offset : offset + limit]
        runs = [self._repository.load_run(run_id) for run_id in selected_run_ids]
        return [
            OptimizationRunDigest.model_validate(
                run.model_dump(exclude={"result", "multisite_result"})
            )
            for run in runs
        ]

    def count_runs(self) -> int:
        return self._repository.count_runs()

    def compare_runs(self, baseline_run_id: str, candidate_run_id: str) -> RunComparison:
        baseline_run = self.get_run(baseline_run_id)
        candidate_run = self.get_run(candidate_run_id)
        if baseline_run.result is not None and candidate_run.result is not None:
            return compare_single_site_results(
                baseline_run_id,
                candidate_run_id,
                baseline_run.result,
                candidate_run.result,
            )
        if baseline_run.multisite_result is not None and candidate_run.multisite_result is not None:
            return compare_multisite_results(
                baseline_run_id,
                candidate_run_id,
                baseline_run.multisite_result,
                candidate_run.multisite_result,
            )
        msg = "Runs must both be single-site or both be multi-site to compare"
        raise ValueError(msg)

    @staticmethod
    def _build_run_id(run_kind: RunKind) -> str:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S%f")
        short_uuid = uuid4().hex[:8]
        return f"{run_kind.value}-{timestamp}-{short_uuid}"

    @staticmethod
    def _run_id_sort_key(run_id: str) -> tuple[str, str]:
        parts = run_id.split("-", 2)
        if len(parts) == 3 and parts[1].isdigit():
            return parts[1], parts[2]
        return "", run_id

    @staticmethod
    def _build_single_site_summary(result: OptimizationResult) -> RunSummary:
        unmet_energy_kwh = sum(vehicle.unmet_energy_kwh for vehicle in result.vehicle_summaries)
        return RunSummary(
            total_cost=result.objective_breakdown.total_cost,
            solve_time_seconds=result.statistics.solve_time_seconds,
            vehicle_count=len(result.vehicle_summaries),
            unmet_vehicle_count=sum(
                1 for vehicle in result.vehicle_summaries if vehicle.unmet_energy_kwh > 1e-6
            ),
            unmet_energy_kwh=unmet_energy_kwh,
            at_risk_vehicle_count=(
                len(result.insights.at_risk_vehicle_ids) if result.insights else 0
            ),
            peak_site_power_kw=max(
                (max(slot.total_power_kw, 0.0) for slot in result.site_summary),
                default=0.0,
            ),
        )

    @classmethod
    def _build_multisite_summary(cls, result: MultiSiteOptimizationResult) -> RunSummary:
        all_vehicle_summaries = [
            vehicle
            for site_result in result.site_results
            for vehicle in site_result.vehicle_summaries
        ]
        unmet_energy_kwh = sum(vehicle.unmet_energy_kwh for vehicle in all_vehicle_summaries)
        peak_site_power_kw = max(
            (
                max(
                    (max(slot.total_power_kw, 0.0) for slot in site_result.site_summary),
                    default=0.0,
                )
                for site_result in result.site_results
            ),
            default=0.0,
        )
        peak_network_power_kw = max(
            (max(slot.total_power_kw, 0.0) for slot in result.network_summary),
            default=0.0,
        )
        return RunSummary(
            total_cost=result.objective_breakdown.total_cost,
            solve_time_seconds=result.statistics.solve_time_seconds,
            vehicle_count=len(all_vehicle_summaries),
            unmet_vehicle_count=sum(
                1 for vehicle in all_vehicle_summaries if vehicle.unmet_energy_kwh > 1e-6
            ),
            unmet_energy_kwh=unmet_energy_kwh,
            at_risk_vehicle_count=(
                len(result.insights.at_risk_vehicle_ids) if result.insights else 0
            ),
            peak_site_power_kw=peak_site_power_kw,
            peak_network_power_kw=peak_network_power_kw,
        )
