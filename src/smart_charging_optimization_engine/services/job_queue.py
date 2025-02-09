from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from queue import Empty
from threading import Event, Thread
from time import perf_counter, sleep
from typing import Any
from uuid import uuid4

from smart_charging_optimization_engine.config import settings
from smart_charging_optimization_engine.domain.jobs import JobStatus, OptimizationJob
from smart_charging_optimization_engine.domain.models import (
    ChargingScenario,
    PortfolioScenario,
    RollingHorizonRequest,
)
from smart_charging_optimization_engine.domain.runs import OptimizationRun, RunKind
from smart_charging_optimization_engine.exceptions import JobStateError, JobTimeoutError
from smart_charging_optimization_engine.metrics import metrics
from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
from smart_charging_optimization_engine.optimization.multisite import (
    MultiSiteSmartChargingOptimizer,
)
from smart_charging_optimization_engine.services.job_worker import execute_job_in_subprocess
from smart_charging_optimization_engine.services.rolling_horizon import RollingHorizonOptimizer
from smart_charging_optimization_engine.services.run_tracking import OptimizationRunService
from smart_charging_optimization_engine.storage.base import StateRepository
from smart_charging_optimization_engine.storage.factory import (
    RepositoryDescriptor,
    repository_descriptor_from_settings,
)

RepositoryFactory = Callable[[], StateRepository]

logger = logging.getLogger(__name__)

_MAX_TRANSITION_ATTEMPTS = 5


class OptimizationJobService:
    def __init__(
        self,
        repository_factory: RepositoryFactory,
        max_attempts: int | None = None,
        timeout_seconds: float | None = None,
        retry_backoff_seconds: float | None = None,
        heartbeat_interval_seconds: float | None = None,
        stale_threshold_seconds: float | None = None,
        process_isolation: bool | None = None,
        executor_id: str | None = None,
        repository_descriptor: RepositoryDescriptor | None = None,
        max_queue_depth: int | None = None,
    ) -> None:
        self._repository_factory = repository_factory
        self._max_attempts = max_attempts if max_attempts is not None else settings.job_max_attempts
        self._timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.job_timeout_seconds
        )
        self._retry_backoff_seconds = (
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else settings.job_retry_backoff_seconds
        )
        self._heartbeat_interval_seconds = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else settings.job_heartbeat_interval_seconds
        )
        self._stale_threshold_seconds = (
            stale_threshold_seconds
            if stale_threshold_seconds is not None
            else settings.job_stale_threshold_seconds
        )
        self._process_isolation = (
            process_isolation if process_isolation is not None else settings.job_process_isolation
        )
        self._executor_id = executor_id or f"{socket.gethostname()}:{os.getpid()}"
        self._repository_descriptor = repository_descriptor or repository_descriptor_from_settings()
        self._max_queue_depth = (
            max_queue_depth if max_queue_depth is not None else settings.job_max_queue_depth
        )

    def create_solve_job(self, scenario: ChargingScenario) -> OptimizationJob:
        return self._create_job(
            RunKind.solve,
            scenario.metadata.scenario_name,
            scenario.model_dump(mode="json"),
        )

    def create_reoptimize_job(self, request: RollingHorizonRequest) -> OptimizationJob:
        return self._create_job(
            RunKind.reoptimize,
            request.scenario.metadata.scenario_name,
            request.model_dump(mode="json"),
            telemetry_snapshot_id=request.telemetry.snapshot_id,
            source_run_id=request.source_run_id,
        )

    def create_multisite_job(self, scenario: PortfolioScenario) -> OptimizationJob:
        return self._create_job(
            RunKind.multisite,
            scenario.metadata.scenario_name,
            scenario.model_dump(mode="json"),
        )

    def get_job(self, job_id: str) -> OptimizationJob:
        repository = self._repository_factory()
        try:
            return repository.load_job(job_id)
        finally:
            repository.close()

    def list_jobs(self) -> list[OptimizationJob]:
        repository = self._repository_factory()
        try:
            jobs = repository.load_all_jobs()
        finally:
            repository.close()
        jobs.sort(key=lambda job: job.submitted_at, reverse=True)
        return jobs

    def delete_job(self, job_id: str) -> None:
        repository = self._repository_factory()
        try:
            repository.delete_job(job_id)
            metrics.increment("jobs_deleted_total")
        finally:
            repository.close()

    def execute_solve_job(self, job_id: str, scenario: ChargingScenario) -> None:
        self._persist_job_input(job_id, scenario.model_dump(mode="json"))
        self.execute_job_from_payload(job_id)

    def execute_reoptimize_job(self, job_id: str, request: RollingHorizonRequest) -> None:
        self._persist_job_input(job_id, request.model_dump(mode="json"))
        self.execute_job_from_payload(job_id)

    def execute_multisite_job(self, job_id: str, scenario: PortfolioScenario) -> None:
        self._persist_job_input(job_id, scenario.model_dump(mode="json"))
        self.execute_job_from_payload(job_id)

    def execute_job_from_payload(self, job_id: str) -> None:
        repository = self._repository_factory()
        try:
            job = repository.load_job(job_id)
            payload = repository.load_job_input(job_id)
        finally:
            repository.close()
        self._execute_job(job, payload)

    def retry_job(self, job_id: str) -> OptimizationJob:
        repository = self._repository_factory()
        try:
            job = repository.load_job(job_id)
            repository.load_job_input(job_id)
            if job.status == JobStatus.running:
                msg = f"Job {job_id} is currently running and cannot be retried"
                raise ValueError(msg)
            if job.status == JobStatus.succeeded:
                msg = f"Job {job_id} already succeeded and cannot be retried"
                raise ValueError(msg)
            retried_job = job.model_copy(
                update={
                    "status": JobStatus.queued,
                    "started_at": None,
                    "last_heartbeat_at": None,
                    "completed_at": None,
                    "retry_after": datetime.now(tz=UTC),
                    "execution_token": None,
                    "executor_id": None,
                    "run_id": None,
                    "result_summary": None,
                    "error_message": None,
                },
                deep=True,
            )
            repository.save_job(job_id, retried_job)
            metrics.increment("jobs_retried_total", run_kind=retried_job.run_kind.value)
            return retried_job
        finally:
            repository.close()

    def recover_stale_jobs(self) -> list[OptimizationJob]:
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=self._stale_threshold_seconds)
        repository = self._repository_factory()
        recovered_jobs: list[OptimizationJob] = []
        try:
            for job in repository.load_all_jobs():
                if job.status != JobStatus.running:
                    continue
                reference_time = job.last_heartbeat_at or job.started_at
                if reference_time is None or reference_time > cutoff:
                    continue
                if job.attempts < job.max_attempts:
                    recovered = self._queue_retry(
                        job.job_id,
                        self._format_timeout(job, self._stale_threshold_seconds, stale=True),
                        expected_execution_token=job.execution_token,
                        expected_status=JobStatus.running,
                    )
                else:
                    recovered = self._mark_failed(
                        job.job_id,
                        self._format_timeout(job, self._stale_threshold_seconds, stale=True),
                        JobStatus.timed_out,
                        expected_execution_token=job.execution_token,
                        expected_status=JobStatus.running,
                    )
                if recovered is None:
                    continue
                recovered_jobs.append(recovered)
                metrics.increment("jobs_recovered_total", run_kind=job.run_kind.value)
            return recovered_jobs
        finally:
            repository.close()

    def list_runnable_jobs(self) -> list[OptimizationJob]:
        repository = self._repository_factory()
        try:
            now = datetime.now(tz=UTC)
            runnable_jobs = [
                job
                for job in repository.load_all_jobs()
                if job.status == JobStatus.queued
                and (job.retry_after is None or job.retry_after <= now)
            ]
        finally:
            repository.close()
        runnable_jobs.sort(key=lambda job: job.submitted_at)
        return runnable_jobs

    def _execute_job(self, job: OptimizationJob, payload: dict[str, Any]) -> None:
        transition_failures = 0
        while True:
            repository = self._repository_factory()
            running_job: OptimizationJob | None = None
            started_at = perf_counter()
            try:
                running_job = self._mark_running(repository, job.job_id)
                transition_failures = 0
                execution_token = running_job.execution_token
                if execution_token is None:
                    msg = f"Job {job.job_id} started without an execution token"
                    raise JobStateError(msg)
                with self._job_heartbeat(job.job_id, execution_token):
                    run = self._run_attempt(running_job, payload, repository)
                duration = perf_counter() - started_at
                if not self._mark_succeeded(repository, job.job_id, run, duration, execution_token):
                    logger.warning(
                        "Skipping success update for job %s because ownership changed",
                        job.job_id,
                        extra={"job_id": job.job_id, "executor_id": self._executor_id},
                    )
                    return
                metrics.increment(
                    "jobs_completed_total",
                    run_kind=running_job.run_kind.value,
                    status=JobStatus.succeeded.value,
                )
                metrics.observe(
                    "job_duration_seconds",
                    duration,
                    run_kind=running_job.run_kind.value,
                )
                return
            except Exception as exc:
                if running_job is None:
                    if isinstance(exc, JobStateError):
                        transition_failures += 1
                        if transition_failures >= _MAX_TRANSITION_ATTEMPTS:
                            logger.error(
                                "Giving up on job %s after %s transition failures",
                                job.job_id,
                                transition_failures,
                                extra={
                                    "job_id": job.job_id,
                                    "executor_id": self._executor_id,
                                },
                            )
                            self._mark_failed(
                                job.job_id,
                                self._format_exception(exc),
                                JobStatus.failed,
                            )
                            return
                        logger.info(
                            "Skipping execution for job %s: %s",
                            job.job_id,
                            exc,
                            extra={"job_id": job.job_id, "executor_id": self._executor_id},
                        )
                        metrics.increment(
                            "jobs_skipped_total",
                            reason="state",
                            run_kind=job.run_kind.value,
                        )
                        return
                    logger.exception(
                        "Optimization job %s failed before execution started",
                        job.job_id,
                        extra={"job_id": job.job_id, "executor_id": self._executor_id},
                    )
                    self._mark_failed(job.job_id, self._format_exception(exc), JobStatus.failed)
                    return
                logger.exception(
                    "Optimization job %s failed on attempt %s",
                    job.job_id,
                    running_job.attempts,
                    extra={
                        "job_id": job.job_id,
                        "attempt": running_job.attempts,
                        "executor_id": self._executor_id,
                    },
                )
                error_message = self._format_exception(exc)
                terminal_status = (
                    JobStatus.timed_out if isinstance(exc, JobTimeoutError) else JobStatus.failed
                )
                metrics.increment(
                    "jobs_completed_total",
                    run_kind=running_job.run_kind.value,
                    status=terminal_status.value,
                )
                if running_job.attempts < running_job.max_attempts:
                    queued_job = self._queue_retry(
                        job.job_id,
                        error_message,
                        expected_execution_token=running_job.execution_token,
                        expected_status=JobStatus.running,
                    )
                    if queued_job is None:
                        logger.warning(
                            "Skipping retry scheduling for job %s because ownership changed",
                            job.job_id,
                            extra={"job_id": job.job_id, "executor_id": self._executor_id},
                        )
                        return
                    if queued_job.retry_after is not None:
                        delay_seconds = max(
                            (queued_job.retry_after - datetime.now(tz=UTC)).total_seconds(),
                            0.0,
                        )
                        if delay_seconds > 0.0:
                            sleep(delay_seconds)
                    continue
                failed_job = self._mark_failed(
                    job.job_id,
                    error_message,
                    terminal_status,
                    expected_execution_token=running_job.execution_token,
                    expected_status=JobStatus.running,
                )
                if failed_job is None:
                    logger.warning(
                        "Skipping terminal failure update for job %s because ownership changed",
                        job.job_id,
                        extra={"job_id": job.job_id, "executor_id": self._executor_id},
                    )
                return
            finally:
                repository.close()

    def _run_attempt(
        self,
        job: OptimizationJob,
        payload: dict[str, Any],
        repository: StateRepository,
    ) -> OptimizationRun:
        if self._process_isolation:
            return self._run_attempt_isolated(job, payload)
        return self._run_attempt_inline(repository, job.run_kind, payload)

    def _run_attempt_isolated(
        self,
        job: OptimizationJob,
        payload: dict[str, Any],
    ) -> OptimizationRun:
        context = get_context("spawn")
        result_queue = context.Queue()
        process = context.Process(
            target=execute_job_in_subprocess,
            args=(
                self._repository_descriptor,
                job.run_kind.value,
                payload,
                result_queue,
            ),
        )
        process.start()
        process.join(job.timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=5.0)
            msg = self._format_timeout(job, job.timeout_seconds)
            raise JobTimeoutError(msg)
        try:
            outcome = result_queue.get(timeout=5.0)
        except Empty as exc:
            msg = f"Isolated worker for job {job.job_id} exited without reporting a result"
            raise RuntimeError(msg) from exc
        if outcome["status"] != "succeeded" or outcome["run_id"] is None:
            error_type = outcome["error_type"] or "RuntimeError"
            error_message = outcome["error_message"] or "worker failed without details"
            raise RuntimeError(f"{error_type}: {error_message}")
        repository = self._repository_factory()
        try:
            return repository.load_run(outcome["run_id"])
        finally:
            repository.close()

    @staticmethod
    def _run_attempt_inline(
        repository: StateRepository,
        run_kind: RunKind,
        payload: dict[str, Any],
    ) -> OptimizationRun:
        if run_kind == RunKind.solve:
            scenario = ChargingScenario.model_validate(payload)
            return OptimizationJobService._run_solve(repository, scenario)
        if run_kind == RunKind.reoptimize:
            request = RollingHorizonRequest.model_validate(payload)
            return OptimizationJobService._run_reoptimize(repository, request)
        if run_kind == RunKind.multisite:
            multisite_scenario = PortfolioScenario.model_validate(payload)
            return OptimizationJobService._run_multisite(repository, multisite_scenario)
        msg = f"Unsupported job run kind: {run_kind}"
        raise ValueError(msg)

    @staticmethod
    def _run_solve(repository: StateRepository, scenario: ChargingScenario) -> OptimizationRun:
        optimizer = SmartChargingOptimizer()
        result = optimizer.solve(scenario)
        return OptimizationRunService(repository).record_single_site_run(
            RunKind.solve,
            scenario,
            result,
            optimizer.solver_config,
        )

    @staticmethod
    def _run_reoptimize(
        repository: StateRepository,
        request: RollingHorizonRequest,
    ) -> OptimizationRun:
        optimizer = RollingHorizonOptimizer()
        result = optimizer.reoptimize(request)
        return OptimizationRunService(repository).record_single_site_run(
            RunKind.reoptimize,
            request.scenario,
            result,
            optimizer.solver_config,
            telemetry_snapshot_id=request.telemetry.snapshot_id,
            source_run_id=request.source_run_id,
        )

    @staticmethod
    def _run_multisite(repository: StateRepository, scenario: PortfolioScenario) -> OptimizationRun:
        optimizer = MultiSiteSmartChargingOptimizer()
        result = optimizer.solve(scenario)
        return OptimizationRunService(repository).record_multisite_run(
            scenario,
            result,
            optimizer.solver_config,
        )

    def _create_job(
        self,
        run_kind: RunKind,
        scenario_name: str,
        payload: dict[str, Any],
        telemetry_snapshot_id: str | None = None,
        source_run_id: str | None = None,
    ) -> OptimizationJob:
        repository = self._repository_factory()
        try:
            pending_count = repository.count_pending_jobs()
            if pending_count >= self._max_queue_depth:
                msg = (
                    f"Job queue depth limit reached ({self._max_queue_depth}). "
                    "Wait for existing jobs to complete before submitting new ones."
                )
                raise ValueError(msg)
            job = OptimizationJob(
                job_id=self._build_job_id(run_kind),
                run_kind=run_kind,
                status=JobStatus.queued,
                scenario_name=scenario_name,
                submitted_at=datetime.now(tz=UTC),
                max_attempts=self._max_attempts,
                timeout_seconds=self._timeout_seconds,
                retry_backoff_seconds=self._retry_backoff_seconds,
                telemetry_snapshot_id=telemetry_snapshot_id,
                source_run_id=source_run_id,
            )
            repository.save_job(job.job_id, job)
            repository.save_job_input(job.job_id, payload)
            metrics.increment("jobs_created_total", run_kind=run_kind.value)
            return job
        finally:
            repository.close()

    def _mark_running(self, repository: StateRepository, job_id: str) -> OptimizationJob:
        job = repository.load_job(job_id)
        self._ensure_job_can_start(job)
        now = datetime.now(tz=UTC)
        execution_token = uuid4().hex
        running_job = job.model_copy(
            update={
                "status": JobStatus.running,
                "attempts": job.attempts + 1,
                "started_at": now,
                "last_heartbeat_at": now,
                "completed_at": None,
                "retry_after": None,
                "execution_token": execution_token,
                "executor_id": self._executor_id,
                "run_id": None,
                "result_summary": None,
                "error_message": None,
            },
            deep=True,
        )
        repository.save_job(job_id, running_job)
        persisted_job = repository.load_job(job_id)
        if (
            persisted_job.execution_token != execution_token
            or persisted_job.status != JobStatus.running
        ):
            msg = f"Job {job_id} was claimed by another executor before it could start"
            raise JobStateError(msg)
        metrics.increment("jobs_started_total", run_kind=persisted_job.run_kind.value)
        return persisted_job

    def _mark_succeeded(
        self,
        repository: StateRepository,
        job_id: str,
        run: OptimizationRun,
        duration_seconds: float,
        expected_execution_token: str,
    ) -> bool:
        current_job = repository.load_job(job_id)
        if (
            current_job.status != JobStatus.running
            or current_job.execution_token != expected_execution_token
        ):
            return False
        now = datetime.now(tz=UTC)
        completed_job = current_job.model_copy(
            update={
                "status": JobStatus.succeeded,
                "last_heartbeat_at": now,
                "completed_at": now,
                "execution_token": None,
                "executor_id": None,
                "run_id": run.run_id,
                "result_summary": run.summary.model_dump_json(),
                "error_message": None,
            },
            deep=True,
        )
        repository.save_job(job_id, completed_job)
        metrics.observe(
            "job_result_total_cost",
            run.summary.total_cost,
            run_kind=run.run_kind.value,
        )
        metrics.observe("job_solve_time_seconds", duration_seconds, run_kind=run.run_kind.value)
        return True

    def _mark_failed(
        self,
        job_id: str,
        error_message: str,
        status: JobStatus = JobStatus.failed,
        expected_execution_token: str | None = None,
        expected_status: JobStatus | None = None,
    ) -> OptimizationJob | None:
        repository = self._repository_factory()
        try:
            current_job = repository.load_job(job_id)
            if not self._matches_expected_state(
                current_job,
                expected_status=expected_status,
                expected_execution_token=expected_execution_token,
            ):
                return None
            failed_job = current_job.model_copy(
                update={
                    "status": status,
                    "last_heartbeat_at": datetime.now(tz=UTC),
                    "completed_at": datetime.now(tz=UTC),
                    "retry_after": None,
                    "execution_token": None,
                    "executor_id": None,
                    "error_message": error_message,
                },
                deep=True,
            )
            repository.save_job(job_id, failed_job)
            return failed_job
        finally:
            repository.close()

    def _queue_retry(
        self,
        job_id: str,
        error_message: str,
        expected_execution_token: str | None = None,
        expected_status: JobStatus | None = None,
    ) -> OptimizationJob | None:
        repository = self._repository_factory()
        try:
            current_job = repository.load_job(job_id)
            if not self._matches_expected_state(
                current_job,
                expected_status=expected_status,
                expected_execution_token=expected_execution_token,
            ):
                return None
            backoff_seconds = current_job.retry_backoff_seconds * max(current_job.attempts, 1)
            retry_after = datetime.now(tz=UTC) + timedelta(seconds=backoff_seconds)
            queued_job = current_job.model_copy(
                update={
                    "status": JobStatus.queued,
                    "started_at": None,
                    "last_heartbeat_at": None,
                    "completed_at": None,
                    "retry_after": retry_after,
                    "execution_token": None,
                    "executor_id": None,
                    "run_id": None,
                    "result_summary": None,
                    "error_message": error_message,
                },
                deep=True,
            )
            repository.save_job(job_id, queued_job)
            metrics.increment("jobs_retry_scheduled_total", run_kind=queued_job.run_kind.value)
            return queued_job
        finally:
            repository.close()

    def _persist_job_input(self, job_id: str, payload: dict[str, Any]) -> None:
        repository = self._repository_factory()
        try:
            repository.save_job_input(job_id, payload)
        finally:
            repository.close()

    @contextmanager
    def _job_heartbeat(self, job_id: str, execution_token: str) -> Iterator[None]:
        stop_event = Event()
        thread = Thread(
            target=self._heartbeat_loop,
            args=(job_id, execution_token, stop_event),
            daemon=True,
            name=f"job-heartbeat-{job_id}",
        )
        thread.start()
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=self._heartbeat_interval_seconds + 1.0)

    def _heartbeat_loop(self, job_id: str, execution_token: str, stop_event: Event) -> None:
        while not stop_event.wait(self._heartbeat_interval_seconds):
            try:
                self._heartbeat_job(job_id, execution_token)
            except Exception:
                logger.warning(
                    "Failed to persist heartbeat for job %s",
                    job_id,
                    exc_info=True,
                    extra={"job_id": job_id, "executor_id": self._executor_id},
                )

    def _heartbeat_job(self, job_id: str, execution_token: str) -> None:
        repository = self._repository_factory()
        try:
            job = repository.load_job(job_id)
            if job.status != JobStatus.running or job.execution_token != execution_token:
                return
            repository.save_job(
                job_id,
                job.model_copy(
                    update={"last_heartbeat_at": datetime.now(tz=UTC)},
                    deep=True,
                ),
            )
            metrics.increment("job_heartbeats_total", run_kind=job.run_kind.value)
        finally:
            repository.close()

    @staticmethod
    def _matches_expected_state(
        job: OptimizationJob,
        expected_status: JobStatus | None = None,
        expected_execution_token: str | None = None,
    ) -> bool:
        return not (
            (expected_status is not None and job.status != expected_status)
            or (
                expected_execution_token is not None
                and job.execution_token != expected_execution_token
            )
        )

    @staticmethod
    def _ensure_job_can_start(job: OptimizationJob) -> None:
        if job.status != JobStatus.queued:
            msg = f"Job {job.job_id} is in status {job.status.value} and cannot be started"
            raise JobStateError(msg)
        if job.retry_after is not None and job.retry_after > datetime.now(tz=UTC):
            msg = f"Job {job.job_id} is not eligible to start until {job.retry_after.isoformat()}"
            raise JobStateError(msg)

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        import traceback as _tb

        tb_lines = _tb.format_exception(type(exc), exc, exc.__traceback__)
        full = "".join(tb_lines)
        if len(full) > 8192:
            full = full[-8192:]
        return full

    @staticmethod
    def _format_timeout(
        job: OptimizationJob,
        duration_seconds: float,
        stale: bool = False,
    ) -> str:
        if stale:
            return (
                f"JobTimeoutError: job {job.job_id} exceeded stale threshold after "
                f"{duration_seconds:.2f} seconds"
            )
        return (
            f"JobTimeoutError: job {job.job_id} exceeded timeout of "
            f"{job.timeout_seconds:.2f} seconds after {duration_seconds:.2f} seconds"
        )

    @staticmethod
    def _build_job_id(run_kind: RunKind) -> str:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S%f")
        short_uuid = uuid4().hex[:8]
        return f"job-{run_kind.value}-{timestamp}-{short_uuid}"
