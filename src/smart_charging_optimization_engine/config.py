from __future__ import annotations

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from smart_charging_optimization_engine.exceptions import ConfigurationError

_SUPPORTED_SOLVER_BACKENDS = {
    "CBC_MIXED_INTEGER_PROGRAMMING",
    "SCIP_MIXED_INTEGER_PROGRAMMING",
    "SAT_INTEGER_PROGRAMMING",
    "BOP_INTEGER_PROGRAMMING",
    "GLOP_LINEAR_PROGRAMMING",
    "CLP_LINEAR_PROGRAMMING",
    "GUROBI_MIXED_INTEGER_PROGRAMMING",
    "CPLEX_MIXED_INTEGER_PROGRAMMING",
    "XPRESS_MIXED_INTEGER_PROGRAMMING",
}

_SUPPORTED_REPOSITORY_BACKENDS = {"file", "sql"}

_SUPPORTED_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}

_SUPPORTED_LOG_FORMATS = {"plain", "json"}


class AppSettings(BaseSettings):
    api_title: str = Field(default="Smart Charging Optimization Engine")
    api_version: str = Field(default="0.1.0")
    default_solver_time_limit_seconds: float = Field(default=30.0, ge=1.0, le=3600.0)
    default_solver_backend: str = Field(default="CBC_MIXED_INTEGER_PROGRAMMING")
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    state_store_dir: str = Field(default="artifacts/state")
    state_repository_backend: str = Field(default="sql")
    database_url: str = Field(default="sqlite:///artifacts/state/smart_charging.db")
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_recycle_seconds: int = Field(default=3600, ge=60)
    telemetry_broker_url: str = Field(default="amqp://localhost/")
    telemetry_broker_queue: str = Field(default="smart-charging.telemetry", min_length=1)
    job_timeout_seconds: float = Field(default=60.0, ge=1.0, le=7200.0)
    job_max_attempts: int = Field(default=3, ge=1)
    job_retry_backoff_seconds: float = Field(default=0.1, ge=0.0)
    job_heartbeat_interval_seconds: float = Field(default=30.0, ge=1.0)
    job_stale_threshold_seconds: float = Field(default=300.0, ge=1.0)
    job_process_isolation: bool = Field(default=True)
    job_max_queue_depth: int = Field(default=100, ge=1, le=10_000)
    max_request_body_bytes: int = Field(default=10_485_760, ge=1024)
    cors_allowed_origins: list[str] = Field(default_factory=list)
    graceful_shutdown_timeout_seconds: float = Field(default=30.0, ge=1.0)

    model_config = SettingsConfigDict(
        env_prefix="SCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("default_solver_backend")
    @classmethod
    def validate_solver_backend(cls, value: str) -> str:
        if value not in _SUPPORTED_SOLVER_BACKENDS:
            msg = (
                f"Unsupported solver backend '{value}'. "
                f"Supported values: {sorted(_SUPPORTED_SOLVER_BACKENDS)}"
            )
            raise ConfigurationError(msg)
        return value

    @field_validator("state_repository_backend")
    @classmethod
    def validate_repository_backend(cls, value: str) -> str:
        if value not in _SUPPORTED_REPOSITORY_BACKENDS:
            msg = (
                f"Unsupported repository backend '{value}'. "
                f"Supported values: {sorted(_SUPPORTED_REPOSITORY_BACKENDS)}"
            )
            raise ConfigurationError(msg)
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value or "://" not in value:
            msg = "database_url must be a valid SQLAlchemy URL"
            raise ConfigurationError(msg)
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in _SUPPORTED_LOG_LEVELS:
            msg = (
                f"Unsupported log level '{value}'. "
                f"Supported values: {sorted(_SUPPORTED_LOG_LEVELS)}"
            )
            raise ConfigurationError(msg)
        return normalized

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in _SUPPORTED_LOG_FORMATS:
            msg = (
                f"Unsupported log format '{value}'. "
                f"Supported values: {sorted(_SUPPORTED_LOG_FORMATS)}"
            )
            raise ConfigurationError(msg)
        return normalized

    @model_validator(mode="after")
    def validate_storage_configuration(self) -> AppSettings:
        if self.state_repository_backend == "file" and not self.state_store_dir.strip():
            msg = "state_store_dir must be set when using the file repository backend"
            raise ConfigurationError(msg)
        if self.state_repository_backend == "sql" and not self.database_url.strip():
            msg = "database_url must be set when using the SQL repository backend"
            raise ConfigurationError(msg)
        if self.job_heartbeat_interval_seconds >= self.job_stale_threshold_seconds:
            msg = "job_heartbeat_interval_seconds must be smaller than job_stale_threshold_seconds"
            raise ConfigurationError(msg)
        return self


settings = AppSettings()
