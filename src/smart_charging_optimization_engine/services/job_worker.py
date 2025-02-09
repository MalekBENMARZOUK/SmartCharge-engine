from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Any

from typing_extensions import TypedDict

from smart_charging_optimization_engine.domain.models import (
    ChargingScenario,
    PortfolioScenario,
    RollingHorizonRequest,
)
from smart_charging_optimization_engine.domain.runs import RunKind
from smart_charging_optimization_engine.optimization.engine import SmartChargingOptimizer
from smart_charging_optimization_engine.optimization.multisite import (
    MultiSiteSmartChargingOptimizer,
)
from smart_charging_optimization_engine.services.rolling_horizon import RollingHorizonOptimizer
from smart_charging_optimization_engine.services.run_tracking import OptimizationRunService
from smart_charging_optimization_engine.storage.factory import (
    RepositoryDescriptor,
    build_state_repository_from_descriptor,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from multiprocessing.queues import Queue


class JobWorkerOutcome(TypedDict):
    status: str
    run_id: str | None
    error_type: str | None
    error_message: str | None


def execute_job_in_subprocess(
    repository_descriptor: RepositoryDescriptor,
    run_kind: str,
    payload: Mapping[str, Any],
    result_queue: Queue[JobWorkerOutcome],
) -> None:
    repository = build_state_repository_from_descriptor(repository_descriptor)
    try:
        run_kind_enum = RunKind(run_kind)
        run_service = OptimizationRunService(repository)
        if run_kind_enum == RunKind.solve:
            scenario = ChargingScenario.model_validate(payload)
            optimizer = SmartChargingOptimizer()
            result = optimizer.solve(scenario)
            run = run_service.record_single_site_run(
                RunKind.solve,
                scenario,
                result,
                optimizer.solver_config,
            )
        elif run_kind_enum == RunKind.reoptimize:
            request = RollingHorizonRequest.model_validate(payload)
            rolling_optimizer = RollingHorizonOptimizer()
            result = rolling_optimizer.reoptimize(request)
            run = run_service.record_single_site_run(
                RunKind.reoptimize,
                request.scenario,
                result,
                rolling_optimizer.solver_config,
                telemetry_snapshot_id=request.telemetry.snapshot_id,
                source_run_id=request.source_run_id,
            )
        else:
            multisite_scenario = PortfolioScenario.model_validate(payload)
            multisite_optimizer = MultiSiteSmartChargingOptimizer()
            multisite_result = multisite_optimizer.solve(multisite_scenario)
            run = run_service.record_multisite_run(
                multisite_scenario,
                multisite_result,
                multisite_optimizer.solver_config,
            )
        result_queue.put(
            {
                "status": "succeeded",
                "run_id": run.run_id,
                "error_type": None,
                "error_message": None,
            }
        )
    except Exception as exc:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        error_detail = "".join(tb)[-4096:]
        result_queue.put(
            {
                "status": "failed",
                "run_id": None,
                "error_type": type(exc).__name__,
                "error_message": error_detail,
            }
        )
    finally:
        repository.close()
