from __future__ import annotations


class ApplicationError(Exception):
    """Base class for operational errors exposed across service boundaries."""


class ConfigurationError(ApplicationError, ValueError):
    """Raised when application settings are invalid or unsupported."""


class InvalidIdentifierError(ApplicationError, ValueError):
    """Raised when a storage identifier is malformed or unsafe."""


class RepositoryError(ApplicationError):
    """Raised when persistence operations fail unexpectedly."""


class StorageNotFoundError(ApplicationError, KeyError):
    """Raised when a stored document cannot be found."""


class JsonPayloadError(ApplicationError, ValueError):
    """Raised when JSON payloads cannot be loaded or written safely."""


class TelemetryIngestionError(ApplicationError, ValueError):
    """Raised when telemetry payloads cannot be decoded or validated."""


class OptimizationError(ApplicationError, RuntimeError):
    """Raised when optimization setup or result extraction fails."""


class JobTimeoutError(ApplicationError, TimeoutError):
    """Raised when a background optimization job exceeds its configured timeout."""


class JobStateError(ApplicationError, RuntimeError):
    """Raised when a background job cannot transition to the requested state."""
