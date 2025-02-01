from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from smart_charging_optimization_engine.domain.models import (
    ChargingScenario,
    PortfolioScenario,
    TelemetryMessageEnvelope,
    TelemetrySnapshot,
)
from smart_charging_optimization_engine.exceptions import JsonPayloadError

if TYPE_CHECKING:
    from smart_charging_optimization_engine.domain.results import (
        MultiSiteOptimizationResult,
        OptimizationResult,
    )


def _load_json_document(path: str | Path, description: str) -> object:
    input_path = Path(path)
    try:
        with input_path.open("r", encoding="utf-8") as input_file:
            return json.load(input_file)
    except FileNotFoundError as exc:
        msg = f"{description} file not found: {input_path}"
        raise JsonPayloadError(msg) from exc
    except json.JSONDecodeError as exc:
        msg = (
            f"Invalid JSON in {description} file {input_path} at "
            f"line {exc.lineno}, column {exc.colno}"
        )
        raise JsonPayloadError(msg) from exc
    except OSError as exc:
        msg = f"Failed to read {description} file: {input_path}"
        raise JsonPayloadError(msg) from exc


def _write_json_text(path: str | Path, content: str, description: str) -> None:
    output_path = Path(path)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        msg = f"Failed to write {description} file: {output_path}"
        raise JsonPayloadError(msg) from exc


def load_scenario(path: str | Path) -> ChargingScenario:
    return ChargingScenario.model_validate(_load_json_document(path, "scenario"))


def save_result(result: OptimizationResult, path: str | Path) -> None:
    _write_json_text(path, result.model_dump_json(indent=2), "optimization result")


def save_json_payload(payload: object, path: str | Path) -> None:
    try:
        serialized = json.dumps(payload, indent=2)
    except (TypeError, ValueError) as exc:
        msg = "Failed to serialize JSON payload"
        raise JsonPayloadError(msg) from exc
    _write_json_text(path, serialized, "JSON payload")


def load_telemetry_snapshot(path: str | Path) -> TelemetrySnapshot:
    return TelemetrySnapshot.model_validate(_load_json_document(path, "telemetry snapshot"))


def load_telemetry_envelope(path: str | Path) -> TelemetryMessageEnvelope:
    return TelemetryMessageEnvelope.model_validate(_load_json_document(path, "telemetry envelope"))


def load_portfolio(path: str | Path) -> PortfolioScenario:
    return PortfolioScenario.model_validate(_load_json_document(path, "portfolio"))


def save_multisite_result(result: MultiSiteOptimizationResult, path: str | Path) -> None:
    _write_json_text(path, result.model_dump_json(indent=2), "multisite optimization result")
