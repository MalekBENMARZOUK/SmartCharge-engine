from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smart_charging_optimization_engine.exceptions import TelemetryIngestionError
from smart_charging_optimization_engine.services.telemetry_ingestion import (
    TelemetryIngestionService,
)
from smart_charging_optimization_engine.storage.sql_repository import (
    SqlAlchemyStateRepository,
)

if TYPE_CHECKING:
    from pathlib import Path

    from smart_charging_optimization_engine.domain.models import TelemetryMessageEnvelope


def test_ingestion_service_persists_envelope(
    tmp_path: Path,
    fixture_envelope: TelemetryMessageEnvelope,
) -> None:
    repository = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'ingestion.db'}")
    try:
        service = TelemetryIngestionService(repository)

        destination = service.ingest_envelope(fixture_envelope)

        assert destination.endswith("telemetry/broker-snapshot-001")
        loaded = repository.load_telemetry("broker-snapshot-001")
        assert loaded.snapshot_id == fixture_envelope.telemetry.snapshot_id
    finally:
        repository.close()


def test_ingestion_service_parses_raw_message_body(
    tmp_path: Path,
    fixture_envelope_path: Path,
) -> None:
    repository = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'ingestion-body.db'}")
    try:
        service = TelemetryIngestionService(repository)
        raw_body = fixture_envelope_path.read_text(encoding="utf-8").encode("utf-8")

        service.ingest_message_body(raw_body)

        loaded = repository.load_telemetry("broker-snapshot-001")
        assert loaded.current_slot == 2
    finally:
        repository.close()


def test_ingestion_service_rejects_invalid_message_body(tmp_path: Path) -> None:
    repository = SqlAlchemyStateRepository(f"sqlite:///{tmp_path / 'ingestion-invalid.db'}")
    try:
        service = TelemetryIngestionService(repository)

        with pytest.raises(TelemetryIngestionError):
            service.ingest_message_body(b"{invalid json")
    finally:
        repository.close()
