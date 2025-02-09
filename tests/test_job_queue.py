from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import sleep
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from smart_charging_optimization_engine.domain.jobs import JobStatus
from smart_charging_optimization_engine.exceptions import JobTimeoutError
from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
from smart_charging_optimization_engine.services.job_queue import OptimizationJobService
from smart_charging_optimization_engine.storage.sql_repository import SqlAlchemyStateRepository

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from smart_charging_optimization_engine.domain.models import ChargingScenario
    from smart_charging_optimization_engine.domain.runs import RunKind
    from smart_charging_optimization_engine.storage.base import StateRepository


def test_job_service_retries_failed_job_until_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository_path = tmp_path / "job-retry.db"
    service = OptimizationJobService(
        lambda: SqlAlchemyStateRepository(f"sqlite:///{repository_path}"),
        max_attempts=2,
        retry_backoff_seconds=0.0,
        process_isolation=False,
    )
    job = service.create_solve_job(fixture_scenario)
    call_count = {"count": 0}
    original_solve = SmartChargingOptimizer.solve

    def flaky_solve(self: SmartChargingOptimizer, scenario: ChargingScenario):
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise RuntimeError("temporary failure")
        return original_solve(self, scenario)

    monkeypatch.setattr(SmartChargingOptimizer, "solve", flaky_solve)

    service.execute_job_from_payload(job.job_id)

    repository = SqlAlchemyStateRepository(f"sqlite:///{repository_path}")
    try:
        stored_job = repository.load_job(job.job_id)
    finally:
        repository.close()

    assert stored_job.status == JobStatus.succeeded
    assert stored_job.attempts == 2


def test_job_service_recovers_stale_job_for_retry(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository_path = tmp_path / "job-stale.db"
    service = OptimizationJobService(
        lambda: SqlAlchemyStateRepository(f"sqlite:///{repository_path}"),
        max_attempts=3,
        stale_threshold_seconds=1.0,
        retry_backoff_seconds=0.0,
    )
    job = service.create_solve_job(fixture_scenario)
    repository = SqlAlchemyStateRepository(f"sqlite:///{repository_path}")
    try:
        running_job = repository.load_job(job.job_id).model_copy(
            update={
                "status": JobStatus.running,
                "attempts": 1,
                "started_at": datetime.now(tz=UTC) - timedelta(seconds=10),
                "last_heartbeat_at": datetime.now(tz=UTC) - timedelta(seconds=10),
            },
            deep=True,
        )
        repository.save_job(job.job_id, running_job)
    finally:
        repository.close()

    recovered_jobs = service.recover_stale_jobs()

    assert len(recovered_jobs) == 1
    assert recovered_jobs[0].status == JobStatus.queued


def test_job_service_marks_timeout_when_isolated_attempt_exceeds_limit(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_path = tmp_path / "job-timeout.db"
    service = OptimizationJobService(
        lambda: SqlAlchemyStateRepository(f"sqlite:///{repository_path}"),
        max_attempts=1,
        process_isolation=True,
        timeout_seconds=1.0,
        repository_descriptor={
            "backend": "sql",
            "state_store_dir": str(tmp_path),
            "database_url": f"sqlite:///{repository_path}",
        },
    )
    job = service.create_solve_job(fixture_scenario)

    def force_timeout(job_obj: object, payload: object) -> object:
        raise JobTimeoutError("timed out")

    monkeypatch.setattr(service, "_run_attempt_isolated", force_timeout)

    service.execute_job_from_payload(job.job_id)

    repository = SqlAlchemyStateRepository(f"sqlite:///{repository_path}")
    try:
        stored_job = repository.load_job(job.job_id)
    finally:
        repository.close()

    assert stored_job.status == JobStatus.timed_out


def test_job_service_updates_heartbeat_while_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository_path = tmp_path / "job-heartbeat.db"
    database_url = f"sqlite:///{repository_path}"
    service = OptimizationJobService(
        lambda: SqlAlchemyStateRepository(database_url),
        max_attempts=1,
        retry_backoff_seconds=0.0,
        heartbeat_interval_seconds=0.05,
        process_isolation=False,
    )
    job = service.create_solve_job(fixture_scenario)
    heartbeat_observed = {"value": False}
    original_run_attempt_inline = service._run_attempt_inline

    def slow_run_attempt(
        repository: StateRepository,
        run_kind: RunKind,
        payload: dict[str, Any],
    ) -> object:
        baseline_repository = SqlAlchemyStateRepository(database_url)
        try:
            baseline_heartbeat = baseline_repository.load_job(job.job_id).last_heartbeat_at
        finally:
            baseline_repository.close()
        for _ in range(10):
            sleep(0.03)
            probe_repository = SqlAlchemyStateRepository(database_url)
            try:
                current_job = probe_repository.load_job(job.job_id)
            finally:
                probe_repository.close()
            if (
                baseline_heartbeat is not None
                and current_job.last_heartbeat_at is not None
                and current_job.last_heartbeat_at > baseline_heartbeat
            ):
                heartbeat_observed["value"] = True
                break
        return original_run_attempt_inline(repository, run_kind, payload)

    monkeypatch.setattr(service, "_run_attempt_inline", slow_run_attempt)

    service.execute_job_from_payload(job.job_id)

    repository = SqlAlchemyStateRepository(database_url)
    try:
        stored_job = repository.load_job(job.job_id)
    finally:
        repository.close()

    assert heartbeat_observed["value"] is True
    assert stored_job.status == JobStatus.succeeded


def test_job_service_does_not_start_job_before_retry_after(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository_path = tmp_path / "job-retry-after.db"
    database_url = f"sqlite:///{repository_path}"
    service = OptimizationJobService(
        lambda: SqlAlchemyStateRepository(database_url),
        process_isolation=False,
    )
    job = service.create_solve_job(fixture_scenario)
    repository = SqlAlchemyStateRepository(database_url)
    try:
        delayed_job = repository.load_job(job.job_id).model_copy(
            update={"retry_after": datetime.now(tz=UTC) + timedelta(seconds=60)},
            deep=True,
        )
        repository.save_job(job.job_id, delayed_job)
    finally:
        repository.close()

    service.execute_job_from_payload(job.job_id)

    repository = SqlAlchemyStateRepository(database_url)
    try:
        stored_job = repository.load_job(job.job_id)
    finally:
        repository.close()

    assert stored_job.status == JobStatus.queued
    assert stored_job.attempts == 0


def test_job_service_does_not_rerun_succeeded_job(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository_path = tmp_path / "job-succeeded.db"
    database_url = f"sqlite:///{repository_path}"
    service = OptimizationJobService(
        lambda: SqlAlchemyStateRepository(database_url),
        process_isolation=False,
    )
    job = service.create_solve_job(fixture_scenario)

    service.execute_job_from_payload(job.job_id)

    repository = SqlAlchemyStateRepository(database_url)
    try:
        completed_job = repository.load_job(job.job_id)
        original_run_id = completed_job.run_id
        original_attempts = completed_job.attempts
    finally:
        repository.close()

    service.execute_job_from_payload(job.job_id)

    repository = SqlAlchemyStateRepository(database_url)
    try:
        stored_job = repository.load_job(job.job_id)
    finally:
        repository.close()

    assert stored_job.status == JobStatus.succeeded
    assert stored_job.attempts == original_attempts
    assert stored_job.run_id == original_run_id


def test_job_service_lists_only_runnable_queued_jobs(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository_path = tmp_path / "job-runnable.db"
    database_url = f"sqlite:///{repository_path}"
    service = OptimizationJobService(
        lambda: SqlAlchemyStateRepository(database_url),
        process_isolation=False,
    )
    ready_job = service.create_solve_job(fixture_scenario)
    delayed_job = service.create_solve_job(fixture_scenario)
    repository = SqlAlchemyStateRepository(database_url)
    try:
        stored_delayed_job = repository.load_job(delayed_job.job_id).model_copy(
            update={"retry_after": datetime.now(tz=UTC) + timedelta(seconds=60)},
            deep=True,
        )
        repository.save_job(delayed_job.job_id, stored_delayed_job)
    finally:
        repository.close()

    runnable_job_ids = [job.job_id for job in service.list_runnable_jobs()]

    assert runnable_job_ids == [ready_job.job_id]


def test_queue_retry_requires_matching_running_state(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    repository_path = tmp_path / "job-race.db"
    database_url = f"sqlite:///{repository_path}"
    service = OptimizationJobService(
        lambda: SqlAlchemyStateRepository(database_url),
        process_isolation=False,
    )
    job = service.create_solve_job(fixture_scenario)
    execution_token = uuid4().hex
    repository = SqlAlchemyStateRepository(database_url)
    try:
        running_job = repository.load_job(job.job_id).model_copy(
            update={
                "status": JobStatus.running,
                "attempts": 1,
                "started_at": datetime.now(tz=UTC) - timedelta(seconds=10),
                "last_heartbeat_at": datetime.now(tz=UTC) - timedelta(seconds=10),
                "execution_token": execution_token,
            },
            deep=True,
        )
        repository.save_job(job.job_id, running_job)
        completed_job = running_job.model_copy(
            update={
                "status": JobStatus.succeeded,
                "completed_at": datetime.now(tz=UTC),
                "execution_token": None,
                "run_id": "solve-20260401000000000000-done1234",
            },
            deep=True,
        )
        repository.save_job(job.job_id, completed_job)
    finally:
        repository.close()

    recovered = service._queue_retry(
        job.job_id,
        "stale heartbeat",
        expected_execution_token=execution_token,
        expected_status=JobStatus.running,
    )

    repository = SqlAlchemyStateRepository(database_url)
    try:
        stored_job = repository.load_job(job.job_id)
    finally:
        repository.close()

    assert recovered is None
    assert stored_job.status == JobStatus.succeeded
