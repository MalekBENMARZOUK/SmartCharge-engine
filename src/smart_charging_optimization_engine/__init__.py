from importlib.metadata import PackageNotFoundError, version

from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
from smart_charging_optimization_engine.optimization.multisite import (
    MultiSiteSmartChargingOptimizer,
)
from smart_charging_optimization_engine.services.rolling_horizon import (
    RollingHorizonOptimizer,
)
from smart_charging_optimization_engine.services.telemetry_ingestion import (
    TelemetryIngestionService,
)

try:
    __version__ = version("smart-charging-optimization-engine")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = [
    "MultiSiteSmartChargingOptimizer",
    "RollingHorizonOptimizer",
    "SmartChargingOptimizer",
    "TelemetryIngestionService",
    "__version__",
]
