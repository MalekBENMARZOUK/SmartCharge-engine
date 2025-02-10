from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from typing import TYPE_CHECKING

import aio_pika

from smart_charging_optimization_engine.metrics import metrics

if TYPE_CHECKING:
    from aio_pika.abc import AbstractIncomingMessage

    from smart_charging_optimization_engine.services.telemetry_ingestion import (
        TelemetryIngestionService,
    )

logger = logging.getLogger(__name__)


class AmqpTelemetryConsumer:
    def __init__(
        self,
        service: TelemetryIngestionService,
        broker_url: str,
        queue_name: str,
    ) -> None:
        self._service = service
        self._broker_url = broker_url
        self._queue_name = queue_name
        self._shutdown_event: asyncio.Event = asyncio.Event()

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def consume(self, max_messages: int | None = None) -> int:
        logger.info(
            "Connecting to telemetry broker %s queue=%s",
            self._broker_url,
            self._queue_name,
        )
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self._shutdown_event.set)
        try:
            connection = await aio_pika.connect_robust(self._broker_url)
        except Exception:
            logger.exception(
                "Failed to connect to telemetry broker %s",
                self._broker_url,
            )
            raise
        processed_messages = 0
        async with connection:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=10)
            queue = await channel.declare_queue(self._queue_name, durable=True)
            async with queue.iterator() as queue_iterator:
                async for message in queue_iterator:
                    try:
                        await self._handle_message(message)
                        processed_messages += 1
                        metrics.increment("amqp_messages_processed_total", queue=self._queue_name)
                    except Exception:
                        logger.exception(
                            "Failed to process telemetry message from %s",
                            self._queue_name,
                        )
                        metrics.increment("amqp_message_failures_total", queue=self._queue_name)
                    if max_messages is not None and processed_messages >= max_messages:
                        break
                    if self._shutdown_event.is_set():
                        logger.info(
                            "Shutdown requested, stopping consumer for %s after %d message(s)",
                            self._queue_name,
                            processed_messages,
                        )
                        break
        logger.info(
            "Finished consuming from %s, processed %d message(s)",
            self._queue_name,
            processed_messages,
        )
        return processed_messages

    async def _handle_message(self, message: AbstractIncomingMessage) -> None:
        async with message.process(requeue=False):
            await asyncio.to_thread(self._service.ingest_message_body, message.body)
