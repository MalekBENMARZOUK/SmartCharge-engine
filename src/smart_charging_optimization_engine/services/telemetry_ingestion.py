from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError

from smart_charging_optimization_engine.domain.models import (
    TelemetryMessageEnvelope,
    TelemetrySnapshot,
)
from smart_charging_optimization_engine.exceptions import TelemetryIngestionError
from smart_charging_optimization_engine.metrics import metrics

if TYPE_CHECKING:
    from smart_charging_optimization_engine.storage.base import StateRepository

logger = logging.getLogger(__name__)


class TelemetryIngestionService:
    def __init__(self, repository: StateRepository) -> None:
        self._repository = repository

    def ingest_snapshot(self, telemetry: TelemetrySnapshot) -> str:
        destination = self._repository.save_telemetry(telemetry.snapshot_id, telemetry)
        logger.info("Persisted telemetry snapshot %s to %s", telemetry.snapshot_id, destination)
        metrics.increment("telemetry_ingested_total")
        return destination

    def ingest_envelope(self, envelope: TelemetryMessageEnvelope) -> str:
        return self.ingest_snapshot(envelope.telemetry)

    def ingest_message_body(self, message_body: bytes) -> str:
        try:
            payload = json.loads(message_body.decode("utf-8"))
            envelope = TelemetryMessageEnvelope.model_validate(payload)
            return self.ingest_envelope(envelope)
        except UnicodeDecodeError as exc:
            msg = "Telemetry message body is not valid UTF-8"
            logger.error(msg)
            metrics.increment("telemetry_ingest_failures_total", reason="decode")
            raise TelemetryIngestionError(msg) from exc
        except json.JSONDecodeError as exc:
            msg = (
                f"Telemetry message body contains invalid JSON at "
                f"line {exc.lineno}, column {exc.colno}"
            )
            logger.error(msg)
            metrics.increment("telemetry_ingest_failures_total", reason="json")
            raise TelemetryIngestionError(msg) from exc
        except ValidationError as exc:
            msg = "Telemetry message body does not conform to the expected schema"
            logger.error("%s: %s", msg, exc)
            metrics.increment("telemetry_ingest_failures_total", reason="validation")
            raise TelemetryIngestionError(msg) from exc
