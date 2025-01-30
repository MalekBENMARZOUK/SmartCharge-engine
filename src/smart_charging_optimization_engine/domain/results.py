from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SolverStatus(StrEnum):
    optimal = "optimal"
    feasible = "feasible"
    infeasible = "infeasible"
    not_solved = "not_solved"


class BindingConstraint(StrEnum):
    availability_window = "availability_window"
    charger_compatibility = "charger_compatibility"
    charger_power_limit = "charger_power_limit"
    site_power_limit = "site_power_limit"
    network_power_limit = "network_power_limit"
    vehicle_to_grid_dispatch = "vehicle_to_grid_dispatch"
    no_active_constraint = "no_active_constraint"


class PowerDeltaScope(StrEnum):
    site = "site"
    network = "network"


class VehicleSlotAssignment(BaseModel):
    site_id: str | None = None
    vehicle_id: str
    charger_id: str
    slot: int
    power_kw: float
    charge_power_kw: float = Field(ge=0.0)
    discharge_power_kw: float = Field(ge=0.0)
    energy_delivered_kwh: float = Field(ge=0.0)
    energy_exported_kwh: float = Field(ge=0.0)
    energy_delta_kwh: float
    is_vehicle_to_grid: bool = False


class VehicleSummary(BaseModel):
    site_id: str | None = None
    vehicle_id: str
    initial_energy_kwh: float
    target_energy_kwh: float
    final_energy_kwh: float
    unmet_energy_kwh: float = Field(ge=0.0)
    total_energy_delivered_kwh: float = Field(ge=0.0)
    total_energy_exported_kwh: float = Field(ge=0.0)
    total_net_energy_delta_kwh: float = 0.0


class SiteSlotSummary(BaseModel):
    site_id: str | None = None
    slot: int
    total_power_kw: float
    electricity_price_per_kwh: float = Field(ge=0.0)
    site_power_limit_kw: float = Field(ge=0.0)
    export_price_per_kwh: float | None = Field(default=None, ge=0.0)
    site_export_limit_kw: float | None = Field(default=None, ge=0.0)


class NetworkSlotSummary(BaseModel):
    slot: int
    total_power_kw: float
    network_power_limit_kw: float | None = Field(default=None, ge=0.0)


class ObjectiveBreakdown(BaseModel):
    electricity_cost: float
    export_revenue: float = 0.0
    unmet_demand_penalty: float
    load_smoothing_penalty: float
    site_demand_charge_cost: float = 0.0
    network_demand_charge_cost: float = 0.0
    battery_degradation_cost: float = 0.0
    total_cost: float


class SolveStatistics(BaseModel):
    solve_time_seconds: float = Field(ge=0.0)
    objective_value: float
    best_bound: float | None = None
    optimality_gap_percent: float | None = Field(default=None, ge=0.0)


class VehicleInsight(BaseModel):
    site_id: str | None = None
    vehicle_id: str
    is_at_risk: bool = False
    available_slot_count: int = Field(ge=0)
    max_feasible_energy_kwh: float = Field(ge=0.0)
    target_gap_kwh: float
    reason_codes: list[BindingConstraint] = Field(default_factory=list)
    summary: str


class SiteConstraintInsight(BaseModel):
    site_id: str
    constrained_slots: list[int] = Field(default_factory=list)
    export_constrained_slots: list[int] = Field(default_factory=list)
    peak_power_kw: float = Field(ge=0.0)
    peak_utilization_ratio: float = Field(ge=0.0)


class NetworkConstraintInsight(BaseModel):
    constrained_slots: list[int] = Field(default_factory=list)
    peak_power_kw: float = Field(ge=0.0)
    peak_utilization_ratio: float = Field(ge=0.0)


class InfeasibilityDiagnostic(BaseModel):
    vehicle_id: str
    issue: str
    energy_gap_kwh: float = 0.0


class OptimizationInsights(BaseModel):
    at_risk_vehicle_ids: list[str] = Field(default_factory=list)
    vehicle_insights: list[VehicleInsight] = Field(default_factory=list)
    site_constraint_insights: list[SiteConstraintInsight] = Field(default_factory=list)
    network_constraint_insight: NetworkConstraintInsight | None = None
    infeasibility_diagnostics: list[InfeasibilityDiagnostic] = Field(default_factory=list)
    summary: str = ""


class VehicleDelta(BaseModel):
    site_id: str | None = None
    vehicle_id: str
    final_energy_delta_kwh: float
    unmet_energy_delta_kwh: float
    delivered_energy_delta_kwh: float
    exported_energy_delta_kwh: float


class PowerDelta(BaseModel):
    scope: PowerDeltaScope
    slot: int
    site_id: str | None = None
    total_power_delta_kw: float


class RunComparison(BaseModel):
    baseline_run_id: str
    candidate_run_id: str
    scenario_name: str
    baseline_status: SolverStatus
    candidate_status: SolverStatus
    status_changed: bool = False
    total_cost_delta: float
    electricity_cost_delta: float
    export_revenue_delta: float
    unmet_demand_penalty_delta: float
    load_smoothing_penalty_delta: float
    site_demand_charge_cost_delta: float
    network_demand_charge_cost_delta: float
    battery_degradation_cost_delta: float
    solve_time_delta_seconds: float
    at_risk_vehicle_delta: int
    vehicle_deltas: list[VehicleDelta] = Field(default_factory=list)
    power_deltas: list[PowerDelta] = Field(default_factory=list)
    summary: str


class OptimizationResult(BaseModel):
    status: SolverStatus
    scenario_name: str
    assignments: list[VehicleSlotAssignment]
    vehicle_summaries: list[VehicleSummary]
    site_summary: list[SiteSlotSummary]
    objective_breakdown: ObjectiveBreakdown
    statistics: SolveStatistics
    telemetry_snapshot_id: str | None = None
    run_id: str | None = None
    insights: OptimizationInsights | None = None
    infeasibility_diagnostics: list[InfeasibilityDiagnostic] = Field(default_factory=list)


class MultiSiteOptimizationResult(BaseModel):
    status: SolverStatus
    scenario_name: str
    site_results: list[OptimizationResult]
    network_summary: list[NetworkSlotSummary]
    objective_breakdown: ObjectiveBreakdown
    statistics: SolveStatistics
    run_id: str | None = None
    insights: OptimizationInsights | None = None
