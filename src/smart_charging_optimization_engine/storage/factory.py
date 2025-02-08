from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import TypedDict

from smart_charging_optimization_engine.config import settings
from smart_charging_optimization_engine.exceptions import ConfigurationError
from smart_charging_optimization_engine.storage.repository import FileStateRepository
from smart_charging_optimization_engine.storage.sql_repository import (
    SqlAlchemyStateRepository,
)

if TYPE_CHECKING:
    from smart_charging_optimization_engine.storage.base import StateRepository


class RepositoryDescriptor(TypedDict):
    backend: str
    state_store_dir: str
    database_url: str


def build_state_repository() -> StateRepository:
    return build_state_repository_from_descriptor(repository_descriptor_from_settings())


def repository_descriptor_from_settings() -> RepositoryDescriptor:
    return {
        "backend": settings.state_repository_backend,
        "state_store_dir": settings.state_store_dir,
        "database_url": settings.database_url,
    }


def build_state_repository_from_descriptor(descriptor: RepositoryDescriptor) -> StateRepository:
    backend = descriptor["backend"]
    if backend == "file":
        return FileStateRepository(descriptor["state_store_dir"])
    if backend == "sql":
        return SqlAlchemyStateRepository(descriptor["database_url"])
    msg = f"Unsupported state repository backend: {backend}"
    raise ConfigurationError(msg)
