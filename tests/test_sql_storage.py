from __future__ import annotations

from datetime import UTC, datetime
from multiprocessing import Queue
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import SQLAlchemyError

from smart_charging_optimization_engine.config import settings
from smart_charging_optimization_engine.domain.jobs import JobStatus, OptimizationJob
from smart_charging_optimization_engine.domain.models import (
    ChargingScenario,
    RollingHorizonRequest,
    TelemetrySnapshot,
)
from smart_charging_optimization_engine.domain.runs import RunKind
from smart_charging_optimization_engine.exceptions import (
    InvalidIdentifierError,
    JsonPayloadError,
    RepositoryError,
    StorageNotFoundError,
)
from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
from smart_charging_optimization_engine.services.job_worker import (
    JobWorkerOutcome,
    execute_job_in_subprocess,
)
from smart_charging_optimization_engine.services.run_tracking import OptimizationRunService
from smart_charging_optimization_engine.storage.factory import repository_descriptor_from_settings
from smart_charging_optimization_engine.storage.sql_repository import (
    Session,
    SqlAlchemyStateRepository,
    StoredDocument,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_sqlalchemy_state_repository_round_trip(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
    fixture_telemetry: TelemetrySnapshot,
) -> None:
    repository = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'state.db'}")
    repository.save_scenario("sql-scenario", fixture_scenario)
    repository.save_telemetry("sql-snapshot", fixture_telemetry)

    loaded_scenario = repository.load_scenario("sql-scenario")
    loaded_telemetry = repository.load_telemetry("sql-snapshot")

    assert loaded_scenario.metadata.scenario_name == fixture_scenario.metadata.scenario_name
    assert loaded_telemetry.snapshot_id == fixture_telemetry.snapshot_id
    assert repository.list_scenarios() == ["sql-scenario"]


def test_sqlalchemy_state_repository_run_round_trip(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'state.db'}")
    optimizer = SmartChargingOptimizer()
    result = optimizer.solve(fixture_scenario)
    run_service = OptimizationRunService(repository)
    run = run_service.record_single_site_run(
        RunKind.solve,
        fixture_scenario,
        result,
        optimizer.solver_config,
    )

    loaded_run = repository.load_run(run.run_id)

    assert loaded_run.run_id == run.run_id
    assert loaded_run.result is not None
    assert loaded_run.result.run_id == run.run_id
    assert repository.list_runs() == [run.run_id]


def test_sqlalchemy_state_repository_job_round_trip(tmp_path: Path) -> None:
    repository = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'state.db'}")
    job = OptimizationJob(
        job_id="job-solve-1",
        run_kind=RunKind.solve,
        status=JobStatus.queued,
        scenario_name="scenario-a",
        submitted_at=datetime.now(UTC),
    )

    repository.save_job(job.job_id, job)
    loaded_job = repository.load_job(job.job_id)

    assert loaded_job.job_id == job.job_id
    assert repository.list_jobs() == [job.job_id]


def test_sqlalchemy_state_repository_job_input_round_trip(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'state.db'}")

    repository.save_job_input("job-solve-1", fixture_scenario.model_dump(mode="json"))
    payload = repository.load_job_input("job-solve-1")

    assert payload["metadata"]["scenario_name"] == fixture_scenario.metadata.scenario_name


def test_sqlalchemy_state_repository_rejects_invalid_identifier(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'state.db'}")

    with pytest.raises(InvalidIdentifierError):
        repository.save_scenario("../bad", fixture_scenario)


def test_sqlalchemy_state_repository_missing_item_raises_storage_not_found(tmp_path: Path) -> None:
    repository = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'state.db'}")

    with pytest.raises(StorageNotFoundError):
        repository.load_scenario("missing-scenario")


def test_sqlalchemy_state_repository_list_wraps_sqlalchemy_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'state.db'}")

    def fail_execute(self: Session, statement: object) -> object:
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(Session, "execute", fail_execute)

    with pytest.raises(RepositoryError, match="Failed to list documents"):
        repository.list_scenarios()


def test_sqlalchemy_state_repository_normalizes_windows_style_sqlite_url(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "state.db"
    windows_style_path = str(database_path).replace("/", "\\")

    repository = SqlAlchemyStateRepository(f"sqlite:///{windows_style_path}")

    try:
        assert database_path.parent.exists()
    finally:
        repository.close()


def test_sqlalchemy_state_repository_load_all_jobs_wraps_invalid_json(tmp_path: Path) -> None:
    repository = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'state.db'}")
    job = OptimizationJob(
        job_id="job-invalid-json",
        run_kind=RunKind.solve,
        status=JobStatus.queued,
        scenario_name="scenario-a",
        submitted_at=datetime.now(UTC),
    )
    repository.save_job(job.job_id, job)

    with Session(repository._engine) as session:
        document = session.get(StoredDocument, {"kind": "job", "item_id": job.job_id})
        assert document is not None
        document.payload_json = "{"
        session.commit()

    with pytest.raises(JsonPayloadError, match="Stored JSON for job documents is invalid"):
        repository.load_all_jobs()


def test_job_worker_reports_success_for_reoptimization(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
    fixture_telemetry: TelemetrySnapshot,
) -> None:
    original_backend = settings.state_repository_backend
    original_database_url = settings.database_url
    settings.state_repository_backend = "sql"
    settings.database_url = f"sqlite:///{tmp_path / 'worker.db'}"
    try:
        queue: Queue[JobWorkerOutcome] = Queue()
        descriptor = repository_descriptor_from_settings()
        request = RollingHorizonRequest(
            scenario=fixture_scenario,
            telemetry=fixture_telemetry,
        )

        execute_job_in_subprocess(
            descriptor,
            RunKind.reoptimize.value,
            request.model_dump(mode="json"),
            queue,
        )

        outcome = queue.get(timeout=1.0)
        assert outcome["status"] == "succeeded"
        assert outcome["run_id"] is not None
    finally:
        settings.state_repository_backend = original_backend
        settings.database_url = original_database_url


def test_job_worker_reports_failure_for_invalid_run_kind(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    original_backend = settings.state_repository_backend
    original_database_url = settings.database_url
    settings.state_repository_backend = "sql"
    settings.database_url = f"sqlite:///{tmp_path / 'worker.db'}"
    try:
        queue: Queue[JobWorkerOutcome] = Queue()
        descriptor = repository_descriptor_from_settings()

        execute_job_in_subprocess(
            descriptor,
            "unknown-kind",
            fixture_scenario.model_dump(mode="json"),
            queue,
        )

        outcome = queue.get(timeout=1.0)
        assert outcome["status"] == "failed"
        assert outcome["error_type"] == "ValueError"
    finally:
        settings.state_repository_backend = original_backend
        settings.database_url = original_database_url
