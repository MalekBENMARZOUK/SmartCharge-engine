from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from smart_charging_optimization_engine.cli import app
from smart_charging_optimization_engine.config import settings
from smart_charging_optimization_engine.domain.jobs import JobStatus
from smart_charging_optimization_engine.exceptions import JsonPayloadError
from smart_charging_optimization_engine.services.job_queue import OptimizationJobService
from smart_charging_optimization_engine.storage.factory import build_state_repository
from smart_charging_optimization_engine.storage.sql_repository import SqlAlchemyStateRepository

if TYPE_CHECKING:
    from pathlib import Path

    from smart_charging_optimization_engine.domain.models import ChargingScenario


def test_validate_command(fixture_scenario_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["validate", str(fixture_scenario_path)])

    assert result.exit_code == 0
    assert "is valid" in result.stdout


def test_export_schemas_command(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["export-schemas", str(tmp_path)])

    assert result.exit_code == 0
    scenario_schema = json.loads((tmp_path / "charging_scenario.schema.json").read_text())
    assert scenario_schema["title"] == "ChargingScenario"


def test_reoptimize_and_store_commands(
    tmp_path: Path,
    fixture_scenario_path: Path,
    fixture_telemetry_path: Path,
) -> None:
    runner = CliRunner()
    result_path = tmp_path / "reoptimized.json"
    reoptimize_result = runner.invoke(
        app,
        [
            "reoptimize",
            str(fixture_scenario_path),
            str(fixture_telemetry_path),
            "--output",
            str(result_path),
        ],
    )

    assert reoptimize_result.exit_code == 0
    assert result_path.exists()

    original_backend = settings.state_repository_backend
    original_database_url = settings.database_url
    settings.state_repository_backend = "sql"
    settings.database_url = f"sqlite:///{tmp_path / 'cli-state.db'}"
    try:
        solve_result = runner.invoke(
            app,
            [
                "solve",
                str(fixture_scenario_path),
                "--output",
                str(tmp_path / "solve.json"),
            ],
        )
        assert solve_result.exit_code == 0
        assert "Run ID:" in solve_result.stdout

        store_result = runner.invoke(
            app,
            [
                "store-scenario",
                str(fixture_scenario_path),
                "--scenario-id",
                "scenario-a",
            ],
        )
        assert store_result.exit_code == 0
    finally:
        settings.state_repository_backend = original_backend
        settings.database_url = original_database_url


def test_solve_portfolio_command(
    tmp_path: Path,
    fixture_portfolio_path: Path,
) -> None:
    runner = CliRunner()
    result_path = tmp_path / "portfolio_result.json"
    result = runner.invoke(
        app,
        [
            "solve-portfolio",
            str(fixture_portfolio_path),
            "--output",
            str(result_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result_path.read_text())
    assert payload["status"] in {"optimal", "feasible"}


def test_ingest_telemetry_envelope_command(
    tmp_path: Path,
    fixture_envelope_path: Path,
) -> None:
    runner = CliRunner()
    original_backend = settings.state_repository_backend
    original_database_url = settings.database_url
    settings.state_repository_backend = "sql"
    settings.database_url = f"sqlite:///{tmp_path / 'cli-ingestion.db'}"
    try:
        result = runner.invoke(
            app,
            ["ingest-telemetry-envelope", str(fixture_envelope_path)],
        )
        assert result.exit_code == 0
        assert "ingested" in result.stdout.lower()
    finally:
        settings.state_repository_backend = original_backend
        settings.database_url = original_database_url


def test_compare_runs_command(
    tmp_path: Path,
    fixture_scenario_path: Path,
    fixture_telemetry_path: Path,
) -> None:
    runner = CliRunner()
    original_backend = settings.state_repository_backend
    original_database_url = settings.database_url
    settings.state_repository_backend = "sql"
    settings.database_url = f"sqlite:///{tmp_path / 'cli-runs.db'}"
    try:
        solve_result = runner.invoke(
            app,
            ["solve", str(fixture_scenario_path), "--output", str(tmp_path / "baseline.json")],
        )
        assert solve_result.exit_code == 0

        reopt_result = runner.invoke(
            app,
            [
                "reoptimize",
                str(fixture_scenario_path),
                str(fixture_telemetry_path),
                "--output",
                str(tmp_path / "candidate.json"),
            ],
        )
        assert reopt_result.exit_code == 0

        repository = build_state_repository()
        try:
            run_ids = repository.list_runs()
        finally:
            repository.close()

        compare_result = runner.invoke(app, ["compare-runs", run_ids[0], run_ids[1]])
        assert compare_result.exit_code == 0
        assert "Run Comparison" in compare_result.stdout
    finally:
        settings.state_repository_backend = original_backend
        settings.database_url = original_database_url


def test_validate_command_rejects_malformed_json(tmp_path: Path) -> None:
    runner = CliRunner()
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{invalid json", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(invalid_path)])

    assert result.exit_code != 0
    assert isinstance(result.exception, JsonPayloadError)


def test_show_metrics_command_supports_prometheus_format() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["show-metrics", "--format", "prometheus"])

    assert result.exit_code == 0


def test_show_metrics_command_rejects_unknown_format() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["show-metrics", "--format", "yaml"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


def test_retry_job_command_reexecutes_failed_job(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    runner = CliRunner()
    original_backend = settings.state_repository_backend
    original_database_url = settings.database_url
    settings.state_repository_backend = "sql"
    settings.database_url = f"sqlite:///{tmp_path / 'cli-retry-job.db'}"
    try:
        service = OptimizationJobService(build_state_repository, process_isolation=False)
        job = service.create_solve_job(fixture_scenario)
        repository = SqlAlchemyStateRepository(settings.database_url)
        try:
            failed_job = repository.load_job(job.job_id).model_copy(
                update={"status": JobStatus.failed, "error_message": "failed earlier"},
                deep=True,
            )
            repository.save_job(job.job_id, failed_job)
        finally:
            repository.close()

        result = runner.invoke(app, ["retry-job", job.job_id])

        assert result.exit_code == 0
        assert f"Retried job {job.job_id}" in result.stdout

        repository = SqlAlchemyStateRepository(settings.database_url)
        try:
            retried_job = repository.load_job(job.job_id)
        finally:
            repository.close()

        assert retried_job.status == JobStatus.succeeded
        assert retried_job.run_id is not None
    finally:
        settings.state_repository_backend = original_backend
        settings.database_url = original_database_url


def test_recover_stale_jobs_command_recovers_and_reschedules(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    runner = CliRunner()
    original_backend = settings.state_repository_backend
    original_database_url = settings.database_url
    original_stale_threshold = settings.job_stale_threshold_seconds
    original_retry_backoff = settings.job_retry_backoff_seconds
    original_process_isolation = settings.job_process_isolation
    settings.state_repository_backend = "sql"
    settings.database_url = f"sqlite:///{tmp_path / 'cli-recover-job.db'}"
    settings.job_stale_threshold_seconds = 1.0
    settings.job_retry_backoff_seconds = 0.0
    settings.job_process_isolation = False
    try:
        service = OptimizationJobService(build_state_repository, process_isolation=False)
        job = service.create_solve_job(fixture_scenario)
        repository = SqlAlchemyStateRepository(settings.database_url)
        try:
            stale_job = repository.load_job(job.job_id).model_copy(
                update={
                    "status": JobStatus.running,
                    "attempts": 1,
                    "started_at": datetime.now(tz=UTC) - timedelta(seconds=10),
                    "last_heartbeat_at": datetime.now(tz=UTC) - timedelta(seconds=10),
                },
                deep=True,
            )
            repository.save_job(job.job_id, stale_job)
        finally:
            repository.close()

        result = runner.invoke(app, ["recover-stale-jobs"])

        assert result.exit_code == 0
        assert "Recovered 1 stale job(s)" in result.stdout

        repository = SqlAlchemyStateRepository(settings.database_url)
        try:
            recovered_job = repository.load_job(job.job_id)
        finally:
            repository.close()

        assert recovered_job.status == JobStatus.succeeded
        assert recovered_job.run_id is not None
    finally:
        settings.state_repository_backend = original_backend
        settings.database_url = original_database_url
        settings.job_stale_threshold_seconds = original_stale_threshold
        settings.job_retry_backoff_seconds = original_retry_backoff
        settings.job_process_isolation = original_process_isolation
