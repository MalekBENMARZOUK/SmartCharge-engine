from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from smart_charging_optimization_engine.config import settings
from smart_charging_optimization_engine.io.json_io import (
    load_portfolio,
    load_scenario,
    load_telemetry_envelope,
    load_telemetry_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smart_charging_optimization_engine.domain.models import (
        ChargingScenario,
        PortfolioScenario,
        TelemetryMessageEnvelope,
        TelemetrySnapshot,
    )

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_scenario_path() -> Path:
    return FIXTURES_DIR / "scenario_small.json"


@pytest.fixture
def fixture_scenario(fixture_scenario_path: Path) -> ChargingScenario:
    return load_scenario(fixture_scenario_path)


@pytest.fixture
def fixture_telemetry_path() -> Path:
    return FIXTURES_DIR / "telemetry" / "reopt_snapshot.json"


@pytest.fixture
def fixture_telemetry(fixture_telemetry_path: Path) -> TelemetrySnapshot:
    return load_telemetry_snapshot(fixture_telemetry_path)


@pytest.fixture
def fixture_portfolio_path() -> Path:
    return FIXTURES_DIR / "portfolio" / "two_site_portfolio.json"


@pytest.fixture
def fixture_portfolio(fixture_portfolio_path: Path) -> PortfolioScenario:
    return load_portfolio(fixture_portfolio_path)


@pytest.fixture
def fixture_envelope_path() -> Path:
    return FIXTURES_DIR / "telemetry" / "telemetry_envelope.json"


@pytest.fixture
def fixture_envelope(fixture_envelope_path: Path) -> TelemetryMessageEnvelope:
    return load_telemetry_envelope(fixture_envelope_path)


@pytest.fixture
def fixture_v2g_scenario_path() -> Path:
    return FIXTURES_DIR / "scenario_v2g.json"


@pytest.fixture
def fixture_v2g_scenario(fixture_v2g_scenario_path: Path) -> ChargingScenario:
    return load_scenario(fixture_v2g_scenario_path)


@pytest.fixture
def sql_settings(tmp_path: Path) -> Iterator[Path]:
    original_backend = settings.state_repository_backend
    original_database_url = settings.database_url
    db_path = tmp_path / "test-state.db"
    settings.state_repository_backend = "sql"
    settings.database_url = f"sqlite:///{db_path}"
    try:
        yield db_path
    finally:
        settings.state_repository_backend = original_backend
        settings.database_url = original_database_url
