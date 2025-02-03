from __future__ import annotations

from typing import TYPE_CHECKING

from smart_charging_optimization_engine.domain.results import (
    BindingConstraint,
    InfeasibilityDiagnostic,
    MultiSiteOptimizationResult,
    NetworkConstraintInsight,
    NetworkSlotSummary,
    OptimizationInsights,
    OptimizationResult,
    PowerDelta,
    PowerDeltaScope,
    RunComparison,
    SiteConstraintInsight,
    SiteSlotSummary,
    VehicleDelta,
    VehicleInsight,
    VehicleSummary,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from smart_charging_optimization_engine.domain.models import (
        ChargingScenario,
        CoordinatedSiteScenario,
        PortfolioScenario,
        Vehicle,
    )

_EPSILON = 1e-6


def build_single_site_insights(
    scenario: ChargingScenario | CoordinatedSiteScenario,
    vehicle_summaries: Sequence[VehicleSummary],
    site_summary: Sequence[SiteSlotSummary],
    network_summary: Sequence[NetworkSlotSummary] | None = None,
) -> OptimizationInsights:
    site_insight = _build_site_constraint_insight(scenario.site.site_id, site_summary)
    network_insight = _build_network_constraint_insight(network_summary)
    site_binding_slots = set(site_insight.constrained_slots)
    network_binding_slots = (
        set(network_insight.constrained_slots) if network_insight is not None else set()
    )
    summary_by_vehicle = {summary.vehicle_id: summary for summary in vehicle_summaries}
    vehicle_insights = [
        _build_vehicle_insight(
            scenario=scenario,
            vehicle=vehicle,
            summary=summary_by_vehicle[vehicle.vehicle_id],
            site_binding_slots=site_binding_slots,
            network_binding_slots=network_binding_slots,
        )
        for vehicle in scenario.vehicles
    ]
    at_risk_vehicle_ids = [insight.vehicle_id for insight in vehicle_insights if insight.is_at_risk]
    summary = (
        f"{len(at_risk_vehicle_ids)} at-risk vehicle(s); "
        f"site cap binding on {len(site_insight.constrained_slots)} slot(s)"
    )
    if network_insight is not None:
        summary = (
            f"{summary}; network cap binding on {len(network_insight.constrained_slots)} slot(s)"
        )
    return OptimizationInsights(
        at_risk_vehicle_ids=at_risk_vehicle_ids,
        vehicle_insights=vehicle_insights,
        site_constraint_insights=[site_insight],
        network_constraint_insight=network_insight,
        summary=summary,
    )


def build_multisite_insights(
    scenario: PortfolioScenario,
    result: MultiSiteOptimizationResult,
) -> OptimizationInsights:
    site_constraint_insights: list[SiteConstraintInsight] = []
    vehicle_insights: list[VehicleInsight] = []
    at_risk_vehicle_ids: list[str] = []
    network_insight = _build_network_constraint_insight(result.network_summary)
    for site_scenario, site_result in zip(scenario.sites, result.site_results, strict=True):
        site_insights = site_result.insights or build_single_site_insights(
            site_scenario,
            site_result.vehicle_summaries,
            site_result.site_summary,
            network_summary=result.network_summary,
        )
        site_constraint_insights.extend(site_insights.site_constraint_insights)
        vehicle_insights.extend(site_insights.vehicle_insights)
        at_risk_vehicle_ids.extend(site_insights.at_risk_vehicle_ids)
    network_binding_count = len(network_insight.constrained_slots) if network_insight else 0
    summary = (
        f"{len(at_risk_vehicle_ids)} at-risk vehicle(s) across {len(result.site_results)} site(s); "
        f"network cap binding on {network_binding_count} "
        "slot(s)"
    )
    return OptimizationInsights(
        at_risk_vehicle_ids=sorted(set(at_risk_vehicle_ids)),
        vehicle_insights=vehicle_insights,
        site_constraint_insights=site_constraint_insights,
        network_constraint_insight=network_insight,
        summary=summary,
    )


def compare_single_site_results(
    baseline_run_id: str,
    candidate_run_id: str,
    baseline: OptimizationResult,
    candidate: OptimizationResult,
) -> RunComparison:
    vehicle_deltas = _build_vehicle_deltas(
        baseline.vehicle_summaries,
        candidate.vehicle_summaries,
    )
    power_deltas = _build_power_deltas(
        baseline.site_summary,
        candidate.site_summary,
        scope=PowerDeltaScope.site,
    )
    baseline_at_risk = len(baseline.insights.at_risk_vehicle_ids) if baseline.insights else 0
    candidate_at_risk = len(candidate.insights.at_risk_vehicle_ids) if candidate.insights else 0
    total_cost_delta = (
        candidate.objective_breakdown.total_cost - baseline.objective_breakdown.total_cost
    )
    summary = f"total cost delta {total_cost_delta:.2f}; {len(vehicle_deltas)} vehicle(s) changed"
    return RunComparison(
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        scenario_name=candidate.scenario_name,
        baseline_status=baseline.status,
        candidate_status=candidate.status,
        status_changed=baseline.status != candidate.status,
        total_cost_delta=candidate.objective_breakdown.total_cost
        - baseline.objective_breakdown.total_cost,
        electricity_cost_delta=candidate.objective_breakdown.electricity_cost
        - baseline.objective_breakdown.electricity_cost,
        export_revenue_delta=candidate.objective_breakdown.export_revenue
        - baseline.objective_breakdown.export_revenue,
        unmet_demand_penalty_delta=candidate.objective_breakdown.unmet_demand_penalty
        - baseline.objective_breakdown.unmet_demand_penalty,
        load_smoothing_penalty_delta=candidate.objective_breakdown.load_smoothing_penalty
        - baseline.objective_breakdown.load_smoothing_penalty,
        site_demand_charge_cost_delta=candidate.objective_breakdown.site_demand_charge_cost
        - baseline.objective_breakdown.site_demand_charge_cost,
        network_demand_charge_cost_delta=0.0,
        battery_degradation_cost_delta=candidate.objective_breakdown.battery_degradation_cost
        - baseline.objective_breakdown.battery_degradation_cost,
        solve_time_delta_seconds=candidate.statistics.solve_time_seconds
        - baseline.statistics.solve_time_seconds,
        at_risk_vehicle_delta=candidate_at_risk - baseline_at_risk,
        vehicle_deltas=vehicle_deltas,
        power_deltas=power_deltas,
        summary=summary,
    )


def compare_multisite_results(
    baseline_run_id: str,
    candidate_run_id: str,
    baseline: MultiSiteOptimizationResult,
    candidate: MultiSiteOptimizationResult,
) -> RunComparison:
    baseline_vehicle_summaries = [
        vehicle for site in baseline.site_results for vehicle in site.vehicle_summaries
    ]
    candidate_vehicle_summaries = [
        vehicle for site in candidate.site_results for vehicle in site.vehicle_summaries
    ]
    vehicle_deltas = _build_vehicle_deltas(
        baseline_vehicle_summaries,
        candidate_vehicle_summaries,
    )
    power_deltas = _build_power_deltas(
        [slot for site in baseline.site_results for slot in site.site_summary],
        [slot for site in candidate.site_results for slot in site.site_summary],
        scope=PowerDeltaScope.site,
    )
    power_deltas.extend(
        _build_network_power_deltas(baseline.network_summary, candidate.network_summary)
    )
    baseline_at_risk = len(baseline.insights.at_risk_vehicle_ids) if baseline.insights else 0
    candidate_at_risk = len(candidate.insights.at_risk_vehicle_ids) if candidate.insights else 0
    total_cost_delta = (
        candidate.objective_breakdown.total_cost - baseline.objective_breakdown.total_cost
    )
    summary = (
        "total cost delta "
        f"{total_cost_delta:.2f}; "
        "network peak delta "
        f"{_peak_network_power(candidate) - _peak_network_power(baseline):.2f} kW"
    )
    return RunComparison(
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        scenario_name=candidate.scenario_name,
        baseline_status=baseline.status,
        candidate_status=candidate.status,
        status_changed=baseline.status != candidate.status,
        total_cost_delta=candidate.objective_breakdown.total_cost
        - baseline.objective_breakdown.total_cost,
        electricity_cost_delta=candidate.objective_breakdown.electricity_cost
        - baseline.objective_breakdown.electricity_cost,
        export_revenue_delta=candidate.objective_breakdown.export_revenue
        - baseline.objective_breakdown.export_revenue,
        unmet_demand_penalty_delta=candidate.objective_breakdown.unmet_demand_penalty
        - baseline.objective_breakdown.unmet_demand_penalty,
        load_smoothing_penalty_delta=candidate.objective_breakdown.load_smoothing_penalty
        - baseline.objective_breakdown.load_smoothing_penalty,
        site_demand_charge_cost_delta=candidate.objective_breakdown.site_demand_charge_cost
        - baseline.objective_breakdown.site_demand_charge_cost,
        network_demand_charge_cost_delta=candidate.objective_breakdown.network_demand_charge_cost
        - baseline.objective_breakdown.network_demand_charge_cost,
        battery_degradation_cost_delta=candidate.objective_breakdown.battery_degradation_cost
        - baseline.objective_breakdown.battery_degradation_cost,
        solve_time_delta_seconds=candidate.statistics.solve_time_seconds
        - baseline.statistics.solve_time_seconds,
        at_risk_vehicle_delta=candidate_at_risk - baseline_at_risk,
        vehicle_deltas=vehicle_deltas,
        power_deltas=power_deltas,
        summary=summary,
    )


def _build_vehicle_insight(
    scenario: ChargingScenario | CoordinatedSiteScenario,
    vehicle: Vehicle,
    summary: VehicleSummary,
    site_binding_slots: set[int],
    network_binding_slots: set[int],
) -> VehicleInsight:
    compatible_chargers = [
        charger
        for charger in scenario.chargers
        if vehicle.compatible_charger_ids is None
        or charger.charger_id in vehicle.compatible_charger_ids
    ]
    available_slots = sorted(_iter_available_slots(vehicle, scenario.site.horizon_slots))
    max_charge_power = max(
        (
            min(vehicle.max_charging_power_kw, charger.max_power_kw)
            for charger in compatible_chargers
        ),
        default=0.0,
    )
    best_efficiency = max((charger.efficiency for charger in compatible_chargers), default=1.0)
    time_step_hours = scenario.site.time_step_minutes / 60.0
    max_feasible_energy_kwh = (
        len(available_slots) * max_charge_power * best_efficiency * time_step_hours
    )
    reasons: list[BindingConstraint] = []
    if not available_slots:
        reasons.append(BindingConstraint.availability_window)
    if not compatible_chargers:
        reasons.append(BindingConstraint.charger_compatibility)
    if summary.unmet_energy_kwh > _EPSILON:
        if available_slots:
            reachable_energy_kwh = vehicle.initial_energy_kwh + max_feasible_energy_kwh
            if reachable_energy_kwh + _EPSILON < vehicle.target_energy_kwh:
                reasons.extend(
                    [
                        BindingConstraint.availability_window,
                        BindingConstraint.charger_power_limit,
                    ]
                )
        if set(available_slots) & site_binding_slots:
            reasons.append(BindingConstraint.site_power_limit)
        if set(available_slots) & network_binding_slots:
            reasons.append(BindingConstraint.network_power_limit)
        if summary.total_energy_exported_kwh > _EPSILON:
            reasons.append(BindingConstraint.vehicle_to_grid_dispatch)
    if not reasons:
        reasons = [BindingConstraint.no_active_constraint]
    reason_codes = list(dict.fromkeys(reasons))
    target_gap_kwh = summary.target_energy_kwh - summary.final_energy_kwh
    summary_text = (
        f"target gap {target_gap_kwh:.2f} kWh with {len(reason_codes)} active reason(s)"
        if summary.unmet_energy_kwh > _EPSILON
        else "target satisfied"
    )
    return VehicleInsight(
        site_id=summary.site_id,
        vehicle_id=vehicle.vehicle_id,
        is_at_risk=summary.unmet_energy_kwh > _EPSILON,
        available_slot_count=len(available_slots),
        max_feasible_energy_kwh=max_feasible_energy_kwh,
        target_gap_kwh=target_gap_kwh,
        reason_codes=reason_codes,
        summary=summary_text,
    )


def _build_site_constraint_insight(
    site_id: str,
    site_summary: Sequence[SiteSlotSummary],
) -> SiteConstraintInsight:
    constrained_slots = [
        slot.slot
        for slot in site_summary
        if slot.total_power_kw > _EPSILON
        and _is_binding(slot.total_power_kw, slot.site_power_limit_kw)
    ]
    export_constrained_slots = [
        slot.slot
        for slot in site_summary
        if slot.total_power_kw < -_EPSILON
        and slot.site_export_limit_kw is not None
        and _is_binding(-slot.total_power_kw, slot.site_export_limit_kw)
    ]
    peak_power_kw = max((max(slot.total_power_kw, 0.0) for slot in site_summary), default=0.0)
    peak_utilization_ratio = max(
        (
            max(slot.total_power_kw, 0.0) / slot.site_power_limit_kw
            for slot in site_summary
            if slot.site_power_limit_kw > _EPSILON
        ),
        default=0.0,
    )
    return SiteConstraintInsight(
        site_id=site_id,
        constrained_slots=constrained_slots,
        export_constrained_slots=export_constrained_slots,
        peak_power_kw=peak_power_kw,
        peak_utilization_ratio=peak_utilization_ratio,
    )


def _build_network_constraint_insight(
    network_summary: Sequence[NetworkSlotSummary] | None,
) -> NetworkConstraintInsight | None:
    if not network_summary:
        return None
    constrained_slots = [
        slot.slot
        for slot in network_summary
        if slot.network_power_limit_kw is not None
        and slot.total_power_kw > _EPSILON
        and _is_binding(slot.total_power_kw, slot.network_power_limit_kw)
    ]
    peak_power_kw = max((max(slot.total_power_kw, 0.0) for slot in network_summary), default=0.0)
    peak_utilization_ratio = max(
        (
            max(slot.total_power_kw, 0.0) / slot.network_power_limit_kw
            for slot in network_summary
            if slot.network_power_limit_kw is not None and slot.network_power_limit_kw > _EPSILON
        ),
        default=0.0,
    )
    return NetworkConstraintInsight(
        constrained_slots=constrained_slots,
        peak_power_kw=peak_power_kw,
        peak_utilization_ratio=peak_utilization_ratio,
    )


def _iter_available_slots(vehicle: Vehicle, horizon_slots: int) -> Iterable[int]:
    for window in vehicle.availability_windows:
        yield from range(
            window.start_slot,
            min(window.end_slot, vehicle.departure_slot, horizon_slots),
        )


def _build_vehicle_deltas(
    baseline: Sequence[VehicleSummary],
    candidate: Sequence[VehicleSummary],
) -> list[VehicleDelta]:
    baseline_by_key = {(summary.site_id, summary.vehicle_id): summary for summary in baseline}
    candidate_by_key = {(summary.site_id, summary.vehicle_id): summary for summary in candidate}
    deltas: list[VehicleDelta] = []
    for key in sorted(
        set(baseline_by_key) | set(candidate_by_key),
        key=lambda item: (item[0] or "", item[1]),
    ):
        baseline_summary = baseline_by_key.get(key)
        candidate_summary = candidate_by_key.get(key)
        if baseline_summary is None or candidate_summary is None:
            continue
        delta = VehicleDelta(
            site_id=key[0],
            vehicle_id=key[1],
            final_energy_delta_kwh=(
                candidate_summary.final_energy_kwh - baseline_summary.final_energy_kwh
            ),
            unmet_energy_delta_kwh=(
                candidate_summary.unmet_energy_kwh - baseline_summary.unmet_energy_kwh
            ),
            delivered_energy_delta_kwh=(
                candidate_summary.total_energy_delivered_kwh
                - baseline_summary.total_energy_delivered_kwh
            ),
            exported_energy_delta_kwh=(
                candidate_summary.total_energy_exported_kwh
                - baseline_summary.total_energy_exported_kwh
            ),
        )
        if any(
            abs(value) > _EPSILON
            for value in (
                delta.final_energy_delta_kwh,
                delta.unmet_energy_delta_kwh,
                delta.delivered_energy_delta_kwh,
                delta.exported_energy_delta_kwh,
            )
        ):
            deltas.append(delta)
    return deltas


def _build_power_deltas(
    baseline: Sequence[SiteSlotSummary],
    candidate: Sequence[SiteSlotSummary],
    scope: PowerDeltaScope,
) -> list[PowerDelta]:
    baseline_by_key = {(slot.site_id, slot.slot): slot for slot in baseline}
    candidate_by_key = {(slot.site_id, slot.slot): slot for slot in candidate}
    deltas: list[PowerDelta] = []
    for key in sorted(
        set(baseline_by_key) | set(candidate_by_key),
        key=lambda item: (item[0] or "", item[1]),
    ):
        baseline_slot = baseline_by_key.get(key)
        candidate_slot = candidate_by_key.get(key)
        if baseline_slot is None or candidate_slot is None:
            continue
        total_power_delta_kw = candidate_slot.total_power_kw - baseline_slot.total_power_kw
        if abs(total_power_delta_kw) > _EPSILON:
            deltas.append(
                PowerDelta(
                    scope=scope,
                    site_id=key[0],
                    slot=key[1],
                    total_power_delta_kw=total_power_delta_kw,
                )
            )
    return deltas


def _build_network_power_deltas(
    baseline: Sequence[NetworkSlotSummary],
    candidate: Sequence[NetworkSlotSummary],
) -> list[PowerDelta]:
    baseline_by_slot = {slot.slot: slot for slot in baseline}
    candidate_by_slot = {slot.slot: slot for slot in candidate}
    deltas: list[PowerDelta] = []
    for slot in sorted(set(baseline_by_slot) | set(candidate_by_slot)):
        baseline_summary = baseline_by_slot.get(slot)
        candidate_summary = candidate_by_slot.get(slot)
        if baseline_summary is None or candidate_summary is None:
            continue
        total_power_delta_kw = candidate_summary.total_power_kw - baseline_summary.total_power_kw
        if abs(total_power_delta_kw) > _EPSILON:
            deltas.append(
                PowerDelta(
                    scope=PowerDeltaScope.network,
                    slot=slot,
                    total_power_delta_kw=total_power_delta_kw,
                )
            )
    return deltas


def _peak_network_power(result: MultiSiteOptimizationResult) -> float:
    return max((max(slot.total_power_kw, 0.0) for slot in result.network_summary), default=0.0)


def _is_binding(value: float, limit: float | None) -> bool:
    if limit is None or limit <= _EPSILON:
        return False
    tolerance = max(limit * 0.02, 0.1)
    return value >= limit - tolerance


def build_infeasibility_diagnostics(
    scenario: ChargingScenario | CoordinatedSiteScenario,
) -> list[InfeasibilityDiagnostic]:
    diagnostics: list[InfeasibilityDiagnostic] = []
    time_step_hours = scenario.site.time_step_minutes / 60.0

    for vehicle in scenario.vehicles:
        compatible_chargers = [
            charger
            for charger in scenario.chargers
            if vehicle.compatible_charger_ids is None
            or charger.charger_id in vehicle.compatible_charger_ids
        ]

        if not compatible_chargers:
            diagnostics.append(
                InfeasibilityDiagnostic(
                    vehicle_id=vehicle.vehicle_id,
                    issue="No compatible chargers available for this vehicle",
                    energy_gap_kwh=max(vehicle.target_energy_kwh - vehicle.initial_energy_kwh, 0.0),
                )
            )
            continue

        available_slot_count = sum(
            1
            for window in vehicle.availability_windows
            for slot in range(
                window.start_slot,
                min(window.end_slot, vehicle.departure_slot, scenario.site.horizon_slots),
            )
        )

        if available_slot_count == 0:
            diagnostics.append(
                InfeasibilityDiagnostic(
                    vehicle_id=vehicle.vehicle_id,
                    issue=(
                        "Vehicle has no available time slots"
                        " (check availability windows vs departure slot)"
                    ),
                    energy_gap_kwh=max(vehicle.target_energy_kwh - vehicle.initial_energy_kwh, 0.0),
                )
            )
            continue

        max_charge_power = max(
            min(vehicle.max_charging_power_kw, charger.max_power_kw)
            for charger in compatible_chargers
        )
        best_efficiency = max(charger.efficiency for charger in compatible_chargers)
        max_energy = available_slot_count * max_charge_power * best_efficiency * time_step_hours
        energy_needed = vehicle.target_energy_kwh - vehicle.initial_energy_kwh

        if vehicle.initial_energy_kwh < vehicle.minimum_energy_kwh:
            diagnostics.append(
                InfeasibilityDiagnostic(
                    vehicle_id=vehicle.vehicle_id,
                    issue=(
                        f"Initial energy ({vehicle.initial_energy_kwh:.1f} kWh) is below "
                        f"minimum energy ({vehicle.minimum_energy_kwh:.1f} kWh)"
                    ),
                    energy_gap_kwh=vehicle.minimum_energy_kwh - vehicle.initial_energy_kwh,
                )
            )

        if energy_needed > _EPSILON and max_energy + _EPSILON < energy_needed:
            site_power_limited = any(
                scenario.site.power_limit_kw[slot] < max_charge_power
                for window in vehicle.availability_windows
                for slot in range(
                    window.start_slot,
                    min(window.end_slot, vehicle.departure_slot, scenario.site.horizon_slots),
                )
            )
            hint = (
                "Consider increasing site power limits, extending availability windows, "
                "or reducing the target energy."
            )
            if site_power_limited:
                hint = "Site power limit constrains charging during some available slots. " + hint
            diagnostics.append(
                InfeasibilityDiagnostic(
                    vehicle_id=vehicle.vehicle_id,
                    issue=(
                        f"Insufficient charging capacity: max reachable energy "
                        f"{vehicle.initial_energy_kwh + max_energy:.1f} kWh < target "
                        f"{vehicle.target_energy_kwh:.1f} kWh. {hint}"
                    ),
                    energy_gap_kwh=energy_needed - max_energy,
                )
            )

    return diagnostics
