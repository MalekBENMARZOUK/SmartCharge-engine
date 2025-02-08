from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from smart_charging_optimization_engine.domain.jobs import JobStatus, OptimizationJob
from smart_charging_optimization_engine.domain.runs import RunKind
from smart_charging_optimization_engine.exceptions import (
    InvalidIdentifierError,
    StorageNotFoundError,
)
from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
from smart_charging_optimization_engine.services.run_tracking import OptimizationRunService
from smart_charging_optimization_engine.storage.repository import FileStateRepository

if TYPE_CHECKING:
    from pathlib import Path

    from smart_charging_optimization_engine.domain.models import (
        ChargingScenario,
        TelemetrySnapshot,
    )


def test_file_state_repository_round_trip(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
    fixture_telemetry: TelemetrySnapshot,
) -> None:
    repository = FileStateRepository(tmp_path)
    repository.save_scenario("scenario-a", fixture_scenario)
    repository.save_telemetry("snapshot-a", fixture_telemetry)

    loaded_scenario = repository.load_scenario("scenario-a")
    loaded_telemetry = repository.load_telemetry("snapshot-a")

    assert loaded_scenario.metadata.scenario_name == fixture_scenario.metadata.scenario_name
    assert loaded_telemetry.snapshot_id == fixture_telemetry.snapshot_id
    assert repository.list_scenarios() == ["scenario-a"]


def test_file_state_repository_run_round_trip(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository = FileStateRepository(tmp_path)
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


def test_file_state_repository_job_round_trip(tmp_path: Path) -> None:
    repository = FileStateRepository(tmp_path)
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


def test_file_state_repository_job_input_round_trip(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository = FileStateRepository(tmp_path)

    repository.save_job_input("job-solve-1", fixture_scenario.model_dump(mode="json"))
    payload = repository.load_job_input("job-solve-1")

    assert payload["metadata"]["scenario_name"] == fixture_scenario.metadata.scenario_name


def test_file_state_repository_rejects_invalid_identifier(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository = FileStateRepository(tmp_path)

    with pytest.raises(InvalidIdentifierError):
        repository.save_scenario("../bad", fixture_scenario)


def test_file_state_repository_missing_item_raises_storage_not_found(tmp_path: Path) -> None:
    repository = FileStateRepository(tmp_path)

    with pytest.raises(StorageNotFoundError):
        repository.load_scenario("missing-scenario")
