from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from smart_charging_optimization_engine.api.app import create_app
from smart_charging_optimization_engine.config import settings
from smart_charging_optimization_engine.domain.results import SolverStatus
from smart_charging_optimization_engine.optimization._solver_utils import (
    build_availability_lookup,
    compute_optimality_gap,
    map_solver_status,
    require_finite,
    require_optional_finite,
)
from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
from smart_charging_optimization_engine.optimization.multisite import (
    MultiSiteSmartChargingOptimizer,
)
from smart_charging_optimization_engine.services.result_analysis import (
    build_infeasibility_diagnostics,
)

if TYPE_CHECKING:
    from pathlib import Path

    from smart_charging_optimization_engine.domain.models import (
        ChargingScenario,
        PortfolioScenario,
        TelemetrySnapshot,
    )


def test_compute_optimality_gap_returns_zero_for_optimal() -> None:
    assert compute_optimality_gap(10.0, 10.0) == 0.0


def test_compute_optimality_gap_returns_percentage() -> None:
    gap = compute_optimality_gap(100.0, 95.0)
    assert gap is not None
    assert abs(gap - 5.0) < 0.01


def test_compute_optimality_gap_none_when_no_bound() -> None:
    assert compute_optimality_gap(10.0, None) is None


def test_require_finite_raises_on_none() -> None:
    with pytest.raises(Exception, match="no value"):
        require_finite(None, "test")


def test_require_finite_passes_for_normal_value() -> None:
    assert require_finite(3.14, "test") == 3.14


def test_require_optional_finite_allows_none() -> None:
    assert require_optional_finite(None, "test") is None


def test_map_solver_status_maps_known_codes() -> None:
    from ortools.linear_solver import pywraplp

    assert map_solver_status(pywraplp.Solver.OPTIMAL) == SolverStatus.optimal
    assert map_solver_status(pywraplp.Solver.INFEASIBLE) == SolverStatus.infeasible
    assert map_solver_status(999) == SolverStatus.not_solved


def test_build_availability_lookup_for_vehicle(fixture_scenario: ChargingScenario) -> None:
    vehicle = fixture_scenario.vehicles[0]
    slots = build_availability_lookup(vehicle, fixture_scenario.site.horizon_slots)
    assert isinstance(slots, set)
    assert len(slots) > 0


def test_solve_result_includes_optimality_gap(fixture_scenario: ChargingScenario) -> None:
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_scenario)
    assert result.statistics.optimality_gap_percent is not None
    assert result.statistics.optimality_gap_percent >= 0.0


def test_multisite_result_includes_optimality_gap(fixture_portfolio: PortfolioScenario) -> None:
    optimizer = MultiSiteSmartChargingOptimizer()
    result = optimizer.solve(fixture_portfolio)
    assert result.statistics.optimality_gap_percent is not None
    assert result.statistics.optimality_gap_percent >= 0.0


def test_run_id_has_uuid_suffix(fixture_scenario: ChargingScenario) -> None:
    client = TestClient(create_app())
    response = client.post("/solve", json=fixture_scenario.model_dump(mode="json"))
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    parts = run_id.split("-")
    assert len(parts) >= 3, f"Expected at least 3 parts in run_id, got {run_id}"


def test_job_id_has_uuid_suffix(
    sql_settings: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    client = TestClient(create_app())
    response = client.post(
        "/jobs/solve",
        json=fixture_scenario.model_dump(mode="json"),
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert job_id.startswith("job-solve-")
    parts = job_id.split("-")
    assert len(parts) >= 4, f"Expected UUID suffix in job_id, got {job_id}"


def test_infeasibility_diagnostics_detects_insufficient_capacity(
    fixture_scenario: ChargingScenario,
) -> None:
    impossible = fixture_scenario.model_copy(deep=True)
    impossible.vehicles[0].target_energy_kwh = impossible.vehicles[0].battery_capacity_kwh
    impossible.vehicles[0].departure_slot = 1
    impossible.vehicles[0].availability_windows[0].end_slot = 1

    diagnostics = build_infeasibility_diagnostics(impossible)
    assert len(diagnostics) >= 1
    bus1_diag = [d for d in diagnostics if d.vehicle_id == impossible.vehicles[0].vehicle_id]
    assert any("Insufficient" in d.issue or "capacity" in d.issue.lower() for d in bus1_diag)


def test_infeasibility_diagnostics_empty_for_feasible(
    fixture_scenario: ChargingScenario,
) -> None:
    diagnostics = build_infeasibility_diagnostics(fixture_scenario)
    assert len(diagnostics) == 0


def test_pagination_on_scenarios_endpoint(
    sql_settings: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    client = TestClient(create_app())
    for i in range(3):
        client.post(
            f"/storage/scenarios/scenario-{i}",
            json=fixture_scenario.model_dump(mode="json"),
        )
    response = client.get("/storage/scenarios?offset=1&limit=1")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["scenario_ids"]) == 1


def test_pagination_on_runs_endpoint(
    sql_settings: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    client = TestClient(create_app())
    client.post("/solve", json=fixture_scenario.model_dump(mode="json"))
    client.post("/solve", json=fixture_scenario.model_dump(mode="json"))

    response = client.get("/runs?offset=0&limit=1")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["runs"]) == 1


def test_job_queue_depth_limit(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smart_charging_optimization_engine.services.job_queue import OptimizationJobService
    from smart_charging_optimization_engine.storage.sql_repository import SqlAlchemyStateRepository

    db_path = tmp_path / "queue-depth.db"
    service = OptimizationJobService(
        lambda: SqlAlchemyStateRepository(f"sqlite:///{db_path}"),
        max_queue_depth=2,
        process_isolation=False,
    )
    service.create_solve_job(fixture_scenario)
    service.create_solve_job(fixture_scenario)
    with pytest.raises(ValueError, match="queue depth limit"):
        service.create_solve_job(fixture_scenario)


def test_config_rejects_excessive_solver_timeout() -> None:
    from pydantic import ValidationError

    from smart_charging_optimization_engine.config import AppSettings

    with pytest.raises(ValidationError, match="less than or equal to 3600"):
        AppSettings(default_solver_time_limit_seconds=7200.0)


def test_config_rejects_excessive_job_timeout() -> None:
    from pydantic import ValidationError

    from smart_charging_optimization_engine.config import AppSettings

    with pytest.raises(ValidationError, match="less than or equal to 7200"):
        AppSettings(job_timeout_seconds=10000.0)


def test_count_scenarios_returns_correct_count(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    from smart_charging_optimization_engine.storage.sql_repository import SqlAlchemyStateRepository

    repo = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'count.db'}")
    assert repo.count_scenarios() == 0
    repo.save_scenario("s1", fixture_scenario)
    assert repo.count_scenarios() == 1
    repo.save_scenario("s2", fixture_scenario)
    assert repo.count_scenarios() == 2
    repo.close()


def test_delete_run_via_api(
    sql_settings: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    client = TestClient(create_app())
    solve_response = client.post("/solve", json=fixture_scenario.model_dump(mode="json"))
    run_id = solve_response.json()["run_id"]

    delete_response = client.delete(f"/runs/{run_id}")
    assert delete_response.status_code == 204

    get_response = client.get(f"/runs/{run_id}")
    assert get_response.status_code == 404


def test_delete_job_via_api(
    sql_settings: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    client = TestClient(create_app())
    enqueue_response = client.post("/jobs/solve", json=fixture_scenario.model_dump(mode="json"))
    job_id = enqueue_response.json()["job_id"]

    delete_response = client.delete(f"/jobs/{job_id}")
    assert delete_response.status_code == 204

    get_response = client.get(f"/jobs/{job_id}")
    assert get_response.status_code == 404


def test_delete_running_job_rejected(
    sql_settings: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    from datetime import UTC, datetime

    from smart_charging_optimization_engine.domain.jobs import JobStatus, OptimizationJob
    from smart_charging_optimization_engine.domain.runs import RunKind
    from smart_charging_optimization_engine.storage.sql_repository import SqlAlchemyStateRepository

    client = TestClient(create_app())
    repo = SqlAlchemyStateRepository(settings.database_url)
    running_job = OptimizationJob(
        job_id="job-solve-test-running",
        run_kind=RunKind.solve,
        status=JobStatus.running,
        scenario_name="test",
        submitted_at=datetime.now(tz=UTC),
    )
    repo.save_job(running_job.job_id, running_job)
    repo.close()

    delete_response = client.delete("/jobs/job-solve-test-running")
    assert delete_response.status_code == 400


def test_amqp_consumer_has_shutdown_mechanism() -> None:
    from unittest.mock import MagicMock

    from smart_charging_optimization_engine.messaging.amqp import AmqpTelemetryConsumer

    consumer = AmqpTelemetryConsumer(
        service=MagicMock(),
        broker_url="amqp://localhost/",
        queue_name="test",
    )
    assert not consumer._shutdown_event.is_set()
    consumer.request_shutdown()
    assert consumer._shutdown_event.is_set()


def test_jobs_list_includes_total(
    sql_settings: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    client = TestClient(create_app())
    client.post("/jobs/solve", json=fixture_scenario.model_dump(mode="json"))
    response = client.get("/jobs?offset=0&limit=50")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert data["total"] >= 1


def test_demand_charge_ignores_negative_power() -> None:
    from unittest.mock import MagicMock

    from smart_charging_optimization_engine.domain.models import (
        FleetPriorityRule,
        ObjectiveConfig,
    )
    from smart_charging_optimization_engine.domain.results import (
        SiteSlotSummary,
    )
    from smart_charging_optimization_engine.optimization._model_builder import (
        compute_objective_breakdown,
    )

    site_summary = [
        SiteSlotSummary(
            site_id="test",
            slot=0,
            total_power_kw=-10.0,
            electricity_price_per_kwh=0.1,
            site_power_limit_kw=100.0,
        ),
        SiteSlotSummary(
            site_id="test",
            slot=1,
            total_power_kw=-5.0,
            electricity_price_per_kwh=0.1,
            site_power_limit_kw=100.0,
        ),
    ]
    vehicle = MagicMock()
    vehicle.vehicle_id = "v1"
    vehicle.priority = "normal"
    sv = MagicMock()
    sv.unmet = {"v1": MagicMock(solution_value=MagicMock(return_value=0.0))}
    objective = ObjectiveConfig(site_demand_charge_per_kw=10.0)
    priority_rules = FleetPriorityRule()
    breakdown = compute_objective_breakdown(
        site_summary,
        [vehicle],
        [],
        sv,
        objective,
        priority_rules,
        time_step_hours=0.25,
        objective_value=0.0,
    )
    assert breakdown.site_demand_charge_cost >= 0.0, (
        "Demand charge must not be negative even when all site power is export"
    )


def test_file_repo_path_uses_validated_id(tmp_path: Path) -> None:
    from smart_charging_optimization_engine.exceptions import InvalidIdentifierError
    from smart_charging_optimization_engine.storage.repository import FileStateRepository

    repo = FileStateRepository(tmp_path)
    with pytest.raises(InvalidIdentifierError):
        repo.load_scenario("../../etc/passwd")


def test_invalid_identifier_returns_400(
    sql_settings: Path,
) -> None:
    client = TestClient(create_app())
    response = client.get("/storage/scenarios/invalid%40id!")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_identifier"


def test_load_all_jobs_returns_all(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    from smart_charging_optimization_engine.services.job_queue import OptimizationJobService
    from smart_charging_optimization_engine.storage.sql_repository import SqlAlchemyStateRepository

    db_path = tmp_path / "batch.db"
    service = OptimizationJobService(
        lambda: SqlAlchemyStateRepository(f"sqlite:///{db_path}"),
        process_isolation=False,
    )
    service.create_solve_job(fixture_scenario)
    service.create_solve_job(fixture_scenario)
    jobs = service.list_jobs()
    assert len(jobs) == 2


def test_load_all_runs_returns_all(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    from smart_charging_optimization_engine.domain.runs import RunKind
    from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
    from smart_charging_optimization_engine.services.run_tracking import OptimizationRunService
    from smart_charging_optimization_engine.storage.sql_repository import SqlAlchemyStateRepository

    db_path = tmp_path / "runs.db"
    repo = SqlAlchemyStateRepository(f"sqlite:///{db_path}")
    run_service = OptimizationRunService(repo)
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_scenario)
    run_service.record_single_site_run(
        RunKind.solve,
        fixture_scenario,
        result,
        optimizer.solver_config,
    )
    run_service.record_single_site_run(
        RunKind.solve,
        fixture_scenario,
        result,
        optimizer.solver_config,
    )
    runs = run_service.list_runs()
    assert len(runs) == 2
    repo.close()


def test_run_service_paginates_without_loading_all_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    from smart_charging_optimization_engine.domain.runs import RunKind
    from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
    from smart_charging_optimization_engine.services.run_tracking import OptimizationRunService
    from smart_charging_optimization_engine.storage.sql_repository import SqlAlchemyStateRepository

    db_path = tmp_path / "paged-runs.db"
    repo = SqlAlchemyStateRepository(f"sqlite:///{db_path}")
    run_service = OptimizationRunService(repo)
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_scenario)
    for _ in range(3):
        run_service.record_single_site_run(
            RunKind.solve,
            fixture_scenario,
            result,
            optimizer.solver_config,
        )

    load_run_calls = {"count": 0}
    original_load_run = repo.load_run

    def tracking_load_run(run_id: str):
        load_run_calls["count"] += 1
        return original_load_run(run_id)

    monkeypatch.setattr(repo, "load_run", tracking_load_run)

    paged_runs = run_service.list_runs(offset=1, limit=1)

    assert len(paged_runs) == 1
    assert load_run_calls["count"] == 1
    repo.close()


def test_config_default_broker_url_has_no_credentials() -> None:
    from smart_charging_optimization_engine.config import AppSettings

    s = AppSettings()
    assert "guest:guest" not in s.telemetry_broker_url


def test_charger_efficiency_affects_delivered_energy(
    fixture_scenario: ChargingScenario,
) -> None:
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_scenario)
    for assignment in result.assignments:
        if assignment.charge_power_kw > 1e-6:
            assert assignment.energy_delivered_kwh <= (
                assignment.charge_power_kw * fixture_scenario.site.time_step_minutes / 60.0 + 1e-6
            )


def test_rolling_horizon_rejects_current_slot_at_horizon(
    fixture_scenario: ChargingScenario,
    fixture_telemetry: TelemetrySnapshot,
) -> None:
    from smart_charging_optimization_engine.domain.models import (
        RollingHorizonRequest,
    )
    from smart_charging_optimization_engine.services.rolling_horizon import (
        RollingHorizonOptimizer,
    )

    bad_telemetry = fixture_telemetry.model_copy(
        update={"current_slot": fixture_scenario.site.horizon_slots}
    )
    optimizer = RollingHorizonOptimizer()
    with pytest.raises(ValueError, match="strictly smaller"):
        optimizer.reoptimize(
            RollingHorizonRequest(scenario=fixture_scenario, telemetry=bad_telemetry)
        )
