from __future__ import annotations

import pytest
from pydantic import ValidationError

from smart_charging_optimization_engine.config import AppSettings


def test_settings_reject_invalid_repository_backend() -> None:
    with pytest.raises(ValidationError, match="Unsupported repository backend"):
        AppSettings(state_repository_backend="memory")


def test_settings_reject_invalid_log_level() -> None:
    with pytest.raises(ValidationError, match="Unsupported log level"):
        AppSettings(log_level="trace")


def test_settings_require_heartbeat_smaller_than_stale_threshold() -> None:
    with pytest.raises(ValidationError, match="job_heartbeat_interval_seconds"):
        AppSettings(
            job_heartbeat_interval_seconds=60.0,
            job_stale_threshold_seconds=60.0,
        )


def test_settings_accept_valid_file_backend_configuration() -> None:
    settings = AppSettings(
        state_repository_backend="file",
        state_store_dir="artifacts/state",
    )

    assert settings.state_repository_backend == "file"


def test_settings_reject_invalid_database_url() -> None:
    with pytest.raises(ValidationError, match="database_url must be a valid SQLAlchemy URL"):
        AppSettings(database_url="not-a-url")
