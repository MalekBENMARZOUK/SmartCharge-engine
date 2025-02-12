.DEFAULT_GOAL := help
.PHONY: help install lint format test clean solve-example reoptimize-example solve-portfolio-example run-api benchmark benchmark-multisite migrate migrate-generate

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

install:
	python -m pip install -e ".[dev]"

lint:
	python -m ruff check .
	python -m mypy src
	python -m pyright

format:
	python -m ruff check --fix .
	python -m ruff format .

test:
	python -m pytest

clean:
	python scripts/clean.py

solve-example:
	sce solve examples/fleet_day_ahead.json --output artifacts/example_result.json --plot-dir artifacts/plots

reoptimize-example:
	sce reoptimize examples/fleet_day_ahead.json examples/telemetry/fleet_day_ahead_snapshot.json --output artifacts/reoptimized_result.json --plot-dir artifacts/reopt_plots

solve-portfolio-example:
	sce solve-portfolio examples/portfolio/two_depot_network.json --output artifacts/portfolio_result.json

run-api:
	python -m uvicorn smart_charging_optimization_engine.api.app:app --host 0.0.0.0 --port 8000 --reload

benchmark:
	python -m benchmarks.run_benchmark --output artifacts/benchmark_summary.json

benchmark-multisite:
	python -m benchmarks.run_multisite_benchmark --output artifacts/multisite_benchmark_summary.json

migrate:
	alembic upgrade head

migrate-generate:
	alembic revision --autogenerate -m "$(msg)"
