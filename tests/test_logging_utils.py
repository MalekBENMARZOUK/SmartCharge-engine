from __future__ import annotations

import json
import logging
import sys

from smart_charging_optimization_engine.logging_utils import JsonLogFormatter, configure_logging


def test_json_log_formatter_includes_extra_fields_and_exception() -> None:
    formatter = JsonLogFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        exc_info = sys.exc_info()
        record = logging.getLogger("test.logger").makeRecord(
            "test.logger",
            logging.ERROR,
            __file__,
            10,
            "request failed",
            args=(),
            exc_info=exc_info,
            extra={"request_id": "req-1", "status_code": 500},
        )

    payload = json.loads(formatter.format(record))

    assert payload["logger"] == "test.logger"
    assert payload["message"] == "request failed"
    assert payload["fields"]["request_id"] == "req-1"
    assert payload["fields"]["status_code"] == 500
    assert "RuntimeError: boom" in payload["exception"]


def test_configure_logging_updates_existing_handler_formatter() -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    handler = logging.StreamHandler()
    root_logger.handlers = [handler]
    try:
        configure_logging("DEBUG", "json")

        assert root_logger.level == logging.DEBUG
        assert isinstance(root_logger.handlers[0].formatter, JsonLogFormatter)
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
