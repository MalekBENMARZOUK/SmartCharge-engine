from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from smart_charging_optimization_engine.domain.results import (
    ObjectiveBreakdown,
    SiteSlotSummary,
    VehicleSlotAssignment,
    VehicleSummary,
)
from smart_charging_optimization_engine.optimization._solver_utils import (
    build_availability_lookup,
    require_finite,
)
from smart_charging_optimization_engine.optimization._solver_utils import (
    expr as _expr,
)
from smart_charging_optimization_engine.optimization._solver_utils import (
    sum_expr as _sum_expr,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ortools.linear_solver import pywraplp

    from smart_charging_optimization_engine.domain.models import (
        Charger,
        FleetPriorityRule,
        ObjectiveConfig,
        SiteProfile,
        Vehicle,
    )


@dataclass
class SiteVariables:
    charge_state: dict[tuple[str, str, int], pywraplp.Variable] = field(default_factory=dict)
    discharge_state: dict[tuple[str, str, int], pywraplp.Variable] = field(default_factory=dict)
    charge_power: dict[tuple[str, str, int], pywraplp.Variable] = field(default_factory=dict)
    discharge_power: dict[tuple[str, str, int], pywraplp.Variable] = field(default_factory=dict)
    site_import: dict[int, pywraplp.Variable] = field(default_factory=dict)
    site_export: dict[int, pywraplp.Variable] = field(default_factory=dict)
    net_power: dict[int, pywraplp.Variable] = field(default_factory=dict)
    grid_import_state: dict[int, pywraplp.Variable] = field(default_factory=dict)
    ramp: dict[int, pywraplp.Variable] = field(default_factory=dict)
    unmet: dict[str, pywraplp.Variable] = field(default_factory=dict)
    peak_power: pywraplp.Variable | None = None


def create_site_variables(
    solver: pywraplp.Solver,
    site: SiteProfile,
    vehicles: Sequence[Vehicle],
    chargers: Sequence[Charger],
    objective: ObjectiveConfig,
    prefix: str = "",
) -> SiteVariables:
    slots = range(site.horizon_slots)
    charger_by_id = {c.charger_id: c for c in chargers}
    charger_ids = list(charger_by_id)
    export_limits = site.export_limit_kw or [0.0] * site.horizon_slots

    sv = SiteVariables()

    if objective.site_demand_charge_per_kw > 0.0:
        sv.peak_power = solver.NumVar(0.0, solver.infinity(), f"{prefix}peak_site_power")

    for vehicle in vehicles:
        sv.unmet[vehicle.vehicle_id] = solver.NumVar(
            0.0,
            solver.infinity(),
            f"{prefix}unmet_{vehicle.vehicle_id}",
        )
        for charger_id in charger_ids:
            charger = charger_by_id[charger_id]
            charge_cap = min(vehicle.max_charging_power_kw, charger.max_power_kw)
            discharge_cap = min(vehicle.max_discharging_power_kw, charger.max_power_kw)
            for slot in slots:
                key = (vehicle.vehicle_id, charger_id, slot)
                sv.charge_state[key] = solver.BoolVar(
                    f"{prefix}charge_state_{vehicle.vehicle_id}_{charger_id}_{slot}"
                )
                sv.discharge_state[key] = solver.BoolVar(
                    f"{prefix}discharge_state_{vehicle.vehicle_id}_{charger_id}_{slot}"
                )
                sv.charge_power[key] = solver.NumVar(
                    0.0,
                    charge_cap,
                    f"{prefix}charge_power_{vehicle.vehicle_id}_{charger_id}_{slot}",
                )
                sv.discharge_power[key] = solver.NumVar(
                    0.0,
                    discharge_cap,
                    f"{prefix}discharge_power_{vehicle.vehicle_id}_{charger_id}_{slot}",
                )

    for slot in slots:
        sv.site_import[slot] = solver.NumVar(
            0.0,
            site.power_limit_kw[slot],
            f"{prefix}site_import_{slot}",
        )
        sv.site_export[slot] = solver.NumVar(
            0.0,
            export_limits[slot],
            f"{prefix}site_export_{slot}",
        )
        sv.net_power[slot] = solver.NumVar(
            -export_limits[slot],
            site.power_limit_kw[slot],
            f"{prefix}net_site_power_{slot}",
        )
        sv.grid_import_state[slot] = solver.BoolVar(f"{prefix}grid_import_state_{slot}")

    return sv


def add_site_constraints(
    solver: pywraplp.Solver,
    sv: SiteVariables,
    site: SiteProfile,
    vehicles: Sequence[Vehicle],
    chargers: Sequence[Charger],
    objective: ObjectiveConfig,
    required_charger_by_vehicle_slot: Mapping[tuple[str, int], str] | None = None,
    prefix: str = "",
) -> None:
    slots = range(site.horizon_slots)
    charger_by_id = {c.charger_id: c for c in chargers}
    charger_ids = list(charger_by_id)
    export_limits = site.export_limit_kw or [0.0] * site.horizon_slots
    allow_v2g = objective.allow_vehicle_to_grid
    time_step_hours = site.time_step_minutes / 60.0

    available_slots = {
        v.vehicle_id: build_availability_lookup(v, site.horizon_slots) for v in vehicles
    }

    for vehicle in vehicles:
        for charger_id in charger_ids:
            charger = charger_by_id[charger_id]
            charge_cap = min(vehicle.max_charging_power_kw, charger.max_power_kw)
            discharge_cap = min(vehicle.max_discharging_power_kw, charger.max_power_kw)
            for slot in slots:
                key = (vehicle.vehicle_id, charger_id, slot)
                solver.Add(_expr(sv.charge_state[key]) + _expr(sv.discharge_state[key]) <= 1)
                solver.Add(_expr(sv.charge_power[key]) <= charge_cap * _expr(sv.charge_state[key]))

                vehicle_v2g = (
                    allow_v2g and vehicle.v2g_enabled and vehicle.max_discharging_power_kw > 0.0
                )
                if vehicle_v2g:
                    solver.Add(
                        _expr(sv.discharge_power[key])
                        <= discharge_cap * _expr(sv.discharge_state[key])
                    )
                else:
                    solver.Add(sv.discharge_state[key] == 0)
                    solver.Add(sv.discharge_power[key] == 0)

                incompatible = (
                    vehicle.compatible_charger_ids is not None
                    and charger_id not in vehicle.compatible_charger_ids
                )
                locked_charger_id = None
                if required_charger_by_vehicle_slot is not None:
                    locked_charger_id = required_charger_by_vehicle_slot.get(
                        (vehicle.vehicle_id, slot)
                    )
                locked_to_other_charger = (
                    locked_charger_id is not None and charger_id != locked_charger_id
                )
                unavailable = (
                    slot not in available_slots[vehicle.vehicle_id]
                    or slot >= vehicle.departure_slot
                )
                if incompatible or locked_to_other_charger or unavailable:
                    solver.Add(sv.charge_state[key] == 0)
                    solver.Add(sv.discharge_state[key] == 0)
                    solver.Add(sv.charge_power[key] == 0)
                    solver.Add(sv.discharge_power[key] == 0)

    for slot in slots:
        total_charge = _sum_expr(
            sv.charge_power[(v.vehicle_id, cid, slot)] for v in vehicles for cid in charger_ids
        )
        total_discharge = _sum_expr(
            sv.discharge_power[(v.vehicle_id, cid, slot)] for v in vehicles for cid in charger_ids
        )
        solver.Add(_expr(sv.net_power[slot]) == _expr(total_charge) - _expr(total_discharge))
        solver.Add(
            _expr(sv.site_import[slot]) - _expr(sv.site_export[slot]) == _expr(sv.net_power[slot])
        )
        solver.Add(
            _expr(sv.site_import[slot])
            <= site.power_limit_kw[slot] * _expr(sv.grid_import_state[slot])
        )
        solver.Add(
            _expr(sv.site_export[slot])
            <= export_limits[slot] * (_expr(1.0) - _expr(sv.grid_import_state[slot]))
        )

        if sv.peak_power is not None:
            solver.Add(_expr(sv.peak_power) >= _expr(sv.site_import[slot]))

        for charger_id in charger_ids:
            solver.Add(
                _sum_expr(
                    _expr(sv.charge_state[(v.vehicle_id, charger_id, slot)])
                    + _expr(sv.discharge_state[(v.vehicle_id, charger_id, slot)])
                    for v in vehicles
                )
                <= 1
            )

        for vehicle in vehicles:
            solver.Add(
                _sum_expr(
                    _expr(sv.charge_state[(vehicle.vehicle_id, cid, slot)])
                    + _expr(sv.discharge_state[(vehicle.vehicle_id, cid, slot)])
                    for cid in charger_ids
                )
                <= 1
            )

        if slot > 0 and objective.load_smoothing_penalty_per_kw_change > 0.0:
            ramp = solver.NumVar(0.0, solver.infinity(), f"{prefix}ramp_{slot}")
            sv.ramp[slot] = ramp
            solver.Add(ramp >= _expr(sv.net_power[slot]) - _expr(sv.net_power[slot - 1]))
            solver.Add(ramp >= _expr(sv.net_power[slot - 1]) - _expr(sv.net_power[slot]))

    for vehicle in vehicles:
        for end_slot in range(1, site.horizon_slots + 1):
            cumulative = _sum_expr(
                _expr(sv.charge_power[(vehicle.vehicle_id, cid, s)])
                * charger_by_id[cid].efficiency
                * time_step_hours
                - _expr(sv.discharge_power[(vehicle.vehicle_id, cid, s)])
                / vehicle.discharge_efficiency
                * time_step_hours
                for cid in charger_ids
                for s in range(end_slot)
            )
            solver.Add(vehicle.initial_energy_kwh + cumulative >= vehicle.minimum_energy_kwh)
            solver.Add(vehicle.initial_energy_kwh + cumulative <= vehicle.battery_capacity_kwh)

        total_delta = _sum_expr(
            _expr(sv.charge_power[(vehicle.vehicle_id, cid, slot)])
            * charger_by_id[cid].efficiency
            * time_step_hours
            - _expr(sv.discharge_power[(vehicle.vehicle_id, cid, slot)])
            / vehicle.discharge_efficiency
            * time_step_hours
            for cid in charger_ids
            for slot in slots
        )
        solver.Add(
            vehicle.initial_energy_kwh + total_delta + sv.unmet[vehicle.vehicle_id]
            >= vehicle.target_energy_kwh
        )


def build_site_objective_terms(
    sv: SiteVariables,
    site: SiteProfile,
    vehicles: Sequence[Vehicle],
    chargers: Sequence[Charger],
    objective: ObjectiveConfig,
    priority_rules: FleetPriorityRule,
) -> dict[str, Any]:
    slots = range(site.horizon_slots)
    charger_ids = [c.charger_id for c in chargers]
    export_prices = site.export_price_per_kwh or [0.0] * site.horizon_slots
    time_step_hours = site.time_step_minutes / 60.0

    electricity_cost = _sum_expr(
        _expr(sv.site_import[slot]) * site.electricity_price_per_kwh[slot] * time_step_hours
        for slot in slots
    )
    export_revenue = _sum_expr(
        _expr(sv.site_export[slot]) * export_prices[slot] * time_step_hours for slot in slots
    )
    unmet_penalty = _sum_expr(
        _expr(sv.unmet[v.vehicle_id])
        * objective.unmet_demand_penalty_per_kwh
        * priority_rules.weight_for(v.priority)
        for v in vehicles
    )
    ramp_penalty = _sum_expr(
        _expr(rv) * objective.load_smoothing_penalty_per_kw_change for rv in sv.ramp.values()
    )
    demand_charge = (
        _expr(sv.peak_power) * objective.site_demand_charge_per_kw
        if sv.peak_power is not None
        else 0.0
    )
    degradation = _sum_expr(
        _expr(sv.discharge_power[(v.vehicle_id, cid, slot)])
        * time_step_hours
        * objective.battery_degradation_cost_per_kwh
        for v in vehicles
        for cid in charger_ids
        for slot in slots
    )
    return {
        "electricity_cost": electricity_cost,
        "export_revenue": export_revenue,
        "unmet_penalty": unmet_penalty,
        "ramp_penalty": ramp_penalty,
        "demand_charge": demand_charge,
        "degradation": degradation,
    }


def extract_assignments(
    sv: SiteVariables,
    site_id: str,
    vehicles: Sequence[Vehicle],
    chargers: Sequence[Charger],
    horizon_slots: int,
    time_step_hours: float,
) -> list[VehicleSlotAssignment]:
    charger_by_id = {c.charger_id: c for c in chargers}
    charger_ids = list(charger_by_id)
    assignments: list[VehicleSlotAssignment] = []
    for vehicle in vehicles:
        for charger_id in charger_ids:
            for slot in range(horizon_slots):
                cp = sv.charge_power[(vehicle.vehicle_id, charger_id, slot)].solution_value()
                dp = sv.discharge_power[(vehicle.vehicle_id, charger_id, slot)].solution_value()
                if cp > 1e-6 or dp > 1e-6:
                    delivered = cp * charger_by_id[charger_id].efficiency * time_step_hours
                    exported = dp * time_step_hours
                    delta = delivered - dp / vehicle.discharge_efficiency * time_step_hours
                    assignments.append(
                        VehicleSlotAssignment(
                            site_id=site_id,
                            vehicle_id=vehicle.vehicle_id,
                            charger_id=charger_id,
                            slot=slot,
                            power_kw=cp - dp,
                            charge_power_kw=cp,
                            discharge_power_kw=dp,
                            energy_delivered_kwh=delivered,
                            energy_exported_kwh=exported,
                            energy_delta_kwh=delta,
                            is_vehicle_to_grid=dp > 1e-6,
                        )
                    )
    return assignments


def extract_vehicle_summaries(
    sv: SiteVariables,
    site_id: str,
    vehicles: Sequence[Vehicle],
    chargers: Sequence[Charger],
    horizon_slots: int,
    time_step_hours: float,
) -> list[VehicleSummary]:
    charger_by_id = {c.charger_id: c for c in chargers}
    charger_ids = list(charger_by_id)
    summaries: list[VehicleSummary] = []
    for vehicle in vehicles:
        delivered = sum(
            sv.charge_power[(vehicle.vehicle_id, cid, slot)].solution_value()
            * charger_by_id[cid].efficiency
            * time_step_hours
            for cid in charger_ids
            for slot in range(horizon_slots)
        )
        exported = sum(
            sv.discharge_power[(vehicle.vehicle_id, cid, slot)].solution_value() * time_step_hours
            for cid in charger_ids
            for slot in range(horizon_slots)
        )
        battery_draw = sum(
            sv.discharge_power[(vehicle.vehicle_id, cid, slot)].solution_value()
            / vehicle.discharge_efficiency
            * time_step_hours
            for cid in charger_ids
            for slot in range(horizon_slots)
        )
        net_delta = delivered - battery_draw
        summaries.append(
            VehicleSummary(
                site_id=site_id,
                vehicle_id=vehicle.vehicle_id,
                initial_energy_kwh=vehicle.initial_energy_kwh,
                target_energy_kwh=vehicle.target_energy_kwh,
                final_energy_kwh=vehicle.initial_energy_kwh + net_delta,
                unmet_energy_kwh=sv.unmet[vehicle.vehicle_id].solution_value(),
                total_energy_delivered_kwh=delivered,
                total_energy_exported_kwh=exported,
                total_net_energy_delta_kwh=net_delta,
            )
        )
    return summaries


def extract_site_summary(
    sv: SiteVariables,
    site: SiteProfile,
) -> list[SiteSlotSummary]:
    export_prices = site.export_price_per_kwh or [0.0] * site.horizon_slots
    export_limits = site.export_limit_kw or [0.0] * site.horizon_slots
    return [
        SiteSlotSummary(
            site_id=site.site_id,
            slot=slot,
            total_power_kw=sv.net_power[slot].solution_value(),
            electricity_price_per_kwh=site.electricity_price_per_kwh[slot],
            site_power_limit_kw=site.power_limit_kw[slot],
            export_price_per_kwh=export_prices[slot],
            site_export_limit_kw=export_limits[slot],
        )
        for slot in range(site.horizon_slots)
    ]


def compute_objective_breakdown(
    site_summary: Sequence[SiteSlotSummary],
    vehicles: Sequence[Vehicle],
    assignments: Sequence[VehicleSlotAssignment],
    sv: SiteVariables,
    objective: ObjectiveConfig,
    priority_rules: FleetPriorityRule,
    time_step_hours: float,
    objective_value: float,
) -> ObjectiveBreakdown:
    electricity_cost = sum(
        max(s.total_power_kw, 0.0) * s.electricity_price_per_kwh * time_step_hours
        for s in site_summary
    )
    export_revenue = sum(
        max(-s.total_power_kw, 0.0) * (s.export_price_per_kwh or 0.0) * time_step_hours
        for s in site_summary
    )
    unmet_penalty = sum(
        sv.unmet[v.vehicle_id].solution_value()
        * objective.unmet_demand_penalty_per_kwh
        * priority_rules.weight_for(v.priority)
        for v in vehicles
    )
    ramp_penalty = sum(
        abs(site_summary[i].total_power_kw - site_summary[i - 1].total_power_kw)
        * objective.load_smoothing_penalty_per_kw_change
        for i in range(1, len(site_summary))
    )
    demand_charge = (
        max((max(s.total_power_kw, 0.0) for s in site_summary), default=0.0)
        * objective.site_demand_charge_per_kw
    )
    degradation = sum(
        a.energy_exported_kwh * objective.battery_degradation_cost_per_kwh for a in assignments
    )
    return ObjectiveBreakdown(
        electricity_cost=require_finite(electricity_cost, "electricity cost"),
        export_revenue=require_finite(export_revenue, "export revenue"),
        unmet_demand_penalty=require_finite(unmet_penalty, "unmet demand penalty"),
        load_smoothing_penalty=require_finite(ramp_penalty, "load smoothing penalty"),
        site_demand_charge_cost=require_finite(demand_charge, "site demand charge cost"),
        battery_degradation_cost=require_finite(degradation, "battery degradation cost"),
        total_cost=objective_value,
    )
