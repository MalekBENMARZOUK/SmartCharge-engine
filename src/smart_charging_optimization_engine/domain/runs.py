from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from smart_charging_optimization_engine.domain.results import (
    MultiSiteOptimizationResult,
    OptimizationResult,
    SolverStatus,
)


class RunKind(StrEnum):
    solve = "solve"
    reoptimize = "reoptimize"
    multisite = "multisite"


class RunInputReferences(BaseModel):
    scenario_id: str | None = None
    telemetry_snapshot_id: str | None = None
    source_run_id: str | None = None


class RunSummary(BaseModel):
    total_cost: float
    solve_time_seconds: float = Field(ge=0.0)
    vehicle_count: int = Field(ge=0)
    unmet_vehicle_count: int = Field(ge=0)
    unmet_energy_kwh: float = Field(ge=0.0)
    at_risk_vehicle_count: int = Field(ge=0)
    peak_site_power_kw: float = Field(ge=0.0)
    peak_network_power_kw: float | None = Field(default=None, ge=0.0)


class OptimizationRunDigest(BaseModel):
    run_id: str
    run_kind: RunKind
    created_at: datetime
    scenario_name: str
    status: SolverStatus
    input_references: RunInputReferences
    solver_backend: str
    solver_time_limit_seconds: float = Field(ge=1.0)
    summary: RunSummary


class OptimizationRun(OptimizationRunDigest):
    result: OptimizationResult | None = None
    multisite_result: MultiSiteOptimizationResult | None = None
