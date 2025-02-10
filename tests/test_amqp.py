from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from smart_charging_optimization_engine.messaging.amqp import AmqpTelemetryConsumer
from smart_charging_optimization_engine.services.telemetry_ingestion import (
    TelemetryIngestionService,
)

if TYPE_CHECKING:
    import pytest

    from smart_charging_optimization_engine.storage.base import StateRepository


class _FakeQueueIterator:
    def __init__(self, messages: list[object]) -> None:
        self._messages = messages

    async def __aenter__(self) -> _FakeQueueIterator:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def __aiter__(self) -> _FakeQueueIterator:
        self._index = 0
        return self

    async def __anext__(self) -> object:
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        message = self._messages[self._index]
        self._index += 1
        return message


class _FakeQueue:
    def __init__(self, messages: list[object]) -> None:
        self._messages = messages

    def iterator(self) -> _FakeQueueIterator:
        return _FakeQueueIterator(self._messages)


class _FakeChannel:
    def __init__(self, messages: list[object]) -> None:
        self._messages = messages

    async def set_qos(self, prefetch_count: int = 0) -> None:
        return None

    async def declare_queue(self, queue_name: str, durable: bool = True) -> _FakeQueue:
        return _FakeQueue(self._messages)


class _FakeConnection:
    def __init__(self, messages: list[object]) -> None:
        self._messages = messages

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def channel(self) -> _FakeChannel:
        return _FakeChannel(self._messages)


def test_amqp_consumer_continues_after_message_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    consumer = AmqpTelemetryConsumer(
        TelemetryIngestionService(cast("StateRepository", object())),
        "amqp://broker",
        "queue",
    )
    messages = [object(), object()]
    outcomes = iter([RuntimeError("bad message"), None])

    async def fake_connect_robust(url: str) -> _FakeConnection:
        return _FakeConnection(messages)

    async def fake_handle_message(message: object) -> None:
        outcome = next(outcomes)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(
        "smart_charging_optimization_engine.messaging.amqp.aio_pika.connect_robust",
        fake_connect_robust,
    )
    monkeypatch.setattr(consumer, "_handle_message", fake_handle_message)

    processed = asyncio.run(consumer.consume())

    assert processed == 1
