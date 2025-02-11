from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from smart_charging_optimization_engine.api.app import create_app
from smart_charging_optimization_engine.config import settings
from smart_charging_optimization_engine.exceptions import RepositoryError
from smart_charging_optimization_engine.services.job_queue import OptimizationJobService
from smart_charging_optimization_engine.storage.factory import build_state_repository

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from smart_charging_optimization_engine.domain.models import (
        ChargingScenario,
        PortfolioScenario,
        TelemetryMessageEnvelope,
        TelemetrySnapshot,
    )


def test_health_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == settings.api_version
    assert response.json()["repository_backend"] == settings.state_repository_backend
    assert response.json()["job_process_isolation"] == settings.job_process_isolation
    assert response.json()["log_format"] == settings.log_format


def test_solve_endpoint(fixture_scenario: ChargingScenario) -> None:
    client = TestClient(create_app())
    response = client.post("/solve", json=fixture_scenario.model_dump(mode="json"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario_name"] == fixture_scenario.metadata.scenario_name
    assert payload["status"] in {"optimal", "feasible"}
    assert payload["run_id"].startswith("solve-")
    assert "insights" in payload


def test_multisite_and_reoptimize_endpoints(
    fixture_portfolio: PortfolioScenario,
    fixture_scenario: ChargingScenario,
    fixture_telemetry: TelemetrySnapshot,
) -> None:
    client = TestClient(create_app())

    portfolio_response = client.post(
        "/solve/multisite",
        json=fixture_portfolio.model_dump(mode="json"),
    )
    assert portfolio_response.status_code == 200
    assert portfolio_response.json()["status"] in {"optimal", "feasible"}

    reopt_response = client.post(
        "/reoptimize",
        json={
            "scenario": fixture_scenario.model_dump(mode="json"),
            "telemetry": fixture_telemetry.model_dump(mode="json"),
        },
    )
    assert reopt_response.status_code == 200
    assert reopt_response.json()["telemetry_snapshot_id"] == fixture_telemetry.snapshot_id


def test_storage_endpoints(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "state_repository_backend", "sql")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'api-state.db'}")
    client = TestClient(create_app())
    store_response = client.post(
        "/storage/scenarios/test-scenario",
        json=fixture_scenario.model_dump(mode="json"),
    )
    assert store_response.status_code == 200

    get_response = client.get("/storage/scenarios/test-scenario")
    assert get_response.status_code == 200
    assert (
        get_response.json()["metadata"]["scenario_name"] == fixture_scenario.metadata.scenario_name
    )

    list_response = client.get("/storage/scenarios")
    assert list_response.status_code == 200
    assert list_response.json()["scenario_ids"] == ["test-scenario"]


def test_ingestion_endpoint(
    tmp_path: Path,
    fixture_envelope: TelemetryMessageEnvelope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "state_repository_backend", "sql")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'api-ingestion.db'}")
    client = TestClient(create_app())
    response = client.post(
        "/ingestion/telemetry-envelope",
        json=fixture_envelope.model_dump(mode="json"),
    )
    assert response.status_code == 200
    assert response.json()["item_id"] == fixture_envelope.telemetry.snapshot_id


def test_run_endpoints(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
    fixture_telemetry: TelemetrySnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "state_repository_backend", "sql")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'api-runs.db'}")
    client = TestClient(create_app())
    solve_response = client.post("/solve", json=fixture_scenario.model_dump(mode="json"))
    assert solve_response.status_code == 200
    solve_run_id = solve_response.json()["run_id"]

    reopt_response = client.post(
        "/reoptimize",
        json={
            "scenario": fixture_scenario.model_dump(mode="json"),
            "telemetry": fixture_telemetry.model_dump(mode="json"),
            "source_run_id": solve_run_id,
        },
    )
    assert reopt_response.status_code == 200
    reopt_run_id = reopt_response.json()["run_id"]

    list_response = client.get("/runs")
    assert list_response.status_code == 200
    assert len(list_response.json()["runs"]) == 2

    get_run_response = client.get(f"/runs/{solve_run_id}")
    assert get_run_response.status_code == 200
    assert get_run_response.json()["run_id"] == solve_run_id

    compare_response = client.get(
        "/runs/compare",
        params={
            "baseline_run_id": solve_run_id,
            "candidate_run_id": reopt_run_id,
        },
    )
    assert compare_response.status_code == 200
    assert compare_response.json()["baseline_run_id"] == solve_run_id


def test_async_job_endpoints_and_dashboard(
    tmp_path: Path,
    fixture_scenario: ChargingScenario,
    fixture_telemetry: TelemetrySnapshot,
    fixture_portfolio: PortfolioScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "state_repository_backend", "sql")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'api-jobs.db'}")
    client = TestClient(create_app())

    solve_job_response = client.post(
        "/jobs/solve",
        json=fixture_scenario.model_dump(mode="json"),
    )
    assert solve_job_response.status_code == 202
    solve_job_id = solve_job_response.json()["job_id"]

    reopt_job_response = client.post(
        "/jobs/reoptimize",
        json={
            "scenario": fixture_scenario.model_dump(mode="json"),
            "telemetry": fixture_telemetry.model_dump(mode="json"),
        },
    )
    assert reopt_job_response.status_code == 202

    multisite_job_response = client.post(
        "/jobs/solve/multisite",
        json=fixture_portfolio.model_dump(mode="json"),
    )
    assert multisite_job_response.status_code == 202

    job_response = client.get(f"/jobs/{solve_job_id}")
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "succeeded"
    assert job_response.json()["run_id"] is not None

    jobs_response = client.get("/jobs")
    assert jobs_response.status_code == 200
    assert len(jobs_response.json()["jobs"]) == 3

    dashboard_response = client.get("/dashboard")
    assert dashboard_response.status_code == 200
    assert "Planner Control Room" in dashboard_response.text


def test_missing_run_returns_structured_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "state_repository_backend", "sql")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'api-missing.db'}")
    client = TestClient(create_app())
    response = client.get("/runs/missing-run")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    assert response.json()["request_id"]
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_unknown_route_returns_structured_http_error() -> None:
    client = TestClient(create_app())

    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"] == "http_error"
    assert response.json()["request_id"]


def test_reoptimize_validation_error_returns_structured_400(
    fixture_scenario: ChargingScenario,
    fixture_telemetry: TelemetrySnapshot,
) -> None:
    client = TestClient(create_app())
    invalid_telemetry = fixture_telemetry.model_copy(
        update={"current_slot": fixture_scenario.site.horizon_slots},
        deep=True,
    )

    response = client.post(
        "/reoptimize",
        json={
            "scenario": fixture_scenario.model_dump(mode="json"),
            "telemetry": invalid_telemetry.model_dump(mode="json"),
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert response.json()["request_id"]


def test_metrics_endpoint_returns_runtime_metrics() -> None:
    client = TestClient(create_app())
    client.get("/health")
    client.get("/runs/missing-run")

    response = client.get("/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert "counters" in payload
    assert any(item["name"] == "http_requests_total" for item in payload["counters"])
    assert any(
        item["name"] == "http_requests_total" and item["labels"].get("path") == "/runs/{run_id}"
        for item in payload["counters"]
    )


def test_request_body_validation_returns_structured_422() -> None:
    client = TestClient(create_app())

    response = client.post("/solve", json={})

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "validation_error"
    assert payload["request_id"]
    assert payload["issues"]
    assert payload["issues"][0]["location"].startswith("body")


def test_prometheus_metrics_endpoint_returns_text_payload() -> None:
    client = TestClient(create_app())
    client.get("/health")

    response = client.get("/metrics/prometheus")

    assert response.status_code == 200
    assert "http_requests_total" in response.text
    assert response.headers["content-type"].startswith("text/plain")


def test_repository_failures_return_structured_503(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingRepository:
        def close(self) -> None:
            return None

        def save_scenario(self, scenario_id: str, scenario: ChargingScenario) -> str:
            raise RepositoryError("repository unavailable")

        def load_scenario(self, scenario_id: str) -> ChargingScenario:
            raise RepositoryError("repository unavailable")

        def list_scenarios(self) -> list[str]:
            return []

        def save_portfolio(self, scenario_id: str, scenario: PortfolioScenario) -> str:
            raise RepositoryError("repository unavailable")

        def load_portfolio(self, scenario_id: str) -> PortfolioScenario:
            raise RepositoryError("repository unavailable")

        def save_telemetry(self, snapshot_id: str, telemetry: TelemetrySnapshot) -> str:
            raise RepositoryError("repository unavailable")

        def load_telemetry(self, snapshot_id: str) -> TelemetrySnapshot:
            raise RepositoryError("repository unavailable")

        def save_result(self, result_id: str, result: object) -> str:
            raise RepositoryError("repository unavailable")

        def load_result(self, result_id: str) -> object:
            raise RepositoryError("repository unavailable")

        def save_multisite_result(self, result_id: str, result: object) -> str:
            raise RepositoryError("repository unavailable")

        def load_multisite_result(self, result_id: str) -> object:
            raise RepositoryError("repository unavailable")

        def save_run(self, run_id: str, run: object) -> str:
            raise RepositoryError("repository unavailable")

        def load_run(self, run_id: str) -> object:
            raise RepositoryError("repository unavailable")

        def list_runs(self) -> list[str]:
            return []

        def save_job(self, job_id: str, job: object) -> str:
            raise RepositoryError("repository unavailable")

        def load_job(self, job_id: str) -> object:
            raise RepositoryError("repository unavailable")

        def list_jobs(self) -> list[str]:
            return []

        def save_job_input(self, job_id: str, payload: dict[str, object]) -> str:
            raise RepositoryError("repository unavailable")

        def load_job_input(self, job_id: str) -> dict[str, object]:
            raise RepositoryError("repository unavailable")

    monkeypatch.setattr(
        "smart_charging_optimization_engine.api.app.build_state_repository",
        lambda: FailingRepository(),
    )
    client = TestClient(create_app())

    response = client.get("/storage/scenarios/example")

    assert response.status_code == 503
    assert response.json()["error"] == "repository_error"


def test_request_body_limit_rejects_stream_without_content_length(
    fixture_scenario: ChargingScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "max_request_body_bytes", 128)
    request_body = json.dumps(fixture_scenario.model_dump(mode="json")).encode("utf-8")
    chunks = [request_body[:64], request_body[64:128], request_body[128:]]
    client = TestClient(create_app())

    with client.stream(
        "POST",
        "/solve",
        content=iter(chunks),
        headers={"content-type": "application/json"},
    ) as response:
        response.read()
        payload = response.json()

    assert response.status_code == 413, payload
    assert payload["error"] == "payload_too_large"


def test_app_startup_reschedules_queued_jobs(
    sql_settings: Path,
    fixture_scenario: ChargingScenario,
) -> None:
    job_service = OptimizationJobService(build_state_repository, process_isolation=False)
    job = job_service.create_solve_job(fixture_scenario)

    with TestClient(create_app()) as client:
        deadline = time.monotonic() + 5.0
        status = "queued"
        while time.monotonic() < deadline:
            response = client.get(f"/jobs/{job.job_id}")
            assert response.status_code == 200
            status = response.json()["status"]
            if status == "succeeded":
                break
            time.sleep(0.05)

    assert status == "succeeded"
