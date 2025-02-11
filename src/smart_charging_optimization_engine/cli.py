from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from smart_charging_optimization_engine.api.app import create_app
from smart_charging_optimization_engine.config import settings
from smart_charging_optimization_engine.domain.jobs import JobStatus
from smart_charging_optimization_engine.domain.models import (
    ChargingScenario,
    PortfolioScenario,
    RollingHorizonRequest,
)
from smart_charging_optimization_engine.domain.results import (
    MultiSiteOptimizationResult,
    OptimizationResult,
    RunComparison,
)
from smart_charging_optimization_engine.domain.runs import OptimizationRun, RunKind
from smart_charging_optimization_engine.io.json_io import (
    load_portfolio,
    load_scenario,
    load_telemetry_envelope,
    load_telemetry_snapshot,
    save_multisite_result,
    save_result,
)
from smart_charging_optimization_engine.logging_utils import configure_logging
from smart_charging_optimization_engine.messaging.amqp import AmqpTelemetryConsumer
from smart_charging_optimization_engine.metrics import metrics
from smart_charging_optimization_engine.optimization.engine import (
    SmartChargingOptimizer,
    SolverConfig,
)
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
from smart_charging_optimization_engine.visualization.plots import (
    write_power_profile_plot,
    write_vehicle_schedule_plot,
)

app = typer.Typer(help="Smart Charging Optimization Engine CLI")
console = Console()


@app.callback(invoke_without_command=True)
def _init() -> None:
    configure_logging(settings.log_level, settings.log_format)


def _close_repository(repository: object) -> None:
    close_method = getattr(repository, "close", None)
    if callable(close_method):
        close_method()


def _validate_output_path(output_path: Path) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"Cannot create output directory {output_path.parent}: {exc}"
        raise typer.BadParameter(msg) from exc
    if output_path.exists() and not os.access(output_path, os.W_OK):
        msg = f"Output path is not writable: {output_path}"
        raise typer.BadParameter(msg)
    if not output_path.exists() and not os.access(output_path.parent, os.W_OK):
        msg = f"Output directory is not writable: {output_path.parent}"
        raise typer.BadParameter(msg)


@app.command()
def solve(
    scenario_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output_path: Annotated[
        Path,
        typer.Option("--output", "-o"),
    ] = Path("artifacts/result.json"),
    plot_dir: Annotated[Path | None, typer.Option("--plot-dir")] = None,
    time_limit_seconds: Annotated[float, typer.Option(min=1.0)] = 30.0,
) -> None:
    _validate_output_path(output_path)
    scenario = load_scenario(scenario_path)
    optimizer = SmartChargingOptimizer(SolverConfig(time_limit_seconds=time_limit_seconds))
    result = optimizer.solve(scenario)
    save_result(result, output_path)

    if plot_dir is not None:
        write_power_profile_plot(result, plot_dir)
        write_vehicle_schedule_plot(result, plot_dir)

    run = _persist_single_site_run(RunKind.solve, scenario, result, optimizer.solver_config)

    _print_result_summary(run.result or result)
    console.print(f"Run ID: {run.run_id}")
    console.print(f"Result written to {output_path}")


@app.command()
def validate(
    scenario_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
) -> None:
    _ = load_scenario(scenario_path)
    console.print(f"Scenario at {scenario_path} is valid.", soft_wrap=True)


@app.command("export-schemas")
def export_schemas(output_dir: Annotated[Path, typer.Argument()]) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"Cannot create output directory {output_dir}: {exc}"
        raise typer.BadParameter(msg) from exc
    scenario_schema_path = output_dir / "charging_scenario.schema.json"
    result_schema_path = output_dir / "optimization_result.schema.json"
    scenario_schema_path.write_text(
        json.dumps(ChargingScenario.model_json_schema(), indent=2),
        encoding="utf-8",
    )
    result_schema_path.write_text(
        json.dumps(OptimizationResult.model_json_schema(), indent=2),
        encoding="utf-8",
    )
    console.print(f"Schemas written to {output_dir}")


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "0.0.0.0",  # noqa: S104
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
) -> None:
    uvicorn.run(create_app(), host=host, port=port)


@app.command("reoptimize")
def reoptimize(
    scenario_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    telemetry_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output_path: Annotated[Path, typer.Option("--output", "-o")] = Path(
        "artifacts/reoptimized_result.json"
    ),
    plot_dir: Annotated[Path | None, typer.Option("--plot-dir")] = None,
    time_limit_seconds: Annotated[float, typer.Option(min=1.0)] = 30.0,
    source_run_id: Annotated[str | None, typer.Option("--source-run-id")] = None,
) -> None:
    _validate_output_path(output_path)
    scenario = load_scenario(scenario_path)
    telemetry = load_telemetry_snapshot(telemetry_path)
    optimizer = RollingHorizonOptimizer(
        SmartChargingOptimizer(SolverConfig(time_limit_seconds=time_limit_seconds))
    )
    result = optimizer.reoptimize(
        RollingHorizonRequest(
            scenario=scenario,
            telemetry=telemetry,
            source_run_id=source_run_id,
        )
    )
    save_result(result, output_path)

    if plot_dir is not None:
        write_power_profile_plot(result, plot_dir)
        write_vehicle_schedule_plot(result, plot_dir)

    run = _persist_single_site_run(
        RunKind.reoptimize,
        scenario,
        result,
        optimizer.solver_config,
        telemetry_snapshot_id=telemetry.snapshot_id,
        source_run_id=source_run_id,
    )

    _print_result_summary(run.result or result)
    console.print(f"Run ID: {run.run_id}")
    console.print(f"Re-optimized result written to {output_path}")


@app.command("solve-portfolio")
def solve_portfolio(
    portfolio_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output_path: Annotated[Path, typer.Option("--output", "-o")] = Path(
        "artifacts/portfolio_result.json"
    ),
    time_limit_seconds: Annotated[float, typer.Option(min=1.0)] = 30.0,
) -> None:
    _validate_output_path(output_path)
    portfolio = load_portfolio(portfolio_path)
    optimizer = MultiSiteSmartChargingOptimizer(SolverConfig(time_limit_seconds=time_limit_seconds))
    result = optimizer.solve(portfolio)
    save_multisite_result(result, output_path)
    run = _persist_multisite_run(portfolio, result, optimizer.solver_config)
    _print_portfolio_summary(run.multisite_result or result)
    console.print(f"Run ID: {run.run_id}")
    console.print(f"Portfolio result written to {output_path}")


@app.command("list-runs")
def list_runs() -> None:
    repository = build_state_repository()
    try:
        run_service = OptimizationRunService(repository)
        runs = run_service.list_runs()
        if not runs:
            console.print("No runs stored.")
            return

        table = Table(title="Optimization Runs")
        table.add_column("Run ID")
        table.add_column("Kind")
        table.add_column("Scenario")
        table.add_column("Status")
        table.add_column("Total cost")
        for run in runs:
            table.add_row(
                run.run_id,
                run.run_kind.value,
                run.scenario_name,
                run.status.value,
                f"{run.summary.total_cost:.2f}",
            )
        console.print(table)
    finally:
        _close_repository(repository)


@app.command("compare-runs")
def compare_runs(
    baseline_run_id: Annotated[str, typer.Argument()],
    candidate_run_id: Annotated[str, typer.Argument()],
) -> None:
    repository = build_state_repository()
    try:
        run_service = OptimizationRunService(repository)
        comparison = run_service.compare_runs(baseline_run_id, candidate_run_id)
        _print_run_comparison(comparison)
    finally:
        _close_repository(repository)


@app.command("store-scenario")
def store_scenario(
    scenario_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    scenario_id: Annotated[str, typer.Option("--scenario-id")] = "default-scenario",
) -> None:
    scenario = load_scenario(scenario_path)
    repository = build_state_repository()
    try:
        destination = repository.save_scenario(scenario_id, scenario)
        console.print(f"Scenario stored at {destination}")
    finally:
        _close_repository(repository)


@app.command("ingest-telemetry")
def ingest_telemetry(
    telemetry_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    snapshot_id: Annotated[str | None, typer.Option("--snapshot-id")] = None,
) -> None:
    telemetry = load_telemetry_snapshot(telemetry_path)
    repository = build_state_repository()
    try:
        destination = repository.save_telemetry(snapshot_id or telemetry.snapshot_id, telemetry)
        console.print(f"Telemetry stored at {destination}")
    finally:
        _close_repository(repository)


@app.command("list-scenarios")
def list_scenarios() -> None:
    repository = build_state_repository()
    try:
        scenario_ids = repository.list_scenarios()
        if not scenario_ids:
            console.print("No scenarios stored.")
            return

        table = Table(title="Stored Scenarios")
        table.add_column("Scenario ID")
        for scenario_id in scenario_ids:
            table.add_row(scenario_id)
        console.print(table)
    finally:
        _close_repository(repository)


@app.command("retry-job")
def retry_job(job_id: Annotated[str, typer.Argument()]) -> None:
    job_service = OptimizationJobService(build_state_repository)
    job = job_service.retry_job(job_id)
    job_service.execute_job_from_payload(job.job_id)
    console.print(f"Retried job {job.job_id}")


@app.command("recover-stale-jobs")
def recover_stale_jobs() -> None:
    job_service = OptimizationJobService(build_state_repository)
    recovered_jobs = job_service.recover_stale_jobs()
    for job in recovered_jobs:
        if job.status == JobStatus.queued:
            job_service.execute_job_from_payload(job.job_id)
    console.print(f"Recovered {len(recovered_jobs)} stale job(s)")


@app.command("show-metrics")
def show_metrics(
    format: Annotated[str, typer.Option("--format")] = "json",
) -> None:
    if format == "json":
        console.print_json(data=metrics.snapshot())
        return
    if format == "prometheus":
        console.print(metrics.render_prometheus(), end="")
        return
    msg = "--format must be either 'json' or 'prometheus'"
    raise typer.BadParameter(msg)


@app.command("ingest-telemetry-envelope")
def ingest_telemetry_envelope(
    envelope_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
) -> None:
    envelope = load_telemetry_envelope(envelope_path)
    repository = build_state_repository()
    try:
        service = TelemetryIngestionService(repository)
        destination = service.ingest_envelope(envelope)
        console.print(f"Telemetry envelope ingested to {destination}")
    finally:
        _close_repository(repository)


@app.command("consume-telemetry-broker")
def consume_telemetry_broker(
    broker_url: Annotated[str, typer.Option("--broker-url")] = settings.telemetry_broker_url,
    queue_name: Annotated[str, typer.Option("--queue")] = settings.telemetry_broker_queue,
    max_messages: Annotated[int | None, typer.Option("--max-messages")] = None,
) -> None:
    repository = build_state_repository()
    try:
        service = TelemetryIngestionService(repository)
        consumer = AmqpTelemetryConsumer(service, broker_url, queue_name)
        processed = asyncio.run(consumer.consume(max_messages=max_messages))
        console.print(f"Processed {processed} telemetry message(s) from {queue_name}")
    finally:
        _close_repository(repository)


def _print_result_summary(result: OptimizationResult) -> None:
    overview = Table(title=f"Scenario: {result.scenario_name}")
    overview.add_column("Status")
    overview.add_column("Total cost")
    overview.add_column("Solve time (s)")
    overview.add_row(
        result.status.value,
        f"{result.objective_breakdown.total_cost:.2f}",
        f"{result.statistics.solve_time_seconds:.3f}",
    )
    console.print(overview)

    vehicle_table = Table(title="Vehicle Summary")
    vehicle_table.add_column("Vehicle")
    vehicle_table.add_column("Final energy (kWh)")
    vehicle_table.add_column("Target (kWh)")
    vehicle_table.add_column("Unmet (kWh)")
    for vehicle in result.vehicle_summaries:
        vehicle_table.add_row(
            vehicle.vehicle_id,
            f"{vehicle.final_energy_kwh:.2f}",
            f"{vehicle.target_energy_kwh:.2f}",
            f"{vehicle.unmet_energy_kwh:.2f}",
        )
    console.print(vehicle_table)


def _print_portfolio_summary(result: MultiSiteOptimizationResult) -> None:
    overview = Table(title=f"Portfolio: {result.scenario_name}")
    overview.add_column("Status")
    overview.add_column("Sites")
    overview.add_column("Total cost")
    overview.add_row(
        result.status.value,
        str(len(result.site_results)),
        f"{result.objective_breakdown.total_cost:.2f}",
    )
    console.print(overview)

    site_table = Table(title="Site Results")
    site_table.add_column("Site")
    site_table.add_column("Assignments")
    site_table.add_column("Vehicles")
    for site_result in result.site_results:
        site_name = site_result.site_summary[0].site_id if site_result.site_summary else "n/a"
        site_table.add_row(
            site_name,
            str(len(site_result.assignments)),
            str(len(site_result.vehicle_summaries)),
        )
    console.print(site_table)


def _persist_single_site_run(
    run_kind: RunKind,
    scenario: ChargingScenario,
    result: OptimizationResult,
    solver_config: SolverConfig,
    telemetry_snapshot_id: str | None = None,
    source_run_id: str | None = None,
) -> OptimizationRun:
    repository = build_state_repository()
    try:
        run_service = OptimizationRunService(repository)
        return run_service.record_single_site_run(
            run_kind,
            scenario,
            result,
            solver_config,
            telemetry_snapshot_id=telemetry_snapshot_id,
            source_run_id=source_run_id,
        )
    finally:
        _close_repository(repository)


def _persist_multisite_run(
    scenario: PortfolioScenario,
    result: MultiSiteOptimizationResult,
    solver_config: SolverConfig,
) -> OptimizationRun:
    repository = build_state_repository()
    try:
        run_service = OptimizationRunService(repository)
        return run_service.record_multisite_run(scenario, result, solver_config)
    finally:
        _close_repository(repository)


def _print_run_comparison(comparison: RunComparison) -> None:
    overview = Table(title="Run Comparison")
    overview.add_column("Metric")
    overview.add_column("Delta")
    overview.add_row("Total cost", f"{comparison.total_cost_delta:.2f}")
    overview.add_row("Electricity cost", f"{comparison.electricity_cost_delta:.2f}")
    overview.add_row("Unmet penalty", f"{comparison.unmet_demand_penalty_delta:.2f}")
    overview.add_row("At-risk vehicles", str(comparison.at_risk_vehicle_delta))
    console.print(overview)

    if comparison.vehicle_deltas:
        vehicle_table = Table(title="Vehicle Deltas")
        vehicle_table.add_column("Vehicle")
        vehicle_table.add_column("Final energy delta")
        vehicle_table.add_column("Unmet delta")
        for delta in comparison.vehicle_deltas:
            vehicle_table.add_row(
                delta.vehicle_id,
                f"{delta.final_energy_delta_kwh:.2f}",
                f"{delta.unmet_energy_delta_kwh:.2f}",
            )
        console.print(vehicle_table)

    console.print(comparison.summary)
