from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager, contextmanager
from time import perf_counter
from typing import TYPE_CHECKING, TypeVar, cast
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from smart_charging_optimization_engine.api.dashboard import render_dashboard_html
from smart_charging_optimization_engine.config import settings
from smart_charging_optimization_engine.domain.jobs import (
    JobStatus,
    OptimizationJob,
    OptimizationJobList,
)
from smart_charging_optimization_engine.domain.models import (
    ChargingScenario,
    PortfolioScenario,
    RollingHorizonRequest,
    TelemetryMessageEnvelope,
    TelemetrySnapshot,
)
from smart_charging_optimization_engine.domain.results import (
    MultiSiteOptimizationResult,
    OptimizationResult,
    RunComparison,
)
from smart_charging_optimization_engine.domain.runs import (
    OptimizationRun,
    OptimizationRunDigest,
    RunKind,
)
from smart_charging_optimization_engine.exceptions import (
    ApplicationError,
    ConfigurationError,
    InvalidIdentifierError,
    RepositoryError,
    StorageNotFoundError,
)
from smart_charging_optimization_engine.logging_utils import configure_logging
from smart_charging_optimization_engine.metrics import MetricsSnapshot, metrics
from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
from smart_charging_optimization_engine.optimization.multisite import (
    MultiSiteSmartChargingOptimizer,
)
from smart_charging_optimization_engine.services.job_queue import OptimizationJobService
from smart_charging_optimization_engine.services.rolling_horizon import (
    RollingHorizonOptimizer,
)
from smart_charging_optimization_engine.services.run_tracking import OptimizationRunService
from smart_charging_optimization_engine.services.telemetry_ingestion import (
    TelemetryIngestionService,
)
from smart_charging_optimization_engine.storage.factory import build_state_repository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

    from starlette.responses import Response

    from smart_charging_optimization_engine.storage.base import StateRepository


class StoredScenarioList(BaseModel):
    scenario_ids: list[str]
    total: int = 0


class StorageReceipt(BaseModel):
    item_id: str
    path: str


class StoredRunList(BaseModel):
    runs: list[OptimizationRunDigest]
    total: int = 0


class ErrorResponse(BaseModel):
    error: str
    details: str
    request_id: str
    issues: list[dict[str, str]] | None = None


configure_logging(settings.log_level, settings.log_format)
logger = logging.getLogger(__name__)

T = TypeVar("T")


class RequestBodyTooLargeError(RuntimeError):
    pass


_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def create_app() -> FastAPI:
    optimizer = SmartChargingOptimizer()
    rolling_optimizer = RollingHorizonOptimizer(optimizer)
    multi_site_optimizer = MultiSiteSmartChargingOptimizer()
    job_service = OptimizationJobService(build_state_repository)
    scheduled_job_tasks: set[asyncio.Task[object]] = set()

    @contextmanager
    def repository_scope() -> Iterator[StateRepository]:
        repository = build_state_repository()
        try:
            yield repository
        finally:
            repository.close()

    def with_repository(operation: Callable[[StateRepository], T]) -> T:
        with repository_scope() as repository:
            return operation(repository)

    def record_single_site_run(
        run_kind: RunKind,
        scenario: ChargingScenario,
        result: OptimizationResult,
        telemetry_snapshot_id: str | None = None,
        source_run_id: str | None = None,
    ) -> OptimizationRun:
        solver_config = (
            optimizer.solver_config
            if run_kind == RunKind.solve
            else rolling_optimizer.solver_config
        )
        return with_repository(
            lambda repository: OptimizationRunService(repository).record_single_site_run(
                run_kind,
                scenario,
                result,
                solver_config,
                telemetry_snapshot_id=telemetry_snapshot_id,
                source_run_id=source_run_id,
            )
        )

    def schedule_job_execution(job_id: str) -> None:
        task = asyncio.create_task(asyncio.to_thread(job_service.execute_job_from_payload, job_id))
        scheduled_job_tasks.add(task)
        task.add_done_callback(scheduled_job_tasks.discard)

    def record_multisite_run(
        scenario: PortfolioScenario,
        result: MultiSiteOptimizationResult,
    ) -> OptimizationRun:
        return with_repository(
            lambda repository: OptimizationRunService(repository).record_multisite_run(
                scenario,
                result,
                multi_site_optimizer.solver_config,
            )
        )

    async def schedule_persisted_jobs() -> int:
        recovered_jobs = await asyncio.to_thread(job_service.recover_stale_jobs)
        runnable_jobs = await asyncio.to_thread(job_service.list_runnable_jobs)
        queued_job_ids = {
            job.job_id
            for job in (*recovered_jobs, *runnable_jobs)
            if job.status == JobStatus.queued
        }
        for job_id in queued_job_ids:
            schedule_job_execution(job_id)
        return len(queued_job_ids)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            logger.info(
                "Starting service",
                extra={
                    "service": settings.api_title,
                    "version": settings.api_version,
                    "repository_backend": settings.state_repository_backend,
                    "log_format": settings.log_format,
                    "job_process_isolation": settings.job_process_isolation,
                },
            )
            scheduled_jobs = await schedule_persisted_jobs()
            if scheduled_jobs > 0:
                logger.info(
                    "Scheduled %s persisted optimization job(s) on startup",
                    scheduled_jobs,
                    extra={"scheduled_jobs": scheduled_jobs},
                )
            yield
        finally:
            for task in list(scheduled_job_tasks):
                task.cancel()
            logger.info(
                "Stopping service",
                extra={"service": settings.api_title, "version": settings.api_version},
            )

    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description="Day-ahead and intra-day EV fleet charging optimization service.",
        lifespan=lifespan,
    )

    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["X-Request-ID", "Content-Type"],
        )

    @app.middleware("http")
    async def enforce_request_body_limit(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
                if length < 0:
                    return JSONResponse(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        content={
                            "error": "invalid_content_length",
                            "details": "Content-Length must be non-negative",
                            "request_id": getattr(request.state, "request_id", uuid4().hex),
                        },
                    )
                if length > settings.max_request_body_bytes:
                    return JSONResponse(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        content={
                            "error": "payload_too_large",
                            "details": (
                                f"Request body exceeds the "
                                f"{settings.max_request_body_bytes} byte limit"
                            ),
                            "request_id": getattr(request.state, "request_id", uuid4().hex),
                        },
                    )
            except ValueError:
                pass
        received_bytes = 0
        over_limit = False
        original_receive = request._receive

        async def limited_receive() -> dict[str, object]:
            nonlocal received_bytes, over_limit
            message = cast("dict[str, object]", await original_receive())
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    received_bytes += len(body)
                if received_bytes > settings.max_request_body_bytes:
                    over_limit = True
                    raise RequestBodyTooLargeError
            return message

        request._receive = limited_receive
        try:
            response = await call_next(request)
        except RequestBodyTooLargeError:
            response = None
        if over_limit or response is None:
            request_id = getattr(request.state, "request_id", uuid4().hex)
            return JSONResponse(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content={
                    "error": "payload_too_large",
                    "details": (
                        f"Request body exceeds the {settings.max_request_body_bytes} byte limit"
                    ),
                    "request_id": request_id,
                },
            )
        return response

    @app.middleware("http")
    async def add_request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        raw_request_id = request.headers.get("x-request-id") or ""
        if raw_request_id and _REQUEST_ID_PATTERN.fullmatch(raw_request_id):
            request_id = raw_request_id
        else:
            request_id = uuid4().hex
        request.state.request_id = request_id
        start_time = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled request failure request_id=%s method=%s path=%s",
                request_id,
                request.method,
                request.url.path,
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise
        duration_ms = (perf_counter() - start_time) * 1000
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        path_label = getattr(route, "path", request.url.path)
        metrics.increment(
            "http_requests_total",
            method=request.method,
            path=path_label,
            status_code=str(response.status_code),
        )
        metrics.observe(
            "http_request_duration_ms",
            duration_ms,
            method=request.method,
            path=path_label,
        )
        logger.info(
            "Completed request request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "route": path_label,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response

    def build_error_response(
        request: Request,
        error: str,
        details: str,
        status_code: int,
        issues: list[dict[str, str]] | None = None,
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", uuid4().hex)
        payload = ErrorResponse(
            error=error,
            details=details,
            request_id=request_id,
            issues=issues,
        )
        response = JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))
        response.headers["X-Request-ID"] = request_id
        return response

    def error_details(exc: Exception) -> str:
        return str(exc.args[0]) if exc.args else str(exc)

    def format_validation_issues(exc: RequestValidationError) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", [])) or "body"
            issues.append(
                {
                    "location": location,
                    "message": error.get("msg", "Invalid value"),
                    "type": error.get("type", "validation_error"),
                }
            )
        return issues

    @app.exception_handler(StorageNotFoundError)
    def handle_not_found(request: Request, exc: StorageNotFoundError) -> JSONResponse:
        return build_error_response(
            request,
            "not_found",
            error_details(exc),
            status.HTTP_404_NOT_FOUND,
        )

    @app.exception_handler(InvalidIdentifierError)
    def handle_invalid_identifier(request: Request, exc: InvalidIdentifierError) -> JSONResponse:
        return build_error_response(
            request,
            "invalid_identifier",
            error_details(exc),
            status.HTTP_400_BAD_REQUEST,
        )

    @app.exception_handler(ConfigurationError)
    def handle_configuration_error(request: Request, exc: ConfigurationError) -> JSONResponse:
        return build_error_response(
            request,
            "configuration_error",
            error_details(exc),
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    @app.exception_handler(RepositoryError)
    def handle_repository_error(request: Request, exc: RepositoryError) -> JSONResponse:
        return build_error_response(
            request,
            "repository_error",
            error_details(exc),
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.exception_handler(ApplicationError)
    def handle_application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        return build_error_response(
            request,
            "application_error",
            error_details(exc),
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    @app.exception_handler(ValueError)
    def handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
        return build_error_response(
            request,
            "invalid_request",
            error_details(exc),
            status.HTTP_400_BAD_REQUEST,
        )

    @app.exception_handler(RequestValidationError)
    def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        issues = format_validation_issues(exc)
        if len(issues) == 1:
            details = issues[0]["message"]
        else:
            details = f"Request validation failed with {len(issues)} issue(s)"
        return build_error_response(
            request,
            "validation_error",
            details,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            issues=issues,
        )

    @app.exception_handler(StarletteHTTPException)
    def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return build_error_response(
            request,
            "http_error",
            str(exc.detail),
            exc.status_code,
        )

    @app.exception_handler(RequestBodyTooLargeError)
    def handle_body_too_large(request: Request, exc: RequestBodyTooLargeError) -> JSONResponse:
        return build_error_response(
            request,
            "payload_too_large",
            f"Request body exceeds the {settings.max_request_body_bytes} byte limit",
            status.HTTP_413_CONTENT_TOO_LARGE,
        )

    @app.exception_handler(Exception)
    def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Returning generic 500 for request_id=%s",
            getattr(request.state, "request_id", "unknown"),
            exc_info=exc,
            extra={"request_id": getattr(request.state, "request_id", "unknown")},
        )
        return build_error_response(
            request,
            "internal_error",
            "An unexpected error occurred while processing the request",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    @app.get("/health")
    def health() -> dict[str, object]:
        health_status: dict[str, object] = {
            "status": "ok",
            "service": settings.api_title,
            "version": settings.api_version,
            "repository_backend": settings.state_repository_backend,
            "job_process_isolation": settings.job_process_isolation,
            "log_format": settings.log_format,
        }
        try:
            with_repository(lambda repository: repository.list_scenarios())
            health_status["database"] = "connected"
        except Exception:
            health_status["database"] = "unreachable"
            health_status["status"] = "degraded"
        return health_status

    @app.get("/health/ready")
    def readiness() -> JSONResponse:
        try:
            with_repository(lambda repository: repository.list_scenarios())
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"ready": False, "reason": "database unreachable"},
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"ready": True},
        )

    @app.get("/metrics")
    def get_metrics() -> MetricsSnapshot:
        return metrics.snapshot()

    @app.get("/metrics/prometheus", response_class=PlainTextResponse)
    def get_prometheus_metrics() -> PlainTextResponse:
        return PlainTextResponse(
            metrics.render_prometheus(),
            media_type="text/plain; version=0.0.4",
        )

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(render_dashboard_html())

    @app.post("/solve", response_model=OptimizationResult)
    async def solve_scenario(request: Request, scenario: ChargingScenario) -> OptimizationResult:
        logger.info(
            "Solve requested",
            extra={
                "action": "solve",
                "scenario_name": scenario.metadata.scenario_name,
                "vehicle_count": len(scenario.vehicles),
                "request_id": getattr(request.state, "request_id", None),
                "client_host": request.client.host if request.client else None,
            },
        )
        result = await asyncio.to_thread(optimizer.solve, scenario)
        run = await asyncio.to_thread(
            record_single_site_run,
            RunKind.solve,
            scenario,
            result,
        )
        return run.result if run.result is not None else result

    @app.post("/solve/multisite", response_model=MultiSiteOptimizationResult)
    async def solve_multisite_scenario(
        request: Request, scenario: PortfolioScenario
    ) -> MultiSiteOptimizationResult:
        logger.info(
            "Multisite solve requested",
            extra={
                "action": "solve_multisite",
                "scenario_name": scenario.metadata.scenario_name,
                "site_count": len(scenario.sites),
                "request_id": getattr(request.state, "request_id", None),
                "client_host": request.client.host if request.client else None,
            },
        )
        result = await asyncio.to_thread(multi_site_optimizer.solve, scenario)
        run = await asyncio.to_thread(
            record_multisite_run,
            scenario,
            result,
        )
        return run.multisite_result if run.multisite_result is not None else result

    @app.post("/reoptimize", response_model=OptimizationResult)
    async def reoptimize_scenario(
        request: Request, rolling_request: RollingHorizonRequest
    ) -> OptimizationResult:
        logger.info(
            "Reoptimize requested",
            extra={
                "action": "reoptimize",
                "scenario_name": rolling_request.scenario.metadata.scenario_name,
                "snapshot_id": rolling_request.telemetry.snapshot_id,
                "request_id": getattr(request.state, "request_id", None),
                "client_host": request.client.host if request.client else None,
            },
        )
        result = await asyncio.to_thread(rolling_optimizer.reoptimize, rolling_request)
        run = await asyncio.to_thread(
            record_single_site_run,
            RunKind.reoptimize,
            rolling_request.scenario,
            result,
            telemetry_snapshot_id=rolling_request.telemetry.snapshot_id,
            source_run_id=rolling_request.source_run_id,
        )
        return run.result if run.result is not None else result

    @app.post("/validate")
    def validate_scenario(scenario: ChargingScenario) -> dict[str, bool | str]:
        return {"valid": True, "scenario_name": scenario.metadata.scenario_name}

    @app.post("/storage/scenarios/{scenario_id}", response_model=StorageReceipt)
    def store_scenario(
        request: Request, scenario_id: str, scenario: ChargingScenario
    ) -> StorageReceipt:
        logger.info(
            "Storing scenario",
            extra={
                "action": "store_scenario",
                "scenario_id": scenario_id,
                "request_id": getattr(request.state, "request_id", None),
                "client_host": request.client.host if request.client else None,
            },
        )
        destination = with_repository(
            lambda repository: repository.save_scenario(scenario_id, scenario)
        )
        return StorageReceipt(item_id=scenario_id, path=str(destination))

    @app.get("/storage/scenarios/{scenario_id}", response_model=ChargingScenario)
    def get_scenario(scenario_id: str) -> ChargingScenario:
        return with_repository(lambda repository: repository.load_scenario(scenario_id))

    @app.get("/storage/scenarios", response_model=StoredScenarioList)
    def list_scenarios(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> StoredScenarioList:
        def load_scenario_page(repository: StateRepository) -> tuple[int, list[str]]:
            all_ids = repository.list_scenarios()
            return len(all_ids), all_ids[offset : offset + limit]

        total, scenario_ids = with_repository(load_scenario_page)
        return StoredScenarioList(
            scenario_ids=scenario_ids,
            total=total,
        )

    @app.post("/storage/telemetry/{snapshot_id}", response_model=StorageReceipt)
    def store_telemetry(snapshot_id: str, telemetry: TelemetrySnapshot) -> StorageReceipt:
        destination = with_repository(
            lambda repository: repository.save_telemetry(snapshot_id, telemetry)
        )
        return StorageReceipt(item_id=snapshot_id, path=str(destination))

    @app.post("/ingestion/telemetry-envelope", response_model=StorageReceipt)
    def ingest_telemetry_envelope(envelope: TelemetryMessageEnvelope) -> StorageReceipt:
        destination = with_repository(
            lambda repository: TelemetryIngestionService(repository).ingest_envelope(envelope)
        )
        return StorageReceipt(item_id=envelope.telemetry.snapshot_id, path=str(destination))

    @app.get("/runs", response_model=StoredRunList)
    def list_runs(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> StoredRunList:
        def load_run_page(repository: StateRepository) -> tuple[int, list[OptimizationRunDigest]]:
            run_service = OptimizationRunService(repository)
            return repository.count_runs(), run_service.list_runs(offset=offset, limit=limit)

        total, runs = with_repository(load_run_page)
        return StoredRunList(runs=runs, total=total)

    @app.get("/runs/compare", response_model=RunComparison)
    def compare_runs(baseline_run_id: str, candidate_run_id: str) -> RunComparison:
        return with_repository(
            lambda repository: OptimizationRunService(repository).compare_runs(
                baseline_run_id,
                candidate_run_id,
            )
        )

    @app.get("/runs/{run_id}", response_model=OptimizationRun)
    def get_run(run_id: str) -> OptimizationRun:
        return with_repository(
            lambda repository: OptimizationRunService(repository).get_run(run_id)
        )

    @app.delete("/runs/{run_id}", status_code=204)
    def delete_run(request: Request, run_id: str) -> None:
        logger.info(
            "Deleting run",
            extra={
                "action": "delete_run",
                "run_id": run_id,
                "request_id": getattr(request.state, "request_id", None),
                "client_host": request.client.host if request.client else None,
            },
        )

        def delete_existing_run(repository: StateRepository) -> None:
            repository.load_run(run_id)
            repository.delete_run(run_id)

        with_repository(delete_existing_run)

    @app.get("/jobs", response_model=OptimizationJobList)
    def list_jobs(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> OptimizationJobList:
        all_jobs = job_service.list_jobs()
        total = len(all_jobs)
        return OptimizationJobList(jobs=all_jobs[offset : offset + limit], total=total)

    @app.post("/jobs/recover-stale", response_model=OptimizationJobList, status_code=202)
    def recover_stale_jobs(
        background_tasks: BackgroundTasks,
        reschedule: bool = True,
    ) -> OptimizationJobList:
        recovered_jobs = job_service.recover_stale_jobs()
        if reschedule:
            for job in recovered_jobs:
                if job.status == JobStatus.queued:
                    background_tasks.add_task(job_service.execute_job_from_payload, job.job_id)
        return OptimizationJobList(jobs=recovered_jobs)

    @app.get("/jobs/{job_id}", response_model=OptimizationJob)
    def get_job(job_id: str) -> OptimizationJob:
        return job_service.get_job(job_id)

    @app.delete("/jobs/{job_id}", status_code=204)
    def delete_job(request: Request, job_id: str) -> None:
        job = job_service.get_job(job_id)
        if job.status == JobStatus.running:
            msg = f"Cannot delete job {job_id} while it is running"
            raise ValueError(msg)
        logger.info(
            "Deleting job",
            extra={
                "action": "delete_job",
                "job_id": job_id,
                "request_id": getattr(request.state, "request_id", None),
                "client_host": request.client.host if request.client else None,
            },
        )
        job_service.delete_job(job_id)

    @app.post("/jobs/{job_id}/retry", response_model=OptimizationJob, status_code=202)
    def retry_job(job_id: str, background_tasks: BackgroundTasks) -> OptimizationJob:
        job = job_service.retry_job(job_id)
        background_tasks.add_task(job_service.execute_job_from_payload, job.job_id)
        return job

    @app.post("/jobs/solve", response_model=OptimizationJob, status_code=202)
    def enqueue_solve_job(
        request: Request,
        scenario: ChargingScenario,
        background_tasks: BackgroundTasks,
    ) -> OptimizationJob:
        job = job_service.create_solve_job(scenario)
        logger.info(
            "Job enqueued",
            extra={
                "action": "enqueue_job",
                "job_id": job.job_id,
                "run_kind": "solve",
                "request_id": getattr(request.state, "request_id", None),
                "client_host": request.client.host if request.client else None,
            },
        )
        background_tasks.add_task(job_service.execute_solve_job, job.job_id, scenario)
        return job

    @app.post("/jobs/reoptimize", response_model=OptimizationJob, status_code=202)
    def enqueue_reoptimize_job(
        request: Request,
        rolling_request: RollingHorizonRequest,
        background_tasks: BackgroundTasks,
    ) -> OptimizationJob:
        job = job_service.create_reoptimize_job(rolling_request)
        logger.info(
            "Job enqueued",
            extra={
                "action": "enqueue_job",
                "job_id": job.job_id,
                "run_kind": "reoptimize",
                "request_id": getattr(request.state, "request_id", None),
                "client_host": request.client.host if request.client else None,
            },
        )
        background_tasks.add_task(job_service.execute_reoptimize_job, job.job_id, rolling_request)
        return job

    @app.post("/jobs/solve/multisite", response_model=OptimizationJob, status_code=202)
    def enqueue_multisite_job(
        request: Request,
        scenario: PortfolioScenario,
        background_tasks: BackgroundTasks,
    ) -> OptimizationJob:
        job = job_service.create_multisite_job(scenario)
        logger.info(
            "Job enqueued",
            extra={
                "action": "enqueue_job",
                "job_id": job.job_id,
                "run_kind": "multisite",
                "request_id": getattr(request.state, "request_id", None),
                "client_host": request.client.host if request.client else None,
            },
        )
        background_tasks.add_task(job_service.execute_multisite_job, job.job_id, scenario)
        return job

    return app


app = create_app()
