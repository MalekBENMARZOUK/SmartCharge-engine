from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, cast

from ortools.linear_solver import pywraplp

from smart_charging_optimization_engine.domain.results import SolverStatus
from smart_charging_optimization_engine.exceptions import OptimizationError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from smart_charging_optimization_engine.domain.models import Vehicle


def expr(value: object) -> Any:
    return cast("Any", value)


def sum_expr(terms: Iterable[object]) -> Any:
    total = expr(0.0)
    for term in terms:
        total = total + expr(term)
    return total


def build_availability_lookup(vehicle: Vehicle, horizon_slots: int) -> set[int]:
    available_slots: set[int] = set()
    for window in vehicle.availability_windows:
        for slot in range(window.start_slot, min(window.end_slot, horizon_slots)):
            available_slots.add(slot)
    return available_slots


def map_solver_status(status_code: int) -> SolverStatus:
    if status_code == pywraplp.Solver.OPTIMAL:
        return SolverStatus.optimal
    if status_code == pywraplp.Solver.FEASIBLE:
        return SolverStatus.feasible
    if status_code == pywraplp.Solver.INFEASIBLE:
        return SolverStatus.infeasible
    return SolverStatus.not_solved


def require_finite(value: float | None, description: str) -> float:
    if value is None:
        msg = f"Solver returned no value for {description}"
        raise OptimizationError(msg)
    if not math.isfinite(value):
        msg = f"Solver returned a non-finite value for {description}"
        raise OptimizationError(msg)
    return value


def require_optional_finite(value: float | None, description: str) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        msg = f"Solver returned a non-finite value for {description}"
        raise OptimizationError(msg)
    return value


def compute_optimality_gap(objective_value: float, best_bound: float | None) -> float | None:
    if best_bound is None:
        return None
    if abs(objective_value) < 1e-10:
        return 0.0 if best_bound is not None and abs(best_bound) < 1e-10 else None
    gap = abs(objective_value - best_bound) / max(abs(objective_value), 1e-10)
    return round(gap * 100, 4)
