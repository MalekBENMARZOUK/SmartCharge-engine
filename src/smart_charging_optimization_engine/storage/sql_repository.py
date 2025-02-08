from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

from sqlalchemy import DateTime, PrimaryKeyConstraint, String, Text, create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from smart_charging_optimization_engine.config import settings
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
    ConfigurationError,
    JsonPayloadError,
    RepositoryError,
    StorageNotFoundError,
)
from smart_charging_optimization_engine.storage._common import validate_item_id


class Base(DeclarativeBase):
    pass


class StoredDocument(Base):
    __tablename__ = "stored_documents"
    __table_args__ = (PrimaryKeyConstraint("kind", "item_id"),)

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SqlAlchemyStateRepository:
    def __init__(self, database_url: str) -> None:
        normalized_database_url = self._normalize_database_url(database_url)
        self._database_url = normalized_database_url
        self._safe_database_label = self._redact_database_url(normalized_database_url)
        self._ensure_parent_directory(normalized_database_url)
        try:
            engine_kwargs = self._build_engine_kwargs(normalized_database_url)
            self._engine = create_engine(normalized_database_url, **engine_kwargs)
            Base.metadata.create_all(self._engine)
        except SQLAlchemyError as exc:
            msg = f"Failed to initialize SQL repository at {normalized_database_url}"
            raise RepositoryError(msg) from exc

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> SqlAlchemyStateRepository:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def save_scenario(self, scenario_id: str, scenario: ChargingScenario) -> str:
        return self._save_document("scenario", scenario_id, scenario.model_dump(mode="json"))

    def load_scenario(self, scenario_id: str) -> ChargingScenario:
        return ChargingScenario.model_validate(self._load_document("scenario", scenario_id))

    def list_scenarios(self) -> list[str]:
        return self._list_document_ids("scenario")

    def count_scenarios(self) -> int:
        return self._count_documents("scenario")

    def save_portfolio(self, scenario_id: str, scenario: PortfolioScenario) -> str:
        return self._save_document("portfolio", scenario_id, scenario.model_dump(mode="json"))

    def load_portfolio(self, scenario_id: str) -> PortfolioScenario:
        return PortfolioScenario.model_validate(self._load_document("portfolio", scenario_id))

    def save_telemetry(self, snapshot_id: str, telemetry: TelemetrySnapshot) -> str:
        return self._save_document("telemetry", snapshot_id, telemetry.model_dump(mode="json"))

    def load_telemetry(self, snapshot_id: str) -> TelemetrySnapshot:
        return TelemetrySnapshot.model_validate(self._load_document("telemetry", snapshot_id))

    def save_result(self, result_id: str, result: OptimizationResult) -> str:
        return self._save_document("result", result_id, result.model_dump(mode="json"))

    def load_result(self, result_id: str) -> OptimizationResult:
        return OptimizationResult.model_validate(self._load_document("result", result_id))

    def save_multisite_result(self, result_id: str, result: MultiSiteOptimizationResult) -> str:
        return self._save_document(
            "multisite-result",
            result_id,
            result.model_dump(mode="json"),
        )

    def load_multisite_result(self, result_id: str) -> MultiSiteOptimizationResult:
        return MultiSiteOptimizationResult.model_validate(
            self._load_document("multisite-result", result_id)
        )

    def save_run(self, run_id: str, run: OptimizationRun) -> str:
        return self._save_document("run", run_id, run.model_dump(mode="json"))

    def load_run(self, run_id: str) -> OptimizationRun:
        return OptimizationRun.model_validate(self._load_document("run", run_id))

    def list_runs(self) -> list[str]:
        return self._list_document_ids("run")

    def count_runs(self) -> int:
        return self._count_documents("run")

    def save_job(self, job_id: str, job: OptimizationJob) -> str:
        return self._save_document("job", job_id, job.model_dump(mode="json"))

    def load_job(self, job_id: str) -> OptimizationJob:
        return OptimizationJob.model_validate(self._load_document("job", job_id))

    def list_jobs(self) -> list[str]:
        return self._list_document_ids("job")

    def count_jobs(self) -> int:
        return self._count_documents("job")

    def save_job_input(self, job_id: str, payload: dict[str, Any]) -> str:
        return self._save_document("job-input", job_id, payload)

    def load_job_input(self, job_id: str) -> dict[str, Any]:
        return self._load_document("job-input", job_id)

    def delete_run(self, run_id: str) -> None:
        self._delete_document("run", run_id)

    def delete_job(self, job_id: str) -> None:
        self._delete_document("job", job_id)
        self._try_delete_document("job-input", job_id)

    def load_all_jobs(self) -> list[OptimizationJob]:
        return [OptimizationJob.model_validate(doc) for doc in self._load_all_documents("job")]

    def count_pending_jobs(self) -> int:
        try:
            with Session(self._engine) as session:
                statement = select(StoredDocument.payload_json).where(StoredDocument.kind == "job")
                rows = session.execute(statement).scalars().all()
                count = 0
                for row in rows:
                    doc = json.loads(row)
                    if doc.get("status") in {"queued", "running"}:
                        count += 1
                return count
        except SQLAlchemyError as exc:
            msg = "Failed to count pending jobs"
            raise RepositoryError(msg) from exc

    def load_all_runs(self) -> list[OptimizationRun]:
        return [OptimizationRun.model_validate(doc) for doc in self._load_all_documents("run")]

    def _save_document(self, kind: str, item_id: str, payload: dict[str, object]) -> str:
        safe_item_id = validate_item_id(item_id)
        now = datetime.now(tz=UTC)
        try:
            with Session(self._engine) as session:
                document = session.get(StoredDocument, {"kind": kind, "item_id": safe_item_id})
                if document is None:
                    document = StoredDocument(
                        kind=kind,
                        item_id=safe_item_id,
                        payload_json=json.dumps(payload),
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(document)
                else:
                    document.payload_json = json.dumps(payload)
                    document.updated_at = now
                session.commit()
        except (TypeError, ValueError) as exc:
            msg = f"Failed to serialize document {kind}/{safe_item_id}"
            raise RepositoryError(msg) from exc
        except SQLAlchemyError as exc:
            msg = f"Failed to persist document {kind}/{safe_item_id}"
            raise RepositoryError(msg) from exc
        return f"{self._safe_database_label}::{kind}/{safe_item_id}"

    def _load_document(self, kind: str, item_id: str) -> dict[str, object]:
        safe_item_id = validate_item_id(item_id)
        try:
            with Session(self._engine) as session:
                document = session.get(StoredDocument, {"kind": kind, "item_id": safe_item_id})
                if document is None:
                    msg = f"No stored document found for {kind}/{safe_item_id}"
                    raise StorageNotFoundError(msg)
                return cast("dict[str, object]", json.loads(document.payload_json))
        except (StorageNotFoundError, JsonPayloadError):
            raise
        except json.JSONDecodeError as exc:
            msg = (
                f"Stored JSON for {kind}/{safe_item_id} is invalid at "
                f"line {exc.lineno}, column {exc.colno}"
            )
            raise JsonPayloadError(msg) from exc
        except SQLAlchemyError as exc:
            msg = f"Failed to load document {kind}/{safe_item_id}"
            raise RepositoryError(msg) from exc

    def _list_document_ids(self, kind: str) -> list[str]:
        try:
            with Session(self._engine) as session:
                statement = select(StoredDocument.item_id).where(StoredDocument.kind == kind)
                return sorted(session.execute(statement).scalars().all())
        except SQLAlchemyError as exc:
            msg = f"Failed to list documents for kind {kind}"
            raise RepositoryError(msg) from exc

    def _load_all_documents(self, kind: str) -> list[dict[str, object]]:
        try:
            with Session(self._engine) as session:
                statement = select(StoredDocument).where(StoredDocument.kind == kind)
                documents = session.execute(statement).scalars().all()
                return [
                    cast("dict[str, object]", json.loads(doc.payload_json)) for doc in documents
                ]
        except json.JSONDecodeError as exc:
            msg = (
                f"Stored JSON for {kind} documents is invalid at "
                f"line {exc.lineno}, column {exc.colno}"
            )
            raise JsonPayloadError(msg) from exc
        except SQLAlchemyError as exc:
            msg = f"Failed to load all documents for kind {kind}"
            raise RepositoryError(msg) from exc

    def _count_documents(self, kind: str) -> int:
        try:
            with Session(self._engine) as session:
                statement = (
                    select(func.count())
                    .select_from(StoredDocument)
                    .where(StoredDocument.kind == kind)
                )
                return session.execute(statement).scalar_one()
        except SQLAlchemyError as exc:
            msg = f"Failed to count documents for kind {kind}"
            raise RepositoryError(msg) from exc

    def _delete_document(self, kind: str, item_id: str) -> None:
        safe_item_id = validate_item_id(item_id)
        try:
            with Session(self._engine) as session:
                document = session.get(StoredDocument, {"kind": kind, "item_id": safe_item_id})
                if document is None:
                    msg = f"No stored document found for {kind}/{safe_item_id}"
                    raise StorageNotFoundError(msg)
                session.delete(document)
                session.commit()
        except StorageNotFoundError:
            raise
        except SQLAlchemyError as exc:
            msg = f"Failed to delete document {kind}/{safe_item_id}"
            raise RepositoryError(msg) from exc

    def _try_delete_document(self, kind: str, item_id: str) -> None:
        safe_item_id = validate_item_id(item_id)
        try:
            with Session(self._engine) as session:
                document = session.get(StoredDocument, {"kind": kind, "item_id": safe_item_id})
                if document is not None:
                    session.delete(document)
                    session.commit()
        except SQLAlchemyError:
            pass

    @staticmethod
    def _redact_database_url(database_url: str) -> str:
        try:
            parsed = urlparse(database_url)
            if parsed.username or parsed.password:
                netloc = parsed.hostname or ""
                if parsed.port:
                    netloc = f"{netloc}:{parsed.port}"
                return urlunparse(parsed._replace(netloc=netloc))
        except Exception:  # noqa: S110
            pass
        return database_url

    @staticmethod
    def _ensure_parent_directory(database_url: str) -> None:
        sqlite_prefix = "sqlite:///"
        if database_url.startswith(sqlite_prefix):
            database_path = Path(database_url.removeprefix(sqlite_prefix))
            try:
                database_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                msg = f"Failed to create SQLite database directory for {database_url}"
                raise ConfigurationError(msg) from exc

    @staticmethod
    def _normalize_database_url(database_url: str) -> str:
        sqlite_prefix = "sqlite:///"
        if not database_url.startswith(sqlite_prefix):
            supported_prefixes = ("postgresql://", "postgresql+", "mysql://", "mysql+")
            if (
                not any(database_url.startswith(p) for p in supported_prefixes)
                and "://" not in database_url
            ):
                msg = f"Invalid database URL format: {database_url}"
                raise ConfigurationError(msg)
            return database_url
        path_part = database_url.removeprefix(sqlite_prefix).replace("\\", "/")
        return f"{sqlite_prefix}{path_part}"

    @staticmethod
    def _build_engine_kwargs(database_url: str) -> dict[str, object]:
        if database_url.startswith("sqlite://"):
            if database_url in {"sqlite://", "sqlite:///:memory:"}:
                return {
                    "future": True,
                    "poolclass": StaticPool,
                    "connect_args": {"check_same_thread": False},
                }
            return {
                "future": True,
                "connect_args": {"check_same_thread": False},
            }
        return {
            "future": True,
            "pool_pre_ping": True,
            "pool_size": settings.database_pool_size,
            "max_overflow": settings.database_max_overflow,
            "pool_recycle": settings.database_pool_recycle_seconds,
        }
