from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import plotly.graph_objects as go

if TYPE_CHECKING:
    from smart_charging_optimization_engine.domain.results import OptimizationResult


def write_power_profile_plot(result: OptimizationResult, output_dir: str | Path) -> Path:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    slots = [item.slot for item in result.site_summary]
    total_power = [item.total_power_kw for item in result.site_summary]
    site_limit = [item.site_power_limit_kw for item in result.site_summary]

    figure = go.Figure()
    figure.add_trace(go.Scatter(x=slots, y=total_power, mode="lines", name="Scheduled power"))
    figure.add_trace(go.Scatter(x=slots, y=site_limit, mode="lines", name="Site limit"))
    figure.update_layout(
        title="Site Power Profile",
        xaxis_title="Time slot",
        yaxis_title="Power (kW)",
        template="plotly_white",
    )

    output_path = destination / "power_profile.html"
    figure.write_html(output_path, include_plotlyjs="cdn")
    return output_path


def write_vehicle_schedule_plot(result: OptimizationResult, output_dir: str | Path) -> Path:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    assignments = sorted(result.assignments, key=lambda item: (item.vehicle_id, item.slot))
    figure = go.Figure()
    for vehicle_id in {assignment.vehicle_id for assignment in assignments}:
        vehicle_assignments = [item for item in assignments if item.vehicle_id == vehicle_id]
        figure.add_trace(
            go.Bar(
                name=vehicle_id,
                x=[item.slot for item in vehicle_assignments],
                y=[item.power_kw for item in vehicle_assignments],
            )
        )

    figure.update_layout(
        title="Vehicle Charging Schedule",
        xaxis_title="Time slot",
        yaxis_title="Charging power (kW)",
        barmode="stack",
        template="plotly_white",
    )

    output_path = destination / "vehicle_schedule.html"
    figure.write_html(output_path, include_plotlyjs="cdn")
    return output_path
