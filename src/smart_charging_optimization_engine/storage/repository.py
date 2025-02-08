from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any, cast

from smart_charging_optimization_engine.domain.jobs import OptimizationJob
from smart_charging_optimization_engine.domain.models import (
    ChargingScenario,
    PortfolioScenario,
    TelemetrySnapshot,
)
from smart_charging_optimization_engine.domain.results import (
    MultiSiteOptimizationResult,
    OptimizationResult,
)
from smart_charging_optimization_engine.domain.runs import OptimizationRun
from smart_charging_optimization_engine.exceptions import (
    JsonPayloadError,
    RepositoryError,
    StorageNotFoundError,
)
from smart_charging_optimization_engine.storage._common import validate_item_id

if TYPE_CHECKING:
    from pydantic import BaseModel


class FileStateRepository:
    def __init__(self, root_dir: str | Path) -> None:
        self._root_dir = Path(root_dir)

    def close(self) -> None:
        return None

    def __enter__(self) -> FileStateRepository:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def save_scenario(self, scenario_id: str, scenario: ChargingScenario) -> str:
        return self._write_model("scenarios", scenario_id, scenario)

    def load_scenario(self, scenario_id: str) -> ChargingScenario:
        return ChargingScenario.model_validate(self._read_json("scenarios", scenario_id))

    def list_scenarios(self) -> list[str]:
        return self._list_ids("scenarios")

    def count_scenarios(self) -> int:
        return len(self._list_ids("scenarios"))

    def save_portfolio(self, scenario_id: str, scenario: PortfolioScenario) -> str:
        return self._write_model("portfolios", scenario_id, scenario)

    def load_portfolio(self, scenario_id: str) -> PortfolioScenario:
        return PortfolioScenario.model_validate(self._read_json("portfolios", scenario_id))

    def save_telemetry(self, snapshot_id: str, telemetry: TelemetrySnapshot) -> str:
        return self._write_model("telemetry", snapshot_id, telemetry)

    def load_telemetry(self, snapshot_id: str) -> TelemetrySnapshot:
        return TelemetrySnapshot.model_validate(self._read_json("telemetry", snapshot_id))

    def save_result(self, result_id: str, result: OptimizationResult) -> str:
        return self._write_model("results", result_id, result)

    def load_result(self, result_id: str) -> OptimizationResult:
        return OptimizationResult.model_validate(self._read_json("results", result_id))

    def save_multisite_result(self, result_id: str, result: MultiSiteOptimizationResult) -> str:
        return self._write_model("multisite-results", result_id, result)

    def load_multisite_result(self, result_id: str) -> MultiSiteOptimizationResult:
        return MultiSiteOptimizationResult.model_validate(
            self._read_json("multisite-results", result_id)
        )

    def save_run(self, run_id: str, run: OptimizationRun) -> str:
        return self._write_model("runs", run_id, run)

    def load_run(self, run_id: str) -> OptimizationRun:
        return OptimizationRun.model_validate(self._read_json("runs", run_id))

    def list_runs(self) -> list[str]:
        return self._list_ids("runs")

    def count_runs(self) -> int:
        return len(self._list_ids("runs"))

    def save_job(self, job_id: str, job: OptimizationJob) -> str:
        return self._write_model("jobs", job_id, job)

    def load_job(self, job_id: str) -> OptimizationJob:
        return OptimizationJob.model_validate(self._read_json("jobs", job_id))

    def list_jobs(self) -> list[str]:
        return self._list_ids("jobs")

    def count_jobs(self) -> int:
        return len(self._list_ids("jobs"))

    def load_all_jobs(self) -> list[OptimizationJob]:
        return [self.load_job(job_id) for job_id in self.list_jobs()]

    def count_pending_jobs(self) -> int:
        return sum(1 for job in self.load_all_jobs() if job.status in {"queued", "running"})

    def load_all_runs(self) -> list[OptimizationRun]:
        return [self.load_run(run_id) for run_id in self.list_runs()]

    def save_job_input(self, job_id: str, payload: dict[str, Any]) -> str:
        return self._write_json_payload("job-inputs", job_id, payload)

    def load_job_input(self, job_id: str) -> dict[str, Any]:
        return self._read_json("job-inputs", job_id)

    def delete_run(self, run_id: str) -> None:
        self._delete_document("runs", run_id)

    def delete_job(self, job_id: str) -> None:
        self._delete_document("jobs", job_id)
        self._try_delete_document("job-inputs", job_id)

    def _delete_document(self, collection: str, item_id: str) -> None:
        safe_item_id = validate_item_id(item_id)
        file_path = self._root_dir / collection / f"{safe_item_id}.json"
        try:
            file_path.unlink()
        except FileNotFoundError as exc:
            msg = f"No stored document found for {collection}/{safe_item_id}"
            raise StorageNotFoundError(msg) from exc
        except OSError as exc:
            msg = f"Failed to delete {collection}/{safe_item_id}"
            raise RepositoryError(msg) from exc

    def _try_delete_document(self, collection: str, item_id: str) -> None:
        safe_item_id = validate_item_id(item_id)
        file_path = self._root_dir / collection / f"{safe_item_id}.json"
        with suppress(OSError):
            file_path.unlink(missing_ok=True)

    def _write_model(self, collection: str, item_id: str, model: BaseModel) -> str:
        return self._write_json_document(collection, item_id, model.model_dump(mode="json"))

    def _write_json_payload(self, collection: str, item_id: str, payload: dict[str, Any]) -> str:
        return self._write_json_document(collection, item_id, payload)

    def _write_json_document(self, collection: str, item_id: str, payload: dict[str, Any]) -> str:
        safe_item_id = validate_item_id(item_id)
        output_path = self._root_dir / collection / f"{safe_item_id}.json"
        temp_path: Path | None = None
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=output_path.parent,
                delete=False,
                prefix=f"{safe_item_id}-",
                suffix=".tmp",
            ) as temp_file:
                temp_file.write(json.dumps(payload, indent=2))
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = Path(temp_file.name)
            temp_path.replace(output_path)
            return str(output_path)
        except OSError as exc:
            msg = f"Failed to persist {collection}/{safe_item_id} to {output_path}"
            raise RepositoryError(msg) from exc
        finally:
            if temp_path is not None and temp_path.exists():
                with suppress(OSError):
                    temp_path.unlink()

    def _read_json(self, collection: str, item_id: str) -> dict[str, object]:
        safe_item_id = validate_item_id(item_id)
        input_path = self._root_dir / collection / f"{safe_item_id}.json"
        try:
            return cast("dict[str, object]", json.loads(input_path.read_text(encoding="utf-8")))
        except FileNotFoundError as exc:
            msg = f"No stored document found for {collection}/{safe_item_id}"
            raise StorageNotFoundError(msg) from exc
        except json.JSONDecodeError as exc:
            msg = (
                f"Stored JSON for {collection}/{safe_item_id} is invalid at "
                f"line {exc.lineno}, column {exc.colno}"
            )
            raise JsonPayloadError(msg) from exc
        except OSError as exc:
            msg = f"Failed to read stored document {collection}/{safe_item_id}"
            raise RepositoryError(msg) from exc

    def _list_ids(self, collection: str) -> list[str]:
        directory = self._root_dir / collection
        if not directory.exists():
            return []
        try:
            return sorted(path.stem for path in directory.glob("*.json") if path.is_file())
        except OSError as exc:
            msg = f"Failed to list stored documents in {directory}"
            raise RepositoryError(msg) from exc
