from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from smart_charging_optimization_engine.domain.runs import RunKind


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    timed_out = "timed_out"


class OptimizationJob(BaseModel):
    job_id: str
    run_kind: RunKind
    status: JobStatus
    scenario_name: str
    submitted_at: datetime
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1)
    timeout_seconds: float = Field(default=60.0, ge=1.0)
    retry_backoff_seconds: float = Field(default=0.0, ge=0.0)
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    completed_at: datetime | None = None
    retry_after: datetime | None = None
    execution_token: str | None = None
    executor_id: str | None = None
    run_id: str | None = None
    telemetry_snapshot_id: str | None = None
    source_run_id: str | None = None
    error_message: str | None = None
    result_summary: str | None = None


class OptimizationJobList(BaseModel):
    jobs: list[OptimizationJob] = Field(default_factory=list)
    total: int = 0
