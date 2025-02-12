# Smart Charging Optimization Engine

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/framework-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![OR-Tools](https://img.shields.io/badge/solver-OR--Tools-4285F4?logo=google&logoColor=white)](https://developers.google.com/optimization)
[![Pydantic v2](https://img.shields.io/badge/pydantic-v2-e92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-261230?logo=ruff&logoColor=white)](https://docs.astral.sh/ruff/)

A production-ready Python service for optimizing electric-vehicle fleet charging schedules. It minimises electricity costs while respecting grid limits, charger capacities, vehicle availability windows, and fleet priorities — and optionally earns revenue through Vehicle-to-Grid (V2G) export.

## Highlights

- **Day-ahead planning** — cost-minimised schedules with time-varying prices and power limits
- **Intraday re-optimisation** — rolling-horizon replanning driven by real-time telemetry
- **Vehicle-to-Grid (V2G)** — profitable energy dispatch during peak-price periods
- **Multi-site coordination** — network-aware optimisation across depots with shared grid constraints
- **Flexible deployment** — REST API (FastAPI), CLI, or programmatic library usage
- **Pluggable storage** — file-based JSON store or SQL (SQLite, PostgreSQL, …)
- **Background jobs** — async job queue with subprocess isolation, heartbeat, retry, and timeout
- **Observability** — structured JSON logging, Prometheus-compatible metrics, HTML dashboard

## Table of Contents

- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Usage](#usage)
  - [CLI](#cli)
  - [REST API](#rest-api)
  - [Programmatic](#programmatic)
- [Configuration](#configuration)
- [Domain Model](#domain-model)
- [Optimisation Model](#optimisation-model)
- [V2G Support](#v2g-support)
- [Multi-site / Portfolio Optimisation](#multi-site--portfolio-optimisation)
- [Rolling Horizon Re-optimisation](#rolling-horizon-re-optimisation)
- [Telemetry Ingestion](#telemetry-ingestion)
- [Storage Backends](#storage-backends)
- [Docker](#docker)
- [Development](#development)
- [Examples](#examples)
- [License](#license)

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  API / CLI surface                                           │
│  (FastAPI, Typer)                                            │
├──────────────────────────────────────────────────────────────┤
│  Services                                                    │
│  Job Queue · Rolling Horizon · Telemetry · Result Analysis   │
├──────────────────────────────────────────────────────────────┤
│  Optimisation Engine                                         │
│  MILP formulation (Google OR-Tools)                          │
├──────────────────────────────────────────────────────────────┤
│  Domain Models                                               │
│  Scenarios · Vehicles · Chargers · Results · Runs · Jobs     │
├──────────────────────────────────────────────────────────────┤
│  Cross-cutting                                               │
│  Storage · Messaging (AMQP) · Metrics · Logging · Plots      │
└──────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# install
pip install -e ".[dev]"

# solve the bundled day-ahead example
sce solve examples/fleet_day_ahead.json --output result.json --plot-dir plots/

# or start the API
sce serve
```

## Installation

**Requirements:** Python ≥ 3.11

```bash
# from source (editable, with dev tools)
pip install -e ".[dev]"

# production only
pip install .
```

### Docker

```bash
docker build -t sce .
docker compose up        # exposes 127.0.0.1:8000
```

The image runs as a non-root user with a read-only filesystem, OCI labels, and a built-in health check.

## Usage

### CLI

The CLI is exposed as the `sce` command.

```bash
# Single-site optimisation
sce solve <scenario.json> [--output FILE] [--plot-dir DIR] [--time-limit SECONDS]

# Re-optimise with live telemetry
sce reoptimize <scenario.json> <telemetry.json> [--output FILE] [--plot-dir DIR] [--source-run-id ID]

# Multi-site portfolio
sce solve-portfolio <portfolio.json> [--output FILE]

# Validate a scenario file
sce validate <scenario.json>

# Export JSON schemas
sce export-schemas <output-dir>

# Start the API server
sce serve [--host HOST] [--port PORT]
```

### REST API

Start the server with `sce serve` (or `make run-api` for auto-reload).

#### Health & Observability

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service status, DB connectivity, config summary |
| `GET` | `/health/ready` | Readiness probe (503 if DB unreachable) |
| `GET` | `/metrics` | JSON metrics snapshot |
| `GET` | `/metrics/prometheus` | Prometheus-format metrics |
| `GET` | `/dashboard` | HTML dashboard |

#### Core Optimisation

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/solve` | Single-site optimisation (sync) |
| `POST` | `/solve/multisite` | Multi-site portfolio optimisation (sync) |
| `POST` | `/reoptimize` | Rolling-horizon re-optimisation |

#### State Management

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/scenarios` | Store a scenario |
| `GET` | `/scenarios` | List stored scenarios (paginated) |
| `GET` | `/scenarios/{id}` | Retrieve a scenario |
| `POST` | `/portfolios` | Store a portfolio |
| `GET` | `/portfolios` | List stored portfolios (paginated) |
| `GET` | `/portfolios/{id}` | Retrieve a portfolio |

#### Telemetry

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/telemetry` | Ingest a telemetry snapshot |
| `GET` | `/telemetry/{id}` | Retrieve a snapshot |

#### Runs, Results & Jobs

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/runs` | List optimisation runs (paginated) |
| `GET` | `/runs/{id}` | Get run (metadata + result) |
| `DELETE` | `/runs/{id}` | Delete a run |
| `GET` | `/results/{id}` | Retrieve single-site result |
| `GET` | `/results/multisite/{id}` | Retrieve multi-site result |
| `DELETE` | `/results/{id}` | Delete a result |
| `POST` | `/jobs` | Create an async optimisation job |
| `GET` | `/jobs` | List jobs (paginated) |
| `GET` | `/jobs/{id}` | Job details + status |
| `DELETE` | `/jobs/{id}` | Cancel / delete a job |

All endpoints accept an optional `X-Request-ID` header for distributed tracing.

### Programmatic

```python
from smart_charging_optimization_engine.io.json_io import load_scenario
from smart_charging_optimization_engine.optimization.engine import OptimizationEngine

scenario = load_scenario("examples/fleet_day_ahead.json")
engine = OptimizationEngine()
result = engine.solve(scenario)

print(result.status)                    # SolverStatus.OPTIMAL
print(result.objective_breakdown)       # cost components
```

## Configuration

All settings are read from environment variables (prefix `SCE_`) or a `.env` file.

### Solver

| Variable | Default | Description |
|----------|---------|-------------|
| `SCE_DEFAULT_SOLVER_BACKEND` | `CBC_MIXED_INTEGER_PROGRAMMING` | OR-Tools solver (CBC, SCIP, SAT, BOP, GLOP, CLP, GUROBI, CPLEX, XPRESS) |
| `SCE_DEFAULT_SOLVER_TIME_LIMIT_SECONDS` | `30` | Solver wall-clock limit (1–3600) |

### Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `SCE_STATE_REPOSITORY_BACKEND` | `sql` | `file` or `sql` |
| `SCE_STATE_STORE_DIR` | `artifacts/state` | Directory for the file backend |
| `SCE_DATABASE_URL` | `sqlite:///artifacts/state/smart_charging.db` | SQLAlchemy connection URL |
| `SCE_DATABASE_POOL_SIZE` | `5` | Connection pool size |
| `SCE_DATABASE_MAX_OVERFLOW` | `10` | Extra connections above pool size |
| `SCE_DATABASE_POOL_RECYCLE_SECONDS` | `3600` | Connection recycle interval |

### Telemetry & Messaging

| Variable | Default | Description |
|----------|---------|-------------|
| `SCE_TELEMETRY_BROKER_URL` | `amqp://localhost/` | AMQP broker URL |
| `SCE_TELEMETRY_BROKER_QUEUE` | `smart-charging.telemetry` | Queue name |

### Job Queue

| Variable | Default | Description |
|----------|---------|-------------|
| `SCE_JOB_TIMEOUT_SECONDS` | `60` | Per-job timeout (1–7200) |
| `SCE_JOB_MAX_ATTEMPTS` | `3` | Retry count |
| `SCE_JOB_RETRY_BACKOFF_SECONDS` | `0.1` | Back-off between retries |
| `SCE_JOB_HEARTBEAT_INTERVAL_SECONDS` | `30` | Worker heartbeat interval |
| `SCE_JOB_STALE_THRESHOLD_SECONDS` | `300` | Stale-job recovery threshold |
| `SCE_JOB_PROCESS_ISOLATION` | `true` | Run each job in a subprocess |
| `SCE_JOB_MAX_QUEUE_DEPTH` | `100` | Max queued jobs (1–10 000) |

### API & Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `SCE_API_TITLE` | `Smart Charging Optimization Engine` | OpenAPI title |
| `SCE_API_VERSION` | `0.1.0` | OpenAPI version |
| `SCE_LOG_LEVEL` | `INFO` | CRITICAL / ERROR / WARNING / INFO / DEBUG |
| `SCE_LOG_FORMAT` | `json` | `plain` or `json` |
| `SCE_MAX_REQUEST_BODY_BYTES` | `10485760` | Request body limit (10 MB) |
| `SCE_CORS_ALLOWED_ORIGINS` | `[]` | Allowed CORS origins |
| `SCE_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS` | `30` | Graceful shutdown window |

## Domain Model

### Scenario (`ChargingScenario`)

A scenario groups the site profile, chargers, vehicles, objective weights, and priority rules for a single optimisation run.

**Site** — time-step duration, horizon length, per-slot grid power limit & electricity prices, and optional V2G export limits/prices.

**Charger** — ID, max power (kW), charging efficiency, and site association.

**Vehicle** — ID, battery capacity, initial/target/minimum energy, max charge power, availability windows, departure slot, fleet priority (`critical` / `high` / `normal` / `low`), compatible charger list, and optional V2G parameters (max discharge power, discharge efficiency).

**Objective** — unmet-demand penalty, load-smoothing penalty, demand charge rate, V2G toggle, and battery-degradation cost.

**Fleet Priority Rules** — multipliers per priority level (default: critical 5×, high 2×, normal 1×, low 0.7×) applied to unmet-demand penalties.

### Results (`OptimizationResult`)

- **Status** — optimal, feasible, infeasible, or not solved
- **Assignments** — per vehicle-charger-slot power values
- **Vehicle summaries** — energy accounting per vehicle
- **Site summary** — aggregate power vs. limits per slot
- **Objective breakdown** — electricity cost, export revenue, unmet penalty, ramp penalty, demand charge, degradation cost
- **Solve statistics** — wall-clock time, optimality gap
- **Insights** — at-risk vehicles with reason codes, binding-constraint analysis


## V2G Support

Enable Vehicle-to-Grid dispatch by:

1. Setting `v2g_enabled: true` on the vehicle with `max_discharging_power_kw` and `discharge_efficiency`
2. Providing `export_limit_kw` and `export_price_per_kwh` on the site
3. Setting `allow_vehicle_to_grid: true` in the objective config

The optimiser will schedule discharge during slots where export revenue exceeds import cost, subject to battery limits and degradation costs.

## Multi-site / Portfolio Optimisation

A `PortfolioScenario` wraps multiple single-site scenarios and adds a `NetworkConstraint`:

- **Network power limit** — shared grid interconnection cap across all sites per slot
- **Network demand charge** — cost applied to the aggregate peak

The engine solves a single MILP encompassing all sites plus network-level constraints and returns per-site results with network binding analysis.

## Rolling Horizon Re-optimisation

The `POST /reoptimize` endpoint (or `sce reoptimize` CLI) adapts a running schedule to real-time conditions:

1. Trims past time slots from the scenario
2. Updates vehicle energy levels from telemetry observations
3. Adjusts availability windows to the remaining horizon
4. Locks currently connected vehicles to their active charger
5. Applies optional power/price overrides
6. Re-solves the reduced scenario
7. Rebases slot indices to the original horizon

## Telemetry Ingestion

Real-time fleet state can be pushed via:

- **HTTP** — `POST /telemetry` with a `TelemetrySnapshot` payload
- **AMQP** — publish a `TelemetryMessageEnvelope` to the configured RabbitMQ queue

The `AmqpTelemetryConsumer` runs as a long-lived process, consuming messages, validating them, persisting snapshots, and updating metrics.

## Storage Backends

| Backend | Config value | Notes |
|---------|-------------|-------|
| **SQL** | `sql` (default) | SQLAlchemy-managed; SQLite out of the box, swap to PostgreSQL/MySQL via `SCE_DATABASE_URL`. Supports Alembic migrations (`make migrate`). |
| **File** | `file` | One JSON file per entity under `SCE_STATE_STORE_DIR`. No external dependencies. |

## Docker

```bash
docker build -t sce .
docker compose up
```

The Compose stack runs the API on `127.0.0.1:8000` with:

- SQL backend (SQLite at `/app/state/smart_charging.db`)
- Resource limits (2 CPUs / 2 GB memory)
- Read-only root filesystem, non-root user, `no-new-privileges`
- Built-in health check on `/health`
- 30 s graceful shutdown


## Examples

| File | Description |
|------|-------------|
| `examples/fleet_day_ahead.json` | 3 vehicles, 4 chargers, 24-slot horizon with time-varying prices |
| `examples/fleet_v2g.json` | V2G-enabled vehicle earning export revenue during peak pricing |
| `examples/portfolio/two_depot_network.json` | Two-depot portfolio with shared network constraint |
| `examples/telemetry/fleet_day_ahead_snapshot.json` | Telemetry snapshot for re-optimisation |
| `examples/telemetry/telemetry_envelope.json` | AMQP message envelope format |

## License

[MIT](LICENSE) — Malek Benmarzouk
