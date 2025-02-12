from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from smart_charging_optimization_engine.domain.models import (
        ChargingScenario,
        PortfolioScenario,
        TelemetryMessageEnvelope,
        TelemetrySnapshot,
    )


class TestRollingHorizonEdgeCases:
    def test_reoptimize_drops_departed_vehicles(
        self,
        fixture_scenario: ChargingScenario,
        fixture_telemetry: TelemetrySnapshot,
    ) -> None:
        from smart_charging_optimization_engine.domain.models import RollingHorizonRequest
        from smart_charging_optimization_engine.services.rolling_horizon import (
            RollingHorizonOptimizer,
        )

        optimizer = RollingHorizonOptimizer()
        result = optimizer.reoptimize(
            RollingHorizonRequest(scenario=fixture_scenario, telemetry=fixture_telemetry)
        )
        assert result.status.value in ("optimal", "feasible")
        if result.assignments:
            assert all(a.slot >= fixture_telemetry.current_slot for a in result.assignments)

    def test_reoptimize_validates_unknown_charger_in_telemetry(
        self,
        fixture_scenario: ChargingScenario,
        fixture_telemetry: TelemetrySnapshot,
    ) -> None:
        from smart_charging_optimization_engine.domain.models import (
            RollingHorizonRequest,
            TelemetryVehicleState,
        )
        from smart_charging_optimization_engine.services.rolling_horizon import (
            RollingHorizonOptimizer,
        )

        bad_telemetry = fixture_telemetry.model_copy(deep=True)
        bad_telemetry.vehicle_states = [
            TelemetryVehicleState(
                vehicle_id=fixture_scenario.vehicles[0].vehicle_id,
                observed_energy_kwh=5.0,
                connected=True,
                connected_charger_id="nonexistent-charger",
            )
        ]
        optimizer = RollingHorizonOptimizer()
        with pytest.raises(ValueError, match="unknown charger"):
            optimizer.reoptimize(
                RollingHorizonRequest(scenario=fixture_scenario, telemetry=bad_telemetry)
            )

    def test_reoptimize_validates_energy_exceeds_capacity(
        self,
        fixture_scenario: ChargingScenario,
        fixture_telemetry: TelemetrySnapshot,
    ) -> None:
        from smart_charging_optimization_engine.domain.models import (
            RollingHorizonRequest,
            TelemetryVehicleState,
        )
        from smart_charging_optimization_engine.services.rolling_horizon import (
            RollingHorizonOptimizer,
        )

        bad_telemetry = fixture_telemetry.model_copy(deep=True)
        vehicle = fixture_scenario.vehicles[0]
        bad_telemetry.vehicle_states = [
            TelemetryVehicleState(
                vehicle_id=vehicle.vehicle_id,
                observed_energy_kwh=vehicle.battery_capacity_kwh + 100.0,
                connected=True,
                connected_charger_id=fixture_scenario.chargers[0].charger_id,
            )
        ]
        optimizer = RollingHorizonOptimizer()
        with pytest.raises(ValueError, match="exceeds battery capacity"):
            optimizer.reoptimize(
                RollingHorizonRequest(scenario=fixture_scenario, telemetry=bad_telemetry)
            )

    def test_reoptimize_with_power_limit_override(
        self,
        fixture_scenario: ChargingScenario,
        fixture_telemetry: TelemetrySnapshot,
    ) -> None:
        from smart_charging_optimization_engine.domain.models import RollingHorizonRequest
        from smart_charging_optimization_engine.services.rolling_horizon import (
            RollingHorizonOptimizer,
        )

        remaining = fixture_scenario.site.horizon_slots - fixture_telemetry.current_slot
        telemetry_with_override = fixture_telemetry.model_copy(
            update={"power_limit_override_kw": [50.0] * remaining}
        )
        optimizer = RollingHorizonOptimizer()
        result = optimizer.reoptimize(
            RollingHorizonRequest(scenario=fixture_scenario, telemetry=telemetry_with_override)
        )
        assert result.status.value in ("optimal", "feasible")

    def test_reoptimize_with_wrong_length_power_override_raises(
        self,
        fixture_scenario: ChargingScenario,
        fixture_telemetry: TelemetrySnapshot,
    ) -> None:
        from smart_charging_optimization_engine.domain.models import RollingHorizonRequest
        from smart_charging_optimization_engine.services.rolling_horizon import (
            RollingHorizonOptimizer,
        )

        telemetry_with_bad_override = fixture_telemetry.model_copy(
            update={"power_limit_override_kw": [50.0]}
        )
        optimizer = RollingHorizonOptimizer()
        with pytest.raises(ValueError, match="remaining horizon"):
            optimizer.reoptimize(
                RollingHorizonRequest(
                    scenario=fixture_scenario, telemetry=telemetry_with_bad_override
                )
            )

    def test_reoptimize_with_price_override(
        self,
        fixture_scenario: ChargingScenario,
        fixture_telemetry: TelemetrySnapshot,
    ) -> None:
        from smart_charging_optimization_engine.domain.models import RollingHorizonRequest
        from smart_charging_optimization_engine.services.rolling_horizon import (
            RollingHorizonOptimizer,
        )

        remaining = fixture_scenario.site.horizon_slots - fixture_telemetry.current_slot
        telemetry_with_price = fixture_telemetry.model_copy(
            update={"electricity_price_override_per_kwh": [0.05] * remaining}
        )
        optimizer = RollingHorizonOptimizer()
        result = optimizer.reoptimize(
            RollingHorizonRequest(scenario=fixture_scenario, telemetry=telemetry_with_price)
        )
        assert result.status.value in ("optimal", "feasible")

    def test_reoptimize_with_wrong_length_price_override_raises(
        self,
        fixture_scenario: ChargingScenario,
        fixture_telemetry: TelemetrySnapshot,
    ) -> None:
        from smart_charging_optimization_engine.domain.models import RollingHorizonRequest
        from smart_charging_optimization_engine.services.rolling_horizon import (
            RollingHorizonOptimizer,
        )

        telemetry_with_bad_price = fixture_telemetry.model_copy(
            update={"electricity_price_override_per_kwh": [0.05]}
        )
        optimizer = RollingHorizonOptimizer()
        with pytest.raises(ValueError, match="remaining horizon"):
            optimizer.reoptimize(
                RollingHorizonRequest(scenario=fixture_scenario, telemetry=telemetry_with_bad_price)
            )

    def test_reoptimize_no_vehicles_remain_raises(
        self,
        fixture_scenario: ChargingScenario,
    ) -> None:
        from smart_charging_optimization_engine.domain.models import (
            RollingHorizonRequest,
            TelemetrySnapshot,
            TelemetryVehicleState,
        )
        from smart_charging_optimization_engine.services.rolling_horizon import (
            RollingHorizonOptimizer,
        )

        modified_scenario = fixture_scenario.model_copy(deep=True)
        for v in modified_scenario.vehicles:
            v.departure_slot = 1
            v.availability_windows[0].end_slot = 1

        telemetry = TelemetrySnapshot(
            snapshot_id="test-snap",
            current_slot=1,
            vehicle_states=[
                TelemetryVehicleState(
                    vehicle_id=v.vehicle_id,
                    observed_energy_kwh=v.initial_energy_kwh,
                    connected=False,
                )
                for v in modified_scenario.vehicles
            ],
        )
        optimizer = RollingHorizonOptimizer()
        with pytest.raises(ValueError, match="No vehicles remain"):
            optimizer.reoptimize(
                RollingHorizonRequest(scenario=modified_scenario, telemetry=telemetry)
            )


class TestTelemetryIngestionErrors:
    def test_ingest_invalid_utf8_raises(self, sql_settings: Path) -> None:
        from smart_charging_optimization_engine.exceptions import TelemetryIngestionError
        from smart_charging_optimization_engine.services.telemetry_ingestion import (
            TelemetryIngestionService,
        )
        from smart_charging_optimization_engine.storage.sql_repository import (
            SqlAlchemyStateRepository,
        )

        repo = SqlAlchemyStateRepository(f"sqlite:///{sql_settings}")
        service = TelemetryIngestionService(repo)
        with pytest.raises(TelemetryIngestionError, match="UTF-8"):
            service.ingest_message_body(b"\xff\xfe")
        repo.close()

    def test_ingest_invalid_json_raises(self, sql_settings: Path) -> None:
        from smart_charging_optimization_engine.exceptions import TelemetryIngestionError
        from smart_charging_optimization_engine.services.telemetry_ingestion import (
            TelemetryIngestionService,
        )
        from smart_charging_optimization_engine.storage.sql_repository import (
            SqlAlchemyStateRepository,
        )

        repo = SqlAlchemyStateRepository(f"sqlite:///{sql_settings}")
        service = TelemetryIngestionService(repo)
        with pytest.raises(TelemetryIngestionError, match="invalid JSON"):
            service.ingest_message_body(b"{not json at all}")
        repo.close()

    def test_ingest_invalid_schema_raises(self, sql_settings: Path) -> None:
        from smart_charging_optimization_engine.exceptions import TelemetryIngestionError
        from smart_charging_optimization_engine.services.telemetry_ingestion import (
            TelemetryIngestionService,
        )
        from smart_charging_optimization_engine.storage.sql_repository import (
            SqlAlchemyStateRepository,
        )

        repo = SqlAlchemyStateRepository(f"sqlite:///{sql_settings}")
        service = TelemetryIngestionService(repo)
        valid_json = json.dumps({"foo": "bar"}).encode("utf-8")
        with pytest.raises(TelemetryIngestionError, match="schema"):
            service.ingest_message_body(valid_json)
        repo.close()

    def test_ingest_snapshot_directly(
        self,
        sql_settings: Path,
        fixture_telemetry: TelemetrySnapshot,
    ) -> None:
        from smart_charging_optimization_engine.services.telemetry_ingestion import (
            TelemetryIngestionService,
        )
        from smart_charging_optimization_engine.storage.sql_repository import (
            SqlAlchemyStateRepository,
        )

        repo = SqlAlchemyStateRepository(f"sqlite:///{sql_settings}")
        service = TelemetryIngestionService(repo)
        destination = service.ingest_snapshot(fixture_telemetry)
        assert destination
        repo.close()


class TestFileRepositoryExtended:
    def test_round_trip_telemetry(
        self, tmp_path: Path, fixture_telemetry: TelemetrySnapshot
    ) -> None:
        from smart_charging_optimization_engine.storage.repository import FileStateRepository

        repo = FileStateRepository(tmp_path)
        repo.save_telemetry("snap-1", fixture_telemetry)
        loaded = repo.load_telemetry("snap-1")
        assert loaded.snapshot_id == fixture_telemetry.snapshot_id

    def test_round_trip_portfolio(
        self, tmp_path: Path, fixture_portfolio: PortfolioScenario
    ) -> None:
        from smart_charging_optimization_engine.storage.repository import FileStateRepository

        repo = FileStateRepository(tmp_path)
        repo.save_portfolio("port-1", fixture_portfolio)
        loaded = repo.load_portfolio("port-1")
        assert loaded.metadata.scenario_name == fixture_portfolio.metadata.scenario_name

    def test_round_trip_result(self, tmp_path: Path, fixture_scenario: ChargingScenario) -> None:
        from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
        from smart_charging_optimization_engine.storage.repository import FileStateRepository

        repo = FileStateRepository(tmp_path)
        optimizer = SmartChargingOptimizer()
        result = optimizer.solve(fixture_scenario)
        repo.save_result("res-1", result)
        loaded = repo.load_result("res-1")
        assert loaded.scenario_name == result.scenario_name

    def test_round_trip_multisite_result(
        self, tmp_path: Path, fixture_portfolio: PortfolioScenario
    ) -> None:
        from smart_charging_optimization_engine.optimization.multisite import (
            MultiSiteSmartChargingOptimizer,
        )
        from smart_charging_optimization_engine.storage.repository import FileStateRepository

        repo = FileStateRepository(tmp_path)
        optimizer = MultiSiteSmartChargingOptimizer()
        result = optimizer.solve(fixture_portfolio)
        repo.save_multisite_result("ms-1", result)
        loaded = repo.load_multisite_result("ms-1")
        assert loaded.scenario_name == result.scenario_name

    def test_count_scenarios(self, tmp_path: Path, fixture_scenario: ChargingScenario) -> None:
        from smart_charging_optimization_engine.storage.repository import FileStateRepository

        repo = FileStateRepository(tmp_path)
        assert repo.count_scenarios() == 0
        repo.save_scenario("s1", fixture_scenario)
        assert repo.count_scenarios() == 1

    def test_count_runs(self, tmp_path: Path) -> None:
        from smart_charging_optimization_engine.storage.repository import FileStateRepository

        repo = FileStateRepository(tmp_path)
        assert repo.count_runs() == 0
        assert repo.count_jobs() == 0

    def test_delete_run(self, tmp_path: Path, fixture_scenario: ChargingScenario) -> None:
        from smart_charging_optimization_engine.domain.runs import RunKind
        from smart_charging_optimization_engine.exceptions import StorageNotFoundError
        from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
        from smart_charging_optimization_engine.services.run_tracking import (
            OptimizationRunService,
        )
        from smart_charging_optimization_engine.storage.repository import FileStateRepository

        repo = FileStateRepository(tmp_path)
        optimizer = SmartChargingOptimizer()
        result = optimizer.solve(fixture_scenario)
        run_service = OptimizationRunService(repo)
        run = run_service.record_single_site_run(
            RunKind.solve, fixture_scenario, result, optimizer.solver_config
        )
        assert len(repo.list_runs()) == 1
        repo.delete_run(run.run_id)
        assert len(repo.list_runs()) == 0
        with pytest.raises(StorageNotFoundError):
            repo.load_run(run.run_id)

    def test_delete_job(self, tmp_path: Path, fixture_scenario: ChargingScenario) -> None:
        from smart_charging_optimization_engine.exceptions import StorageNotFoundError
        from smart_charging_optimization_engine.services.job_queue import OptimizationJobService
        from smart_charging_optimization_engine.storage.repository import FileStateRepository

        repo = FileStateRepository(tmp_path)
        service = OptimizationJobService(lambda: repo, process_isolation=False)
        job = service.create_solve_job(fixture_scenario)
        assert len(repo.list_jobs()) == 1
        repo.delete_job(job.job_id)
        assert len(repo.list_jobs()) == 0
        with pytest.raises(StorageNotFoundError):
            repo.load_job(job.job_id)

    def test_load_all_jobs_file_repo(
        self, tmp_path: Path, fixture_scenario: ChargingScenario
    ) -> None:
        from smart_charging_optimization_engine.services.job_queue import OptimizationJobService
        from smart_charging_optimization_engine.storage.repository import FileStateRepository

        repo = FileStateRepository(tmp_path)
        service = OptimizationJobService(lambda: repo, process_isolation=False)
        service.create_solve_job(fixture_scenario)
        service.create_solve_job(fixture_scenario)
        assert len(repo.load_all_jobs()) == 2

    def test_load_all_runs_file_repo(
        self, tmp_path: Path, fixture_scenario: ChargingScenario
    ) -> None:
        from smart_charging_optimization_engine.domain.runs import RunKind
        from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
        from smart_charging_optimization_engine.services.run_tracking import (
            OptimizationRunService,
        )
        from smart_charging_optimization_engine.storage.repository import FileStateRepository

        repo = FileStateRepository(tmp_path)
        optimizer = SmartChargingOptimizer()
        result = optimizer.solve(fixture_scenario)
        run_service = OptimizationRunService(repo)
        run_service.record_single_site_run(
            RunKind.solve, fixture_scenario, result, optimizer.solver_config
        )
        assert len(repo.load_all_runs()) == 1

    def test_close_is_noop(self, tmp_path: Path) -> None:
        from smart_charging_optimization_engine.storage.repository import FileStateRepository

        repo = FileStateRepository(tmp_path)
        repo.close()


class TestSqlRepositoryExtended:
    def test_count_runs_and_jobs(self, tmp_path: Path, fixture_scenario: ChargingScenario) -> None:
        from smart_charging_optimization_engine.storage.sql_repository import (
            SqlAlchemyStateRepository,
        )

        repo = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'cnt.db'}")
        assert repo.count_runs() == 0
        assert repo.count_jobs() == 0
        repo.close()

    def test_round_trip_portfolio(
        self, tmp_path: Path, fixture_portfolio: PortfolioScenario
    ) -> None:
        from smart_charging_optimization_engine.storage.sql_repository import (
            SqlAlchemyStateRepository,
        )

        repo = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'port.db'}")
        repo.save_portfolio("p1", fixture_portfolio)
        loaded = repo.load_portfolio("p1")
        assert loaded.metadata.scenario_name == fixture_portfolio.metadata.scenario_name
        repo.close()

    def test_delete_job_also_deletes_input(
        self, tmp_path: Path, fixture_scenario: ChargingScenario
    ) -> None:
        from smart_charging_optimization_engine.services.job_queue import OptimizationJobService
        from smart_charging_optimization_engine.storage.sql_repository import (
            SqlAlchemyStateRepository,
        )

        db = f"sqlite:///{tmp_path / 'deljob.db'}"
        service = OptimizationJobService(
            lambda: SqlAlchemyStateRepository(db), process_isolation=False
        )
        job = service.create_solve_job(fixture_scenario)
        repo = SqlAlchemyStateRepository(db)
        repo.delete_job(job.job_id)
        assert repo.count_jobs() == 0
        repo.close()

    def test_try_delete_nonexistent_is_silent(self, tmp_path: Path) -> None:
        from smart_charging_optimization_engine.storage.sql_repository import (
            SqlAlchemyStateRepository,
        )

        repo = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'noop.db'}")
        repo._try_delete_document("job-input", "nonexistent")
        repo.close()

    def test_upsert_overwrites_existing(
        self, tmp_path: Path, fixture_scenario: ChargingScenario
    ) -> None:
        from smart_charging_optimization_engine.storage.sql_repository import (
            SqlAlchemyStateRepository,
        )

        repo = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'upsert.db'}")
        repo.save_scenario("s1", fixture_scenario)
        modified = fixture_scenario.model_copy(deep=True)
        modified.metadata.description = "updated description"
        repo.save_scenario("s1", modified)
        loaded = repo.load_scenario("s1")
        assert loaded.metadata.description == "updated description"
        assert repo.count_scenarios() == 1
        repo.close()


def test_factory_rejects_unsupported_backend() -> None:
    from smart_charging_optimization_engine.exceptions import ConfigurationError
    from smart_charging_optimization_engine.storage.factory import (
        build_state_repository_from_descriptor,
    )

    with pytest.raises(ConfigurationError, match="Unsupported"):
        build_state_repository_from_descriptor(
            {"backend": "redis", "state_store_dir": "", "database_url": ""}
        )


class TestJsonIoErrors:
    def test_load_scenario_missing_file_raises(self, tmp_path: Path) -> None:
        from smart_charging_optimization_engine.exceptions import JsonPayloadError
        from smart_charging_optimization_engine.io.json_io import load_scenario

        with pytest.raises(JsonPayloadError, match="not found"):
            load_scenario(tmp_path / "nonexistent.json")

    def test_load_scenario_invalid_json_raises(self, tmp_path: Path) -> None:
        from smart_charging_optimization_engine.exceptions import JsonPayloadError
        from smart_charging_optimization_engine.io.json_io import load_scenario

        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(JsonPayloadError, match="Invalid JSON"):
            load_scenario(bad)

    def test_save_result_creates_parent_dirs(
        self, tmp_path: Path, fixture_scenario: ChargingScenario
    ) -> None:
        from smart_charging_optimization_engine.io.json_io import save_result
        from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer

        optimizer = SmartChargingOptimizer()
        result = optimizer.solve(fixture_scenario)
        out = tmp_path / "deep" / "nested" / "result.json"
        save_result(result, out)
        assert out.exists()

    def test_save_json_payload_rejects_non_serializable(self, tmp_path: Path) -> None:
        from smart_charging_optimization_engine.exceptions import JsonPayloadError
        from smart_charging_optimization_engine.io.json_io import save_json_payload

        with pytest.raises(JsonPayloadError, match="serialize"):
            save_json_payload({"bad": object()}, tmp_path / "bad.json")

    def test_load_telemetry_envelope(self, fixture_envelope: TelemetryMessageEnvelope) -> None:
        assert fixture_envelope.telemetry.snapshot_id

    def test_load_portfolio_round_trip(self, fixture_portfolio: PortfolioScenario) -> None:
        assert len(fixture_portfolio.sites) > 0

    def test_save_multisite_result(
        self, tmp_path: Path, fixture_portfolio: PortfolioScenario
    ) -> None:
        from smart_charging_optimization_engine.io.json_io import save_multisite_result
        from smart_charging_optimization_engine.optimization.multisite import (
            MultiSiteSmartChargingOptimizer,
        )

        optimizer = MultiSiteSmartChargingOptimizer()
        result = optimizer.solve(fixture_portfolio)
        out = tmp_path / "ms_result.json"
        save_multisite_result(result, out)
        assert out.exists()


class TestLoggingUtils:
    def test_json_formatter_produces_valid_json(self) -> None:
        import logging

        from smart_charging_optimization_engine.logging_utils import JsonLogFormatter

        formatter = JsonLogFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "Hello world"
        assert parsed["level"] == "INFO"

    def test_json_formatter_includes_exception(self) -> None:
        import logging
        import sys

        from smart_charging_optimization_engine.logging_utils import JsonLogFormatter

        formatter = JsonLogFormatter()
        try:
            msg = "mock error"
            raise RuntimeError(msg)
        except RuntimeError:
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="fail",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "RuntimeError" in parsed["exception"]

    def test_json_formatter_includes_extra_fields(self) -> None:
        import logging

        from smart_charging_optimization_engine.logging_utils import JsonLogFormatter

        formatter = JsonLogFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        record.request_id = "abc-123"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "fields" in parsed
        assert parsed["fields"]["request_id"] == "abc-123"

    def test_configure_logging_json_format(self) -> None:
        import logging

        from smart_charging_optimization_engine.logging_utils import configure_logging

        configure_logging("DEBUG", "json")
        root = logging.getLogger()
        assert root.level == logging.DEBUG


class TestResultAnalysis:
    def test_build_insights_for_solved_scenario(self, fixture_scenario: ChargingScenario) -> None:
        from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer

        optimizer = SmartChargingOptimizer()
        result = optimizer.solve(fixture_scenario)
        assert result.insights is not None
        assert isinstance(result.insights.summary, str)
        assert result.insights.at_risk_vehicle_ids is not None

    def test_multisite_result_has_insights(self, fixture_portfolio: PortfolioScenario) -> None:
        from smart_charging_optimization_engine.optimization.multisite import (
            MultiSiteSmartChargingOptimizer,
        )

        optimizer = MultiSiteSmartChargingOptimizer()
        result = optimizer.solve(fixture_portfolio)
        assert result.insights is not None
        assert "site" in result.insights.summary.lower()

    def test_run_comparison_produces_vehicle_deltas(
        self,
        tmp_path: Path,
        fixture_scenario: ChargingScenario,
    ) -> None:
        from smart_charging_optimization_engine.domain.runs import RunKind
        from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
        from smart_charging_optimization_engine.services.run_tracking import (
            OptimizationRunService,
        )
        from smart_charging_optimization_engine.storage.sql_repository import (
            SqlAlchemyStateRepository,
        )

        repo = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'cmp.db'}")
        optimizer = SmartChargingOptimizer()
        result = optimizer.solve(fixture_scenario)
        run_service = OptimizationRunService(repo)
        run1 = run_service.record_single_site_run(
            RunKind.solve, fixture_scenario, result, optimizer.solver_config
        )
        run2 = run_service.record_single_site_run(
            RunKind.solve, fixture_scenario, result, optimizer.solver_config
        )
        comparison = run_service.compare_runs(run1.run_id, run2.run_id)
        assert comparison.summary
        assert comparison.total_cost_delta == pytest.approx(0.0, abs=0.01)
        repo.close()


class TestValidationExtended:
    def test_vehicle_v2g_requires_flag(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import TimeWindow, Vehicle

        with pytest.raises(ValidationError, match="v2g_enabled"):
            Vehicle(
                vehicle_id="v1",
                battery_capacity_kwh=60.0,
                initial_energy_kwh=30.0,
                target_energy_kwh=50.0,
                max_charging_power_kw=50.0,
                availability_windows=[TimeWindow(start_slot=0, end_slot=4)],
                departure_slot=4,
                v2g_enabled=False,
                max_discharging_power_kw=10.0,
            )

    def test_time_window_requires_end_after_start(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import TimeWindow

        with pytest.raises(ValidationError, match="strictly greater"):
            TimeWindow(start_slot=5, end_slot=3)

    def test_site_profile_rejects_negative_power_limit(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import SiteProfile

        with pytest.raises(ValidationError, match="non-negative"):
            SiteProfile(
                site_id="s1",
                time_step_minutes=15,
                horizon_slots=2,
                power_limit_kw=[-10.0, 50.0],
                electricity_price_per_kwh=[0.1, 0.1],
            )

    def test_site_profile_rejects_mismatched_lengths(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import SiteProfile

        with pytest.raises(ValidationError, match="horizon_slots"):
            SiteProfile(
                site_id="s1",
                time_step_minutes=15,
                horizon_slots=4,
                power_limit_kw=[50.0, 50.0],
                electricity_price_per_kwh=[0.1, 0.1, 0.1, 0.1],
            )

    def test_scenario_rejects_departure_beyond_horizon(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import (
            Charger,
            ChargingScenario,
            ScenarioMetadata,
            SiteProfile,
            TimeWindow,
            Vehicle,
        )

        with pytest.raises(ValidationError, match="departure_slot exceeds horizon"):
            ChargingScenario(
                metadata=ScenarioMetadata(scenario_name="test"),
                site=SiteProfile(
                    site_id="s1",
                    time_step_minutes=15,
                    horizon_slots=4,
                    power_limit_kw=[50.0] * 4,
                    electricity_price_per_kwh=[0.1] * 4,
                ),
                chargers=[Charger(charger_id="c1", max_power_kw=50.0)],
                vehicles=[
                    Vehicle(
                        vehicle_id="v1",
                        battery_capacity_kwh=60.0,
                        initial_energy_kwh=30.0,
                        target_energy_kwh=50.0,
                        max_charging_power_kw=50.0,
                        availability_windows=[TimeWindow(start_slot=0, end_slot=4)],
                        departure_slot=100,
                    )
                ],
            )

    def test_scenario_rejects_duplicate_vehicle_ids(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import (
            Charger,
            ChargingScenario,
            ScenarioMetadata,
            SiteProfile,
            TimeWindow,
            Vehicle,
        )

        vehicle = Vehicle(
            vehicle_id="v1",
            battery_capacity_kwh=60.0,
            initial_energy_kwh=30.0,
            target_energy_kwh=50.0,
            max_charging_power_kw=50.0,
            availability_windows=[TimeWindow(start_slot=0, end_slot=4)],
            departure_slot=4,
        )
        with pytest.raises(ValidationError, match="unique"):
            ChargingScenario(
                metadata=ScenarioMetadata(scenario_name="test"),
                site=SiteProfile(
                    site_id="s1",
                    time_step_minutes=15,
                    horizon_slots=4,
                    power_limit_kw=[50.0] * 4,
                    electricity_price_per_kwh=[0.1] * 4,
                ),
                chargers=[Charger(charger_id="c1", max_power_kw=50.0)],
                vehicles=[vehicle, vehicle.model_copy()],
            )

    def test_vehicle_rejects_initial_above_capacity(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import TimeWindow, Vehicle

        with pytest.raises(ValidationError, match="initial_energy_kwh"):
            Vehicle(
                vehicle_id="v1",
                battery_capacity_kwh=60.0,
                initial_energy_kwh=100.0,
                target_energy_kwh=50.0,
                max_charging_power_kw=50.0,
                availability_windows=[TimeWindow(start_slot=0, end_slot=4)],
                departure_slot=4,
            )

    def test_vehicle_rejects_target_above_capacity(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import TimeWindow, Vehicle

        with pytest.raises(ValidationError, match="target_energy_kwh"):
            Vehicle(
                vehicle_id="v1",
                battery_capacity_kwh=60.0,
                initial_energy_kwh=30.0,
                target_energy_kwh=100.0,
                max_charging_power_kw=50.0,
                availability_windows=[TimeWindow(start_slot=0, end_slot=4)],
                departure_slot=4,
            )

    def test_vehicle_rejects_minimum_above_target(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import TimeWindow, Vehicle

        with pytest.raises(ValidationError, match="minimum_energy_kwh"):
            Vehicle(
                vehicle_id="v1",
                battery_capacity_kwh=60.0,
                initial_energy_kwh=30.0,
                target_energy_kwh=50.0,
                minimum_energy_kwh=55.0,
                max_charging_power_kw=50.0,
                availability_windows=[TimeWindow(start_slot=0, end_slot=4)],
                departure_slot=4,
            )

    def test_vehicle_rejects_empty_compatible_chargers(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import TimeWindow, Vehicle

        with pytest.raises(ValidationError, match="compatible_charger_ids"):
            Vehicle(
                vehicle_id="v1",
                battery_capacity_kwh=60.0,
                initial_energy_kwh=30.0,
                target_energy_kwh=50.0,
                max_charging_power_kw=50.0,
                availability_windows=[TimeWindow(start_slot=0, end_slot=4)],
                departure_slot=4,
                compatible_charger_ids=[],
            )

    def test_scenario_rejects_unknown_compatible_charger(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import (
            Charger,
            ChargingScenario,
            ScenarioMetadata,
            SiteProfile,
            TimeWindow,
            Vehicle,
        )

        with pytest.raises(ValidationError, match="unknown chargers"):
            ChargingScenario(
                metadata=ScenarioMetadata(scenario_name="test"),
                site=SiteProfile(
                    site_id="s1",
                    time_step_minutes=15,
                    horizon_slots=4,
                    power_limit_kw=[50.0] * 4,
                    electricity_price_per_kwh=[0.1] * 4,
                ),
                chargers=[Charger(charger_id="c1", max_power_kw=50.0)],
                vehicles=[
                    Vehicle(
                        vehicle_id="v1",
                        battery_capacity_kwh=60.0,
                        initial_energy_kwh=30.0,
                        target_energy_kwh=50.0,
                        max_charging_power_kw=50.0,
                        availability_windows=[TimeWindow(start_slot=0, end_slot=4)],
                        departure_slot=4,
                        compatible_charger_ids=["nonexistent"],
                    )
                ],
            )

    def test_scenario_rejects_availability_after_departure(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import (
            Charger,
            ChargingScenario,
            ScenarioMetadata,
            SiteProfile,
            TimeWindow,
            Vehicle,
        )

        with pytest.raises(ValidationError, match="after departure"):
            ChargingScenario(
                metadata=ScenarioMetadata(scenario_name="test"),
                site=SiteProfile(
                    site_id="s1",
                    time_step_minutes=15,
                    horizon_slots=8,
                    power_limit_kw=[50.0] * 8,
                    electricity_price_per_kwh=[0.1] * 8,
                ),
                chargers=[Charger(charger_id="c1", max_power_kw=50.0)],
                vehicles=[
                    Vehicle(
                        vehicle_id="v1",
                        battery_capacity_kwh=60.0,
                        initial_energy_kwh=30.0,
                        target_energy_kwh=50.0,
                        max_charging_power_kw=50.0,
                        availability_windows=[TimeWindow(start_slot=0, end_slot=8)],
                        departure_slot=4,
                    )
                ],
            )

    def test_portfolio_rejects_mismatched_horizon(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import (
            Charger,
            CoordinatedSiteScenario,
            NetworkConstraint,
            PortfolioScenario,
            ScenarioMetadata,
            SiteProfile,
            TimeWindow,
            Vehicle,
        )

        site1 = CoordinatedSiteScenario(
            site=SiteProfile(
                site_id="s1",
                time_step_minutes=15,
                horizon_slots=4,
                power_limit_kw=[50.0] * 4,
                electricity_price_per_kwh=[0.1] * 4,
            ),
            chargers=[Charger(charger_id="c1", max_power_kw=50.0, site_id="s1")],
            vehicles=[
                Vehicle(
                    vehicle_id="v1",
                    battery_capacity_kwh=60.0,
                    initial_energy_kwh=30.0,
                    target_energy_kwh=50.0,
                    max_charging_power_kw=50.0,
                    availability_windows=[TimeWindow(start_slot=0, end_slot=4)],
                    departure_slot=4,
                )
            ],
        )
        site2 = CoordinatedSiteScenario(
            site=SiteProfile(
                site_id="s2",
                time_step_minutes=15,
                horizon_slots=8,
                power_limit_kw=[50.0] * 8,
                electricity_price_per_kwh=[0.1] * 8,
            ),
            chargers=[Charger(charger_id="c2", max_power_kw=50.0, site_id="s2")],
            vehicles=[
                Vehicle(
                    vehicle_id="v2",
                    battery_capacity_kwh=60.0,
                    initial_energy_kwh=30.0,
                    target_energy_kwh=50.0,
                    max_charging_power_kw=50.0,
                    availability_windows=[TimeWindow(start_slot=0, end_slot=8)],
                    departure_slot=8,
                )
            ],
        )
        with pytest.raises(ValidationError, match="horizon_slots"):
            PortfolioScenario(
                metadata=ScenarioMetadata(scenario_name="test"),
                sites=[site1, site2],
                network=NetworkConstraint(),
            )

    def test_telemetry_connected_state_requires_charger(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import TelemetryVehicleState

        with pytest.raises(ValidationError, match="connected_charger_id"):
            TelemetryVehicleState(
                vehicle_id="v1",
                observed_energy_kwh=30.0,
                connected=True,
                connected_charger_id=None,
            )

    def test_telemetry_disconnected_rejects_charger(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.domain.models import TelemetryVehicleState

        with pytest.raises(ValidationError, match="connected_charger_id"):
            TelemetryVehicleState(
                vehicle_id="v1",
                observed_energy_kwh=30.0,
                connected=False,
                connected_charger_id="c1",
            )


class TestConfigExtended:
    def test_rejects_unsupported_log_level(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.config import AppSettings

        with pytest.raises((ValidationError, Exception), match=r"[Uu]nsupported log level"):
            AppSettings(log_level="TRACE")

    def test_rejects_unsupported_log_format(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.config import AppSettings

        with pytest.raises((ValidationError, Exception), match=r"[Uu]nsupported log format"):
            AppSettings(log_format="xml")

    def test_rejects_invalid_database_url(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.config import AppSettings

        with pytest.raises((ValidationError, Exception), match="database_url"):
            AppSettings(database_url="not-a-url")

    def test_rejects_heartbeat_above_stale_threshold(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.config import AppSettings

        with pytest.raises((ValidationError, Exception), match="heartbeat"):
            AppSettings(
                job_heartbeat_interval_seconds=500.0,
                job_stale_threshold_seconds=100.0,
            )

    def test_file_backend_requires_store_dir(self) -> None:
        from pydantic import ValidationError

        from smart_charging_optimization_engine.config import AppSettings

        with pytest.raises((ValidationError, Exception), match="state_store_dir"):
            AppSettings(state_repository_backend="file", state_store_dir="   ")


class TestMetricsExtended:
    def test_prometheus_renders_counters(self) -> None:
        from smart_charging_optimization_engine.metrics import MetricsRegistry

        registry = MetricsRegistry()
        registry.increment("test_counter", amount=5.0, service="api")
        output = registry.render_prometheus()
        assert "# TYPE test_counter counter" in output
        assert 'test_counter{service="api"} 5.0' in output

    def test_prometheus_renders_observations(self) -> None:
        from smart_charging_optimization_engine.metrics import MetricsRegistry

        registry = MetricsRegistry()
        registry.observe("latency", 10.0, endpoint="/solve")
        registry.observe("latency", 20.0, endpoint="/solve")
        output = registry.render_prometheus()
        assert "latency_count" in output
        assert "latency_sum" in output

    def test_empty_registry_renders_empty(self) -> None:
        from smart_charging_optimization_engine.metrics import MetricsRegistry

        registry = MetricsRegistry()
        assert registry.render_prometheus() == ""

    def test_label_escaping(self) -> None:
        from smart_charging_optimization_engine.metrics import MetricsRegistry

        assert MetricsRegistry._escape_label_value('a"b') == 'a\\"b'
        assert MetricsRegistry._escape_label_value("a\nb") == "a\\nb"


class TestV2GExtended:
    def test_v2g_result_includes_export_revenue(
        self, fixture_v2g_scenario: ChargingScenario
    ) -> None:
        from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer

        optimizer = SmartChargingOptimizer()
        result = optimizer.solve(fixture_v2g_scenario)
        assert result.objective_breakdown.export_revenue >= 0.0

    def test_v2g_vehicle_has_export_energy(self, fixture_v2g_scenario: ChargingScenario) -> None:
        from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer

        optimizer = SmartChargingOptimizer()
        result = optimizer.solve(fixture_v2g_scenario)
        total_exported = sum(v.total_energy_exported_kwh for v in result.vehicle_summaries)
        assert total_exported > 0.0


class TestMultisiteExtended:
    def test_multisite_result_has_network_summary(
        self, fixture_portfolio: PortfolioScenario
    ) -> None:
        from smart_charging_optimization_engine.optimization.multisite import (
            MultiSiteSmartChargingOptimizer,
        )

        optimizer = MultiSiteSmartChargingOptimizer()
        result = optimizer.solve(fixture_portfolio)
        assert result.network_summary is not None
        assert len(result.network_summary) > 0

    def test_multisite_per_site_results_match_sites(
        self, fixture_portfolio: PortfolioScenario
    ) -> None:
        from smart_charging_optimization_engine.optimization.multisite import (
            MultiSiteSmartChargingOptimizer,
        )

        optimizer = MultiSiteSmartChargingOptimizer()
        result = optimizer.solve(fixture_portfolio)
        assert len(result.site_results) == len(fixture_portfolio.sites)
